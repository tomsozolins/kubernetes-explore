"""Unit tests for bin/kubeconfig-generator — the admin-side tool that mints a
temporary ai-agent token and writes the pinned kubeconfig. Every kubectl
round-trip is mocked; render tests write to a temp dir by patching the
module's KUBECONFIG constant."""
import os
import subprocess
from unittest import mock

import pytest

from tests import load_script

gen = load_script("plugins/kubernetes-explore/bin/kubeconfig-generator")


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_kubectl_returns_stdout():
    with mock.patch.object(gen.subprocess, "run", return_value=completed(stdout="ok\n")):
        assert gen.kubectl("get", "sa") == "ok\n"


def test_kubectl_failure_dies_with_stderr():
    with mock.patch.object(
            gen.subprocess, "run",
            return_value=completed(returncode=1, stderr="boom")), \
            pytest.raises(SystemExit) as excinfo:
        gen.kubectl("get", "sa")
    assert "boom" in str(excinfo.value.code)


def test_get_cluster_details_reads_current_context():
    with mock.patch.object(gen, "kubectl", side_effect=["name\n", "server\n", "ca\n"]):
        assert gen.get_cluster_details() == ("name", "server", "ca")


def test_get_cluster_details_missing_field_dies():
    with mock.patch.object(gen, "kubectl", side_effect=["name\n", "", "ca\n"]), \
            pytest.raises(SystemExit):
        gen.get_cluster_details()


def patch_can_i(answers):
    return mock.patch.object(
        gen.subprocess, "run",
        side_effect=[completed(stdout=a) for a in answers])


def test_rbac_all_denied_passes():
    with patch_can_i(["no\n"] * len(gen.WRITE_VERBS)):
        gen.assert_readonly_rbac()


def test_rbac_any_write_verb_refuses():
    with patch_can_i(["no\n", "yes\n"]), pytest.raises(SystemExit) as excinfo:
        gen.assert_readonly_rbac()
    assert "not read-only" in str(excinfo.value.code)


def test_rbac_unclear_answer_fails_closed():
    # Neither yes nor no (impersonation forbidden, cluster unreachable):
    # refuse to mint rather than trust it.
    with patch_can_i(["error: forbidden\n"]), pytest.raises(SystemExit):
        gen.assert_readonly_rbac()


def test_issue_temporary_token_returns_token():
    with mock.patch.object(gen, "kubectl", return_value="tok\n"):
        assert gen.issue_temporary_token() == "tok"


def test_issue_temporary_empty_token_dies():
    with mock.patch.object(gen, "kubectl", return_value=""), pytest.raises(SystemExit):
        gen.issue_temporary_token()


def render(path):
    with mock.patch.object(gen, "KUBECONFIG", str(path)):
        gen.render_kubeconfig("cluster", "https://api", "Y2E=", "tok")


def test_render_writes_config_with_0600(tmp_path):
    path = tmp_path / "kube" / "ai-agent.kubeconfig"
    render(path)
    content = path.read_text()
    for needle in ("server: https://api", "token: tok",
                   "certificate-authority-data: Y2E=",
                   "current-context: ai-agent@cluster"):
        assert needle in content
    assert path.stat().st_mode & 0o777 == 0o600


def test_render_overwrites_existing_file(tmp_path):
    path = tmp_path / "ai-agent.kubeconfig"
    path.write_text("old")
    render(path)
    assert "token: tok" in path.read_text()


def test_render_refuses_symlink_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("")
    link = tmp_path / "ai-agent.kubeconfig"
    os.symlink(target, link)
    with pytest.raises(SystemExit):
        render(link)
    assert target.read_text() == ""  # token never reached the target


def test_main_rejects_arguments():
    with mock.patch.object(gen.sys, "argv", ["kubeconfig-generator", "--force"]), \
            pytest.raises(SystemExit) as excinfo:
        gen.main()
    assert "takes no arguments" in str(excinfo.value.code)
