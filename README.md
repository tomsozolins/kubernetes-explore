# kubernetes-explore

A Claude Code plugin for **read-only** inspection and debugging of a Kubernetes /
EKS cluster — pods, deployments, services, events, logs, nodes, resource usage,
and Prometheus/Thanos metrics.

All cluster access is funneled through the `kubectl-readonly` wrapper (bare
`kubectl`/`k` is blocked by a PreToolUse hook). The wrapper pins a single
kubeconfig — `~/.kube/ai-agent.kubeconfig` — that authenticates as the
read-only `ai-agent` ServiceAccount with a short-lived, expiring token, and it
refuses to run if that token is missing, non-expiring, the wrong identity, or
carries any write verb in its RBAC.

## Install

The repo doubles as its own plugin marketplace, so install it straight from
git. In Claude Code, register the marketplace, then install the plugin from it:

```text
/plugin marketplace add https://github.com/tomsozolins/kubernetes-explore.git
/plugin install kubernetes-explore@kubernetes-explore
```

The `@kubernetes-explore` suffix names the marketplace; the leading
`kubernetes-explore` names the plugin (they coincide here). After installing,
configure the sandbox (below) and mint the read-only kubeconfig with
`kubeconfig-generator` before the skills can reach a cluster.

## Update

New versions land as commits that bump `version` in
`.claude-plugin/plugin.json`. To pull the latest, refresh the marketplace and
update the plugin from the `/plugin` menu:

```text
/plugin marketplace update kubernetes-explore
/plugin update kubernetes-explore@kubernetes-explore
```

The first command re-fetches the catalog from git; the second upgrades the
installed plugin to the version the refreshed catalog advertises.

## ⚠️ Configure sandbox mode — strongly recommended

This plugin's read-only guarantee comes from the wrapper + RBAC. The OS-level
**sandbox** is the second layer that contains everything *else* the Bash tool
might do — reading your credential stores, escaping to bare `kubectl` via an
unsandboxed retry, or writing outside the working tree. **Run this plugin with
Claude Code's sandbox enabled.** Without it, a single mistaken or
prompt-injected command has a far larger blast radius.

Sandboxing is configured under the `sandbox` key of a Claude Code
`settings.json` — either user-level (`~/.claude/settings.json`) or
project-level (`.claude/settings.json`). Enable it and add the mandatory
settings below. See the
[sandboxing docs](https://code.claude.com/docs/en/sandboxing) for the full
reference.

### Mandatory settings

```json
{
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": false,
    "allowUnsandboxedCommands": false,
    "excludedCommands": ["kubeconfig-generator"],
    "filesystem": {
      "denyRead": ["~/.aws", "~/.kube"],
      "allowRead": ["~/.kube/ai-agent.kubeconfig"]
    }
  }
}
```

#### `autoAllowBashIfSandboxed: false`

Keeps every Bash command behind the normal permission prompt even when it runs
sandboxed. The sandbox bounds *what a command can touch*; it does not judge
*whether the command should run at all*. Leaving this `false` means you still
approve each invocation, so a sandbox-safe-but-wrong command (e.g. one that
deletes files inside the working tree, which the sandbox permits) doesn't slip
through unseen.

#### `allowUnsandboxedCommands: false`

Turns on strict sandbox mode: the `dangerouslyDisableSandbox` escape hatch is
ignored entirely. A command that hits a sandbox boundary **fails** instead of
being silently retried outside the sandbox. This is what makes the boundary
real — without it, any command blocked by the sandbox could fall back to
running unsandboxed, defeating the whole point. Every command must run
sandboxed or be explicitly listed in `excludedCommands`.

#### `excludedCommands: ["kubeconfig-generator"]`

`kubeconfig-generator` is the one command that legitimately *cannot* be
sandboxed: it mints a fresh temporary token for the `ai-agent` ServiceAccount,
which requires reading your **admin** `~/.kube/config` — a path `denyRead`
blocks by design — and writing `~/.kube/ai-agent.kubeconfig`. Listing it here
lets it run outside the sandbox while still behind a permission prompt (because
`autoAllowBashIfSandboxed` is `false`). Excluding this single command is far safer
than `allowRead`-ing the admin config globally, which would let *any* sandboxed
command read and exfiltrate it. The pattern has no trailing `*` because
`kubeconfig-generator` takes no arguments — it's always invoked bare, so a
`kubeconfig-generator *` pattern would never match and the command would fall
back into the sandbox and fail.

#### `filesystem.denyRead: ["~/.aws", "~/.kube"]`

Sandbox reads default to allow-everything, which still exposes credential
stores. Denying `~/.aws` (cloud credentials) and `~/.kube` (your admin
kubeconfig and contexts) ensures that even a sandboxed command can't read your
real cluster-admin or cloud creds. This is what forces all cluster access
through the scoped, read-only token instead of the admin kubeconfig.

#### `filesystem.allowRead: ["~/.kube/ai-agent.kubeconfig"]`

Re-allows the one file under the otherwise-denied `~/.kube` that the wrapper
actually needs: the pinned read-only kubeconfig. It holds only a short-lived
token for the read-only `ai-agent` ServiceAccount — not admin credentials — so
exposing it to sandboxed reads is safe. The rest of `~/.kube` (including the
admin config) stays denied. Without this single re-allow, `kubectl-readonly`
can't read its own kubeconfig and the plugin can't talk to the cluster at all.

## How the pieces fit

- `bin/kubectl-readonly` — the choke point; validates the pinned kubeconfig
  (live, expiring, correct identity, read-only RBAC) on every call, then execs
  `kubectl`.
- `bin/kubeconfig-generator` — mints the temporary `ai-agent` token and writes
  `~/.kube/ai-agent.kubeconfig`. Run with admin creds, out of band, when the
  token has expired. Must be excluded from the sandbox (see above).
- `hooks/deny-kubectl.py` — blocks bare `kubectl`/`k` so all access goes
  through the wrapper.
- `hooks/block-context-mismatch.py` — runs outside the sandbox, reads your
  current `~/.kube/config` context, and denies the call if the pinned
  kubeconfig points at a different cluster than the one you've selected.
- `skills/kubectl`, `skills/metrics`, `skills/logs`, `skills/events` — the
  user-facing skills. `kubectl` runs inline and is reserved for targeted,
  bounded probes; `metrics`, `logs`, and `events` are `context: fork` skills,
  so every invocation runs in the matching subagent below and only a summary
  reaches the caller. `skills/events/references/` holds the query patterns
  and the event-reason catalog, grounded in kubernetes.io.
- `agents/metrics`, `agents/logs`, `agents/events` —
  sonnet-pinned subagents that run the noisy discovery and query churn in
  their own context; each preloads the `kubectl` skill plus its own.

## Tests

`tests/test_hooks.py` covers the command-matching logic both hooks use to
decide what they gate on (quoting, env-assignment prefixes, redirect targets,
substitutions, multi-line commands). Run with the same interpreter the hooks
use — no third-party dependencies:

```sh
python3 tests/test_hooks.py
```
