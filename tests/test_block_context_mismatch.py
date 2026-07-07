"""Unit tests for hooks/block-context-mismatch.py — the PreToolUse(Bash) hook
that denies a kubectl-readonly call when the pinned read-only kubeconfig
points at a different cluster than the user's current context."""
import json
import subprocess
from unittest import mock

import pytest

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


@pytest.mark.parametrize("command,expected", KUBECTL_READONLY_CASES)
def test_kubectl_readonly_matcher(command, expected):
    assert hook.command_invokes(command, {"kubectl-readonly"}) == expected


@pytest.mark.parametrize(
    "command", [c for c, _ in BARE_KUBECTL_CASES + KUBECTL_READONLY_CASES])
def test_both_copies_agree(command):
    """Each hook is self-contained, so both carry their own copy of
    command_invokes. Assert the copies agree, so a fix to one that isn't
    mirrored in the other fails here."""
    names = {"kubectl", "k", "kubectl-readonly"}
    assert (deny_kubectl.command_invokes(command, names)
            == hook.command_invokes(command, names))


def _run(returncode=0, stdout=""):
    proc = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")
    return mock.patch.object(hook.subprocess, "run", return_value=proc)


def test_context_info_name_and_server():
    with _run(stdout="ctx-name\nhttps://api.example\n"):
        assert hook.context_info([], {}) == ("ctx-name", "https://api.example")


def test_context_info_empty_name_keeps_server():
    # A context with no name must not let strip() shift the server line
    # into the name slot.
    with _run(stdout="\nhttps://api.example\n"):
        assert hook.context_info([], {}) == ("", "https://api.example")


def test_context_info_kubectl_failure():
    with _run(returncode=1):
        assert hook.context_info([], {}) == ("", "")


def test_context_info_kubectl_missing():
    with mock.patch.object(hook.subprocess, "run", side_effect=OSError):
        assert hook.context_info([], {}) == ("", "")


def test_describe_with_name():
    assert hook._describe("ctx", "https://api") == "  context: ctx\n  server:  https://api"


def test_describe_without_name():
    assert hook._describe("", "https://api") == "  https://api"


# context_info is patched per case: the first call reads the current context,
# the second the pinned kubeconfig.
@pytest.mark.parametrize("side_effect", [
    [("ctx", "https://api.example")] * 2,   # match
    [("", "")],                             # no current context
    [("ctx", "https://api.example"), ("", "")],  # unreadable pinned
], ids=["match", "no-current-context", "unreadable-pinned"])
def test_assert_context_matches_is_silent(side_effect, capsys):
    with mock.patch.object(hook, "context_info", side_effect=side_effect):
        hook.assert_context_matches()
    assert capsys.readouterr().out == ""


def test_mismatch_denies_with_both_identities(capsys):
    with mock.patch.object(hook, "context_info", side_effect=[
        ("arn:aws:eks:eu:1:cluster/b", "https://b.example"),
        ("ai-agent@a", "https://a.example"),
    ]), pytest.raises(SystemExit) as excinfo:
        hook.assert_context_matches()
    assert excinfo.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    # The context name leads; the server URL is carried alongside.
    assert "context: arn:aws:eks:eu:1:cluster/b" in reason
    assert "server:  https://b.example" in reason
    assert "context: ai-agent@a" in reason
    assert "server:  https://a.example" in reason


def test_bash_command_extracts_command():
    payload = {"tool_name": "Bash", "tool_input": {"command": "kubectl-readonly get"}}
    assert hook.bash_command(payload) == "kubectl-readonly get"


@pytest.mark.parametrize("payload", [
    None, {}, {"tool_name": "Bash"},
    {"tool_name": "Bash", "tool_input": {"command": 5}},
])
def test_bash_command_malformed_payloads(payload):
    assert hook.bash_command(payload) == ""
