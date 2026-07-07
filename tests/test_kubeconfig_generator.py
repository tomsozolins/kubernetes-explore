#!/usr/bin/env python3
#
# Unit tests for bin/kubeconfig-generator — the admin-side tool that mints a
# temporary ai-agent token and writes the pinned kubeconfig. Every kubectl
# round-trip is mocked; render tests write to a temp dir by patching the
# module's KUBECONFIG constant. Run the whole suite with:
#
#   python3 -m unittest discover -s tests
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import load_script

gen = load_script("bin/kubeconfig-generator")


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class TestKubectl(unittest.TestCase):
    def test_returns_stdout(self):
        with mock.patch.object(gen.subprocess, "run", return_value=completed(stdout="ok\n")):
            self.assertEqual(gen.kubectl("get", "sa"), "ok\n")

    def test_failure_dies_with_stderr(self):
        with mock.patch.object(
                gen.subprocess, "run",
                return_value=completed(returncode=1, stderr="boom")), \
                self.assertRaises(SystemExit) as cm:
            gen.kubectl("get", "sa")
        self.assertIn("boom", str(cm.exception.code))


class TestGetClusterDetails(unittest.TestCase):
    def test_reads_current_context(self):
        with mock.patch.object(gen, "kubectl", side_effect=["name\n", "server\n", "ca\n"]):
            self.assertEqual(gen.get_cluster_details(), ("name", "server", "ca"))

    def test_missing_field_dies(self):
        with mock.patch.object(gen, "kubectl", side_effect=["name\n", "", "ca\n"]), \
                self.assertRaises(SystemExit):
            gen.get_cluster_details()


class TestAssertReadonlyRbac(unittest.TestCase):
    def _can_i(self, answers):
        return mock.patch.object(
            gen.subprocess, "run",
            side_effect=[completed(stdout=a) for a in answers])

    def test_all_denied_passes(self):
        with self._can_i(["no\n"] * len(gen.WRITE_VERBS)):
            gen.assert_readonly_rbac()

    def test_any_write_verb_refuses(self):
        with self._can_i(["no\n", "yes\n"]), self.assertRaises(SystemExit) as cm:
            gen.assert_readonly_rbac()
        self.assertIn("not read-only", str(cm.exception.code))

    def test_unclear_answer_fails_closed(self):
        # Neither yes nor no (impersonation forbidden, cluster unreachable):
        # refuse to mint rather than trust it.
        with self._can_i(["error: forbidden\n"]), self.assertRaises(SystemExit):
            gen.assert_readonly_rbac()


class TestIssueTemporaryToken(unittest.TestCase):
    def test_returns_token(self):
        with mock.patch.object(gen, "kubectl", return_value="tok\n"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(gen.issue_temporary_token(), "tok")

    def test_empty_token_dies(self):
        with mock.patch.object(gen, "kubectl", return_value=""), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            gen.issue_temporary_token()


class TestRenderKubeconfig(unittest.TestCase):
    def _render(self, path):
        with mock.patch.object(gen, "KUBECONFIG", str(path)), \
                redirect_stdout(io.StringIO()):
            gen.render_kubeconfig("cluster", "https://api", "Y2E=", "tok")

    def test_writes_config_with_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kube" / "ai-agent.kubeconfig"
            self._render(path)
            content = path.read_text()
            for needle in ("server: https://api", "token: tok",
                           "certificate-authority-data: Y2E=",
                           "current-context: ai-agent@cluster"):
                self.assertIn(needle, content)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ai-agent.kubeconfig"
            path.write_text("old")
            self._render(path)
            self.assertIn("token: tok", path.read_text())

    def test_refuses_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("")
            link = Path(tmp) / "ai-agent.kubeconfig"
            os.symlink(target, link)
            with self.assertRaises(SystemExit):
                self._render(link)
            self.assertEqual(target.read_text(), "")  # token never reached the target


class TestMain(unittest.TestCase):
    def test_rejects_arguments(self):
        with mock.patch.object(gen.sys, "argv", ["kubeconfig-generator", "--force"]), \
                self.assertRaises(SystemExit) as cm:
            gen.main()
        self.assertIn("takes no arguments", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
