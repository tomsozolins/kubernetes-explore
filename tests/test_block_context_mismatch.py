#!/usr/bin/env python3
#
# Unit tests for hooks/block-context-mismatch.py — the PreToolUse(Bash) hook
# that denies a kubectl-readonly call when the pinned read-only kubeconfig
# points at a different cluster than the user's current context. Run the whole
# suite with:
#
#   python3 -m unittest discover -s tests
import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests import load_script
from tests.test_deny_kubectl import BARE_KUBECTL_CASES, deny_kubectl

hook = load_script("hooks/block-context-mismatch.py")

# (command, whether it invokes kubectl-readonly at a command position)
KUBECTL_READONLY_CASES = [
    ("kubectl-readonly get pods", True),
    ("kubectl get pods", False),
    ("KUBECONFIG=/x kubectl-readonly get", True),
    ('kube"ctl-readonly" get', True),
    ("echo `kubectl-readonly get`", True),
]


class TestCommandInvokes(unittest.TestCase):
    def test_kubectl_readonly_matcher(self):
        for command, expected in KUBECTL_READONLY_CASES:
            with self.subTest(command=command):
                self.assertEqual(
                    hook.command_invokes(command, {"kubectl-readonly"}), expected)

    def test_both_copies_agree(self):
        """Each hook is self-contained, so both carry their own copy of
        command_invokes. Assert the copies agree, so a fix to one that isn't
        mirrored in the other fails here."""
        names = {"kubectl", "k", "kubectl-readonly"}
        for command, _ in BARE_KUBECTL_CASES + KUBECTL_READONLY_CASES:
            with self.subTest(command=command):
                self.assertEqual(
                    deny_kubectl.command_invokes(command, names),
                    hook.command_invokes(command, names))


class TestContextInfo(unittest.TestCase):
    def _run(self, returncode=0, stdout=""):
        proc = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
        return mock.patch.object(
            hook.subprocess, "run", return_value=proc)

    def test_name_and_server(self):
        with self._run(stdout="ctx-name\nhttps://api.example\n"):
            self.assertEqual(
                hook.context_info([], {}), ("ctx-name", "https://api.example"))

    def test_empty_name_keeps_server(self):
        # A context with no name must not let strip() shift the server line
        # into the name slot.
        with self._run(stdout="\nhttps://api.example\n"):
            self.assertEqual(hook.context_info([], {}), ("", "https://api.example"))

    def test_kubectl_failure(self):
        with self._run(returncode=1):
            self.assertEqual(hook.context_info([], {}), ("", ""))

    def test_kubectl_missing(self):
        with mock.patch.object(hook.subprocess, "run", side_effect=OSError):
            self.assertEqual(hook.context_info([], {}), ("", ""))


class TestDescribe(unittest.TestCase):
    def test_with_name(self):
        self.assertEqual(
            hook._describe("ctx", "https://api"),
            "  context: ctx\n  server:  https://api")

    def test_without_name(self):
        self.assertEqual(hook._describe("", "https://api"), "  https://api")


class TestAssertContextMatches(unittest.TestCase):
    """context_info is patched per case: the first call reads the current
    context, the second the pinned kubeconfig."""

    def _call(self, side_effect):
        out = io.StringIO()
        with mock.patch.object(hook, "context_info", side_effect=side_effect):
            with redirect_stdout(out):
                hook.assert_context_matches()
        return out.getvalue()

    def test_match_is_silent(self):
        info = ("ctx", "https://api.example")
        self.assertEqual(self._call([info, info]), "")

    def test_no_current_context_is_silent(self):
        self.assertEqual(self._call([("", "")]), "")

    def test_unreadable_pinned_is_silent(self):
        self.assertEqual(self._call([("ctx", "https://api.example"), ("", "")]), "")

    def test_mismatch_denies_with_both_identities(self):
        out = io.StringIO()
        with mock.patch.object(hook, "context_info", side_effect=[
            ("arn:aws:eks:eu:1:cluster/b", "https://b.example"),
            ("ai-agent@a", "https://a.example"),
        ]):
            with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                hook.assert_context_matches()
        self.assertEqual(cm.exception.code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(
            payload["hookSpecificOutput"]["permissionDecision"], "deny")
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        # The context name leads; the server URL is carried alongside.
        self.assertIn("context: arn:aws:eks:eu:1:cluster/b", reason)
        self.assertIn("server:  https://b.example", reason)
        self.assertIn("context: ai-agent@a", reason)
        self.assertIn("server:  https://a.example", reason)


class TestBashCommand(unittest.TestCase):
    def test_extracts_command(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "kubectl-readonly get"}}
        self.assertEqual(hook.bash_command(payload), "kubectl-readonly get")

    def test_malformed_payloads(self):
        for payload in [None, {}, {"tool_name": "Bash"},
                        {"tool_name": "Bash", "tool_input": {"command": 5}}]:
            with self.subTest(payload=payload):
                self.assertEqual(hook.bash_command(payload), "")


if __name__ == "__main__":
    unittest.main()
