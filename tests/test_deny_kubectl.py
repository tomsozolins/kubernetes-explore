"""Unit tests for hooks/deny-kubectl.py — the PreToolUse(Bash) hook that denies
bare 'kubectl' (and the 'k' alias)."""
import pytest

from tests import load_script

deny_kubectl = load_script("plugins/kubernetes-explore/hooks/deny-kubectl.py")

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


@pytest.mark.parametrize("command,expected", BARE_KUBECTL_CASES)
def test_bare_kubectl_matcher(command, expected):
    assert deny_kubectl.command_invokes(command, {"kubectl", "k"}) == expected


@pytest.mark.parametrize("token,expected", [
    ("KUBECONFIG=/x", True),
    ("FOO=bar", True),
    ("_underscore=1", True),
    ("kubectl", False),
    ("/usr/bin/kubectl", False),
    ("=novalue", False),
    ("1abc=x", False),                             # name can't start with a digit
])
def test_is_env_assignment(token, expected):
    assert deny_kubectl._is_env_assignment(token) == expected


def test_bash_command_extracts_command():
    payload = {"tool_name": "Bash", "tool_input": {"command": "kubectl get"}}
    assert deny_kubectl.bash_command(payload) == "kubectl get"


def test_bash_command_non_bash_tool():
    assert deny_kubectl.bash_command({"tool_name": "Read", "tool_input": {}}) == ""


@pytest.mark.parametrize("payload", [
    None, {}, {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": {"command": 5}},
])
def test_bash_command_malformed_payloads(payload):
    assert deny_kubectl.bash_command(payload) == ""
