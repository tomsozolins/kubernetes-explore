#!/usr/bin/env python3
#
# PreToolUse(Bash) hook: deny bare 'kubectl' (and the 'k' alias). Only
# 'kubectl-readonly' may touch the cluster.
#
# Matching is done HERE in Python, not via a hooks.json `if` condition: Claude
# Code's command-prefix matcher fails closed on commands with a variable in a
# redirect target (firing on unrelated commands like `helm ... > "$TMPDIR/x"`)
# and fails open on `KUBECONFIG=x kubectl ...` (letting a real bypass through).
# We inspect only the command position of each sub-command, so a redirect
# target never affects the decision.
import json
import os
import shlex
import sys
from collections.abc import Container

# Tokens consisting solely of these characters are shell control operators
# (`| || && ; & ( )`) or redirects (`< > >> 2>` …). shlex with
# punctuation_chars surfaces each as its own token.
_OPERATOR_CHARS = frozenset("();<>|&")

DENY_REASON = (
    "Bare 'kubectl' (and the 'k' alias) is blocked in the kubernetes-explore "
    "plugin. Use 'kubectl-readonly' — it pins the read-only ai-agent "
    "ServiceAccount kubeconfig. Do not invoke kubectl by path or with "
    "KUBECONFIG=/--kubeconfig/--context/--as."
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
    matched on the basename so `/usr/local/bin/kubectl` matches `kubectl` while
    `kubectl-readonly` does not. shlex tokenizes quote-aware, so `kube"ctl"` and
    `'kubectl'` resolve to the real word. Only the command position is checked —
    redirect targets, arguments, and heredoc bodies never are — so a variable in
    a redirect (`cmd 2>"$TMPDIR/x"`) can't influence the decision. Newlines and
    backticks aren't operators to shlex, so split on them first; each fragment
    is then a self-contained command line whose `| && ; ( $(` operators shlex
    handles."""
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


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    command = bash_command(payload)
    if command and command_invokes(command, {"kubectl", "k"}):
        print(json.dumps({
            "systemMessage": "⛔ Bare 'kubectl'/'k' — blocked by kubernetes-explore. Use 'kubectl-readonly'.",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": DENY_REASON,
            }
        }))
    sys.exit(0)


if __name__ == "__main__":
    main()
