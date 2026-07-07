#!/usr/bin/env python3
#
# PreToolUse(Bash) hook: deny a 'kubectl-readonly' call when its pinned read-only
# kubeconfig points at a different cluster than the user's CURRENT context.
#
# Why this lives in a hook and not in the kubectl-readonly wrapper: a still-live
# token for a previously-selected cluster passes every check the wrapper can
# make, so after a `kubectl config use-context` switch the wrapper would
# otherwise query the OLD cluster. Detecting that needs the user's current
# context from ~/.kube/config — which Claude Code's command sandbox denies to the
# wrapper (admin creds; only ai-agent.kubeconfig is re-allowed), so the wrapper's
# own in-process check failed open (silently skipped) on every call. Hooks run
# OUTSIDE that sandbox, so this one can read ~/.kube/config and compare for real.
#
# Bare-kubectl blocking is the separate concern of block-bare-kubectl.py.
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Container

# Tokens consisting solely of these characters are shell control operators
# (`| || && ; & ( )`) or redirects (`< > >> 2>` …). shlex with
# punctuation_chars surfaces each as its own token.
_OPERATOR_CHARS = frozenset("();<>|&")

KUBECONFIG = os.path.join(os.path.expanduser("~"), ".kube", "ai-agent.kubeconfig")
SA, NS = "ai-agent", "default"
PROVISION_HINT = (
    "Mint a fresh temporary token and write the kubeconfig (admin credentials):\n"
    "  kubeconfig-generator"
)


def _is_env_assignment(token: str) -> bool:
    """A leading `VAR=value` shell assignment, which precedes the command word
    (so `KUBECONFIG=/x kubectl ...` still resolves to `kubectl`)."""
    name, sep, _ = token.partition("=")
    return sep == "=" and name.isidentifier()


def _find_heredocs(line: str) -> list[tuple[str, bool]]:
    """(delimiter, strips_tabs) for each heredoc opened on this command line.
    shlex tokenizes quote-aware, so a `<<` inside a quoted word never counts,
    `<<'EOF'` yields the bare delimiter, and a `<<<` herestring is a single
    operator token that opens no body."""
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    found = []
    for op, word in zip(tokens, tokens[1:]):
        if op != "<<" or not word or _OPERATOR_CHARS.issuperset(word):
            continue
        delim = word[1:] if word.startswith("-") else word  # <<-EOF lexes as '-EOF'
        if delim:
            found.append((delim, word.startswith("-")))
    return found


def _strip_heredoc_bodies(command: str) -> str:
    """Heredoc bodies are stdin data, not shell commands, but the line-based
    scan in command_invokes treats each line as a command line — a commit
    message like `docs(kubectl): ...` inside a heredoc parses as `(` opening a
    command position with `kubectl` in it. Drop every line after a `<<DELIM`
    opener up to and including its terminator; opener lines themselves are
    kept and scanned."""
    kept, open_delims = [], []
    for line in command.split("\n"):
        if open_delims:
            delim, strips_tabs = open_delims[0]
            if (line.lstrip("\t") if strips_tabs else line) == delim:
                open_delims.pop(0)
            continue
        open_delims.extend(_find_heredocs(line))
        kept.append(line)
    return "\n".join(kept)


def command_invokes(command: str, names: Container[str]) -> bool:
    """True when any sub-command of `command` runs one of `names` as its binary,
    matched on the basename so `/usr/local/bin/kubectl-readonly` matches
    `kubectl-readonly` (basenames compared whole, not by prefix). shlex
    tokenizes quote-aware, so `kube"ctl-readonly"` resolves to the real word.
    Only the command position is checked — redirect targets, arguments, and
    heredoc bodies never are — so a variable in a redirect (`cmd 2>"$TMPDIR/x"`)
    can't influence the decision. Newlines and backticks aren't operators to
    shlex, so split on them first; each fragment is then a self-contained
    command line whose `| && ; ( $(` operators shlex handles."""
    for fragment in _strip_heredoc_bodies(command).replace("`", "\n").split("\n"):
        try:
            lexer = shlex.shlex(fragment, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            continue
        at_command = True
        skip_target = False
        for token in tokens:
            if skip_target:
                skip_target = False
            elif not token:
                continue
            elif _OPERATOR_CHARS.issuperset(token):
                if "<" in token or ">" in token:
                    skip_target = True  # next token is a redirect target
                else:
                    at_command = True  # operator opens a new command position
            elif not at_command:
                continue
            elif _is_env_assignment(token):
                continue
            elif os.path.basename(token) in names:
                return True
            else:
                at_command = False
    return False


def bash_command(stdin_json: object) -> str:
    """Extract the Bash command string from a PreToolUse hook payload, or '' if
    this isn't a Bash call or carries no command."""
    if not isinstance(stdin_json, dict) or stdin_json.get("tool_name") != "Bash":
        return ""
    tool_input = stdin_json.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    command = tool_input.get("command")
    return command if isinstance(command, str) else ""


def context_info(extra_args, env):
    """(context_name, server_url) of the current context for the given kubectl
    args, or ('', '') on any failure — missing file, no current context, or
    kubectl not on PATH. The server URL is what actually decides a mismatch (EKS
    reissues opaque `<hash>.gr7.<region>.eks.amazonaws.com` hostnames per
    cluster); the context name is carried alongside only to make the message
    legible, since it's the human-readable label the user set with
    `use-context`. Callers treat '' server as 'nothing to compare', leaving the
    wrapper's own asserts to fire. Name and server are emitted on their own
    lines via jsonpath's `{"\\n"}` literal (neither value contains a newline);
    fields are stripped individually so an empty context name can't shift the
    server past a leading strip()."""
    try:
        proc = subprocess.run(
            ["kubectl", *extra_args, "config", "view", "--minify", "-o",
             'jsonpath={.contexts[0].name}{"\\n"}{.clusters[0].cluster.server}'],
            capture_output=True, text=True, env=env, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if proc.returncode != 0:
        return "", ""
    name, _, server = proc.stdout.partition("\n")
    return name.strip(), server.strip()


def _describe(name, server):
    """One indented line per field so a mismatch shows the legible context name
    over the opaque server URL, or just the server when the name is unknown."""
    return f"  context: {name}\n  server:  {server}" if name else f"  {server}"


def assert_context_matches():
    """Deny when the pinned read-only kubeconfig points at a different cluster
    than the user's current context. Skip silently when there's no current
    context or the pinned file can't be read — those aren't this hook's call to
    block; the wrapper's own checks handle a broken/absent pinned config."""
    current_name, current = context_info([], os.environ)
    if not current:
        return
    pinned_name, pinned = context_info(
        ["--kubeconfig", KUBECONFIG], {**os.environ, "KUBECONFIG": KUBECONFIG})
    if pinned and pinned != current:
        print(json.dumps({
            "systemMessage": (
                "⛔ kubectl-readonly: pinned kubeconfig is for a different "
                "cluster than your current context — blocked."
            ),
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"kubectl-readonly: current context points at\n"
                    f"{_describe(current_name, current)}\n"
                    f"but the pinned read-only kubeconfig is for\n"
                    f"{_describe(pinned_name, pinned)}\n"
                    "re-provision against the current context before reading.\n"
                    f"{PROVISION_HINT}"
                ),
            }
        }))
        sys.exit(0)


# Self-gate here rather than via a hooks.json `if`: Claude Code's command-prefix
# matcher fires on commands it can't parse (e.g. a redirect to "$TMPDIR/x"), so
# an `if` would run the expensive context check on unrelated commands. We match
# the command position ourselves and only proceed for a real kubectl-readonly
# call — keeping the kubectl subprocesses off every other Bash invocation.
def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = bash_command(payload)
    if command and command_invokes(command, {"kubectl-readonly"}):
        assert_context_matches()
    sys.exit(0)


if __name__ == "__main__":
    main()
