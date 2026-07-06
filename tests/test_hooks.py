#!/usr/bin/env python3
#
# Unit tests for the kubernetes-explore PreToolUse(Bash) hooks. Run with the
# same interpreter the hooks use:
#
#   python3 tests/test_hooks.py
#
# Each hook is self-contained (no shared module), so the two carry their own
# copy of command_invokes/bash_command. We load both by file path and assert
# the copies agree, so a fix to one that isn't mirrored in the other fails here.
import importlib.util
import unittest
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def _load(filename: str):
    """Import a hyphen-named hook script by path. The hooks guard their stdin
    handling behind `if __name__ == '__main__'`, so importing only defines the
    functions — nothing reads stdin or exits."""
    path = _HOOKS / filename
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deny_kubectl = _load("deny-kubectl.py")
block_context_mismatch = _load("block-context-mismatch.py")

# (command, whether it invokes a binary in {kubectl, k} at a command position)
BARE_KUBECTL_CASES = [
    ("kubectl get pods", True),
    ("k get pods", True),
    ("kubectl-readonly get pods", False),
    ("KUBECONFIG=/x kubectl get pods", True),
    ('kube"ctl" get pods', True),
    ("'kubectl' get pods", True),
    ("/usr/local/bin/kubectl get", True),
    ('helm template . > "$TMPDIR/out"', False),
    ("echo hi > kubectl", False),                  # redirect target, not a command
    ("echo hi; kubectl get", True),
    ("echo hi && kubectl get", True),
    ("foo | kubectl get", True),
    ("echo $(kubectl get pods)", True),            # $() substitution
    ("echo `kubectl get pods`", True),             # backtick substitution
    ("helm install\nkubectl get", True),           # second line
    ("helm install foo", False),
    (">out kubectl get", True),                    # leading redirect, real command after
    ("make CC=gcc", False),
    ("kubectl-readonly get; kubectl delete", True),  # second sub-command is bare
    # heredoc bodies are stdin data, not commands
    ("cat > msg <<'EOF'\ndocs(kubectl): trim annotations\nEOF\ngit commit -F msg", False),
    ("cat <<EOF\nkubectl get\nEOF\nkubectl get pods", True),   # command after terminator
    ("cat <<-EOF\n\tkubectl get\n\tEOF\necho done", False),    # <<- tab-indented terminator
    ("cat <<A <<B\nkubectl\nA\nkubectl\nB\necho ok", False),   # two heredocs, both bodies skipped
    ("grep foo <<< 'kubectl get'", False),                     # herestring has no body
    ('echo "a << b"\nkubectl get', True),                      # quoted << is not a heredoc
]

KUBECTL_READONLY_CASES = [
    ("kubectl-readonly get pods", True),
    ("kubectl get pods", False),
    ("KUBECONFIG=/x kubectl-readonly get", True),
    ('kube"ctl-readonly" get', True),
    ("echo `kubectl-readonly get`", True),
]

ENV_ASSIGNMENT_CASES = [
    ("KUBECONFIG=/x", True),
    ("FOO=bar", True),
    ("_underscore=1", True),
    ("kubectl", False),
    ("/usr/bin/kubectl", False),
    ("=novalue", False),
    ("1abc=x", False),                             # name can't start with a digit
]


class TestCommandInvokes(unittest.TestCase):
    def test_bare_kubectl_matcher(self):
        for command, expected in BARE_KUBECTL_CASES:
            with self.subTest(command=command):
                self.assertEqual(
                    deny_kubectl.command_invokes(command, {"kubectl", "k"}), expected)

    def test_kubectl_readonly_matcher(self):
        for command, expected in KUBECTL_READONLY_CASES:
            with self.subTest(command=command):
                self.assertEqual(
                    block_context_mismatch.command_invokes(
                        command, {"kubectl-readonly"}), expected)

    def test_both_copies_agree(self):
        names = {"kubectl", "k", "kubectl-readonly"}
        for command, _ in BARE_KUBECTL_CASES + KUBECTL_READONLY_CASES:
            with self.subTest(command=command):
                self.assertEqual(
                    deny_kubectl.command_invokes(command, names),
                    block_context_mismatch.command_invokes(command, names))


class TestEnvAssignment(unittest.TestCase):
    def test_is_env_assignment(self):
        for token, expected in ENV_ASSIGNMENT_CASES:
            with self.subTest(token=token):
                self.assertEqual(deny_kubectl._is_env_assignment(token), expected)


class TestBashCommand(unittest.TestCase):
    def test_extracts_command(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "kubectl get"}}
        self.assertEqual(deny_kubectl.bash_command(payload), "kubectl get")

    def test_non_bash_tool(self):
        self.assertEqual(
            deny_kubectl.bash_command({"tool_name": "Read", "tool_input": {}}), "")

    def test_malformed_payloads(self):
        for payload in [None, {}, {"tool_name": "Bash"},
                        {"tool_name": "Bash", "tool_input": {"command": 5}}]:
            with self.subTest(payload=payload):
                self.assertEqual(deny_kubectl.bash_command(payload), "")


if __name__ == "__main__":
    unittest.main()
