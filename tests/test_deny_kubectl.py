#!/usr/bin/env python3
#
# Unit tests for hooks/deny-kubectl.py — the PreToolUse(Bash) hook that denies
# bare 'kubectl' (and the 'k' alias). Run the whole suite with:
#
#   python3 -m unittest discover -s tests
import unittest

from tests import load_script

deny_kubectl = load_script("hooks/deny-kubectl.py")

# (command, whether it invokes a binary in {kubectl, k} at a command position).
# Also consumed by test_block_context_mismatch.py's agreement test, since both
# hooks carry their own copy of command_invokes.
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
