#!/usr/bin/env python3
#
# Unit tests for bin/kubectl-readonly — the read-only kubectl wrapper that pins
# ~/.kube/ai-agent.kubeconfig and fails closed on identity, expiry, and RBAC.
# Every kubectl round-trip is mocked; nothing here touches a cluster. Run the
# whole suite with:
#
#   python3 -m unittest discover -s tests
import base64
import json
import subprocess
import unittest
from unittest import mock

from tests import load_script

wrapper = load_script("bin/kubectl-readonly")


def jwt(claims: dict[str, object]) -> str:
    """A structurally valid JWT with the given payload claims. Signature is
    irrelevant — the wrapper only decodes the middle segment."""
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{seg}.sig"


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class TestPinnedEnv(unittest.TestCase):
    def test_forces_pinned_kubeconfig(self):
        with mock.patch.dict(wrapper.os.environ, {"KUBECONFIG": "/somewhere/else"}):
            self.assertEqual(wrapper.pinned_env()["KUBECONFIG"], wrapper.KUBECONFIG)


class TestPinnedToken(unittest.TestCase):
    def _view(self, **kwargs):
        return mock.patch.object(
            wrapper.subprocess, "run", return_value=completed(**kwargs))

    def test_extracts_token(self):
        cfg = {"users": [{"user": {"token": "tok"}}]}
        with self._view(stdout=json.dumps(cfg)):
            self.assertEqual(wrapper.pinned_token(), "tok")

    def test_fails_closed(self):
        for case, kwargs in [
            ("kubectl error", {"returncode": 1}),
            ("invalid json", {"stdout": "not-json"}),
            ("no users", {"stdout": "{}"}),
            ("no token", {"stdout": json.dumps({"users": [{"user": {}}]})}),
        ]:
            with self.subTest(case=case), self._view(**kwargs):
                self.assertEqual(wrapper.pinned_token(), "")


class TestAssertTokenExpires(unittest.TestCase):
    def test_expiring_token_passes(self):
        wrapper.assert_token_expires(jwt({"exp": 1}))

    def test_rejects(self):
        for case, token in [
            ("empty token", ""),
            ("not a jwt", "static-secret"),
            ("undecodable claims", "a.!!!.c"),
            ("no exp claim", jwt({"sub": "x"})),
        ]:
            with self.subTest(case=case), self.assertRaises(SystemExit):
                wrapper.assert_token_expires(token)


class TestAssertReadonlyRbac(unittest.TestCase):
    HEADER = "Resources  Non-Resource URLs  Resource Names  Verbs\n"

    def _can_i(self, stdout, returncode=0):
        return mock.patch.object(
            wrapper.subprocess, "run",
            return_value=completed(returncode=returncode, stdout=stdout))

    def test_readonly_grants_pass(self):
        listing = self.HEADER + (
            "selfsubjectaccessreviews.authorization.k8s.io  []  []  [create]\n"
            "pods  []  []  [get list watch]\n"
            "[/healthz]  []  [get]\n"          # non-resource URL grant
        )
        with self._can_i(listing):
            wrapper.assert_readonly_rbac()

    def test_write_verb_is_refused(self):
        for verbs in ("[get delete]", "[*]"):
            listing = self.HEADER + f"pods  []  []  {verbs}\n"
            with self.subTest(verbs=verbs), self._can_i(listing), \
                    self.assertRaises(SystemExit) as cm:
                wrapper.assert_readonly_rbac()
            self.assertIn("write access", str(cm.exception.code))

    def test_enumeration_failure_fails_closed(self):
        with self._can_i("", returncode=1), self.assertRaises(SystemExit):
            wrapper.assert_readonly_rbac()


class TestBlockedFlags(unittest.TestCase):
    def test_redirecting_flags_die_before_any_kubectl(self):
        for argv in (["--kubeconfig", "/x"], ["--context=other"], ["get", "--as=admin"]):
            with self.subTest(argv=argv), \
                    mock.patch.object(wrapper.sys, "argv", ["kubectl-readonly", *argv]), \
                    mock.patch.object(wrapper.subprocess, "run") as run, \
                    self.assertRaises(SystemExit):
                wrapper.main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
