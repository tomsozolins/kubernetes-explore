---
name: kubectl
allowed-tools: Bash(kubectl-readonly:*), Bash(kubeconfig-generator), WebSearch, WebFetch
model: sonnet
description: >-
  Inspect and debug the current kubectl context's Kubernetes / EKS cluster (pods, deployments,
  services, events, logs, nodes, resource usage). Use this skill whenever the
  user wants to look at, troubleshoot, or report on cluster state — "what's
  wrong with the cluster", "why is this pod crashing", "check the deployment",
  "are the nodes healthy", "show me the logs", "what's running in namespace X" —
  even if they don't say "kubectl". Cluster access here goes through the
  read-only `kubectl-readonly` wrapper, NOT bare `kubectl` (which is blocked).
  This skill also owns the reference RBAC manifest
  (`references/ai-agent-rbac.yaml`) that provisions the read-only `ai-agent`
  ServiceAccount — read it before adding or editing that SA's RBAC in a GitOps
  repo, rather than hand-deriving one.
---

# kubectl (read-only cluster access)

This plugin locks cluster access down to a single read-only path. Bare `kubectl`
is blocked by a hook; the only way to reach the cluster is the `kubectl-readonly`
wrapper, which pins `$HOME/.kube/ai-agent.kubeconfig` for the `ai-agent`
ServiceAccount in the `default` namespace (`view` ClusterRole + `eks:nodewatcher`
for node watching).

## Division of labor: targeted probes here, exploration in subagents

This skill is for **targeted, bounded probes** whose small output you need
directly in your context: a specific pod's status, one service list, a single
object's field via `-o jsonpath`, a bounded event listing. Anything
**exploratory or output-heavy** — PromQL queries and metric discovery, log
searching and crash-loop forensics — belongs to the `/metrics` and `/logs`
skills, which fork into the `metrics` / `logs` subagents and
return only a summary. Don't run that churn inline "to save a hop": the
discovery iterations and raw payloads would pollute the invoking context,
which is exactly what the forked skills exist to prevent. If a summary leaves
you needing one specific value, probe for that value directly here — or ask
the forked skill a sharper question.

## Cluster-side prerequisite (the `ai-agent` ServiceAccount)

Everything here assumes the cluster already has a read-only `ai-agent`
ServiceAccount and the RBAC that scopes it. The wrapper and
`kubeconfig-generator` only ever *use* that identity — they never create it.
Without it, token minting fails and there's nothing to authenticate as.

The reference manifest that defines it lives next to this skill:
[`references/ai-agent-rbac.yaml`](references/ai-agent-rbac.yaml). It creates the
`ai-agent` SA in `default` and binds it to read-only roles only:

- the built-in **`view`** ClusterRole — get/list/watch on most namespaced
  resources (pods, services, deployments, configmaps, events, …), but **not**
  Secrets and **not** the `/proxy` subresources;
- **`eks:nodewatcher`** — read-only visibility of Nodes, which `view` omits;
- a small **`ai-agent-proxy`** ClusterRole — GET-only apiserver proxy to
  Services and Pods, so the agent can reach in-cluster HTTP APIs
  (Prometheus/Thanos, metrics exporters) that have no external route. This is
  what the `metrics` skill relies on.

No write verb is granted anywhere, so read-only is enforced **server-side** by
the API server — independently of the wrapper's own client-side guards.

Deliver it the way the cluster is already managed — via **GitOps with Argo CD**.
Commit the manifest to the repo Argo reconciles and let Argo sync it; don't
`kubectl apply` it by hand, which drifts from the declared state Argo owns.
Adapt the namespace, role names (`eks:nodewatcher` is EKS-specific), or proxy
scope to your cluster before committing.

The agent must never create these resources — it has no write RBAC by design,
and the whole point is that the agent cannot grant itself access. If
`kubeconfig-generator` fails because the `ai-agent` SA doesn't exist, surface
that the SA and its RBAC need to be reconciled into the cluster through Argo CD;
don't try to create them yourself.

## How to run it

Run every command through the **Bash tool**, calling `kubectl-readonly` (it's on
`PATH` — no directory prefix). It takes the same arguments as `kubectl`:

```
Bash(kubectl-readonly get nodes)
Bash(kubectl-readonly get pods -A)
Bash(kubectl-readonly -n kube-system get events --sort-by=.lastTimestamp)
Bash(kubectl-readonly logs deploy/my-app -n my-ns --tail=100)
Bash(kubectl-readonly describe node <node>)
```

Don't invoke `kubectl` directly, by absolute path, or via `KUBECONFIG=...` /
`--kubeconfig` — the PreToolUse hook denies anything that isn't `kubectl-readonly`,
so it just wastes a turn.

The pinned kubeconfig (`$HOME/.kube/ai-agent.kubeconfig`) holds a temporary token
for the `ai-agent` ServiceAccount. The wrapper itself does **not** mint: on every
call it requires that file to exist, forces it as the only kubeconfig (scrubbing
any `KUBECONFIG` env var), verifies the token is live and belongs to that SA (one
`auth whoami` round-trip), requires the token to **carry an expiration** —
non-expiring (legacy/static) credentials are rejected and the wrapper fails hard —
and requires the pinned file's cluster to **match your current context**: a
still-live token for a cluster you've since switched away from passes every other
check, so the wrapper refuses to run rather than silently query the old cluster.
Read-only access itself is enforced server-side by the SA's RBAC.

### Provisioning / re-provisioning the kubeconfig

When the wrapper reports the file is missing, the token is expired, or **the
pinned cluster no longer matches your current context** (you've run
`kubectl config use-context` since it was provisioned), the fix is to re-run
`kubeconfig-generator`. It mints a fresh temporary token via the TokenRequest API
and writes the kubeconfig straight to the pinned path — reading the cluster,
server, and CA from the user's current (admin) context, which re-pins it to
wherever you're now pointed:

```
kubeconfig-generator
```

**On a context mismatch specifically, stop and ask the user before re-provisioning.**
The block-context-mismatch hook denies the read because the pinned kubeconfig is
for a *different* cluster than your current context — re-running the generator
silently re-points the agent at the now-current cluster, which is a deliberate
choice the user should make, not you. Surface both clusters from the deny message
(the server it's pinned to vs. the server your context now points at) and ask
something like: "Your kubeconfig is pinned to cluster A but your current context
is cluster B. Re-provision against cluster B?" Only run `kubeconfig-generator`
once they confirm — and if they actually meant to query cluster A, the fix is to
switch their context back (`kubectl config use-context`), not to re-mint.

For the missing-file and expired-token cases there's no ambiguity about *which*
cluster — run it without asking, but say what you're doing and why (the command
is pre-allowed, so no permission prompt will stop you).

`kubeconfig-generator` is on `PATH` (no directory prefix), just like
`kubectl-readonly`. It takes no arguments — the ServiceAccount (`ai-agent`),
namespace (`default`), output path (`~/.kube/ai-agent.kubeconfig`), and 1h token
duration are all hardcoded. It runs with the **user's** admin credentials —
that's why you never run it silently: always tell the user you're
re-provisioning. The wrapper's own failure message prints the same command. It
only ever mints a temporary, expiring token via the TokenRequest API — exactly
what the wrapper requires — and it fails fast without issuing anything if the
`ai-agent` SA's RBAC is not read-only (it probes the write verbs by
impersonating the SA first).

## It is genuinely read-only

The ServiceAccount has no write RBAC, and identity-override flags
(`--as`, `--user`, `--token`, `--context`, `--server`) are rejected by the
wrapper. So reads (`get`, `describe`, `logs`, `top`, `events`, `explain`)
work; mutations (`apply`, `edit`, `delete`, `scale`, `patch`, `create`,
`rollout restart`, `exec` into a container) will be refused by the API server.

## Checking kubectl syntax against the official docs

Don't guess at command syntax, flags, or output options from memory — when you're
unsure how a `kubectl` subcommand works (which flags it takes, valid values for
`-o`/`--output`, `--sort-by` field paths, `--field-selector` keys, JSONPath
expressions, the exact name of a resource type, etc.), confirm it against the
**official Kubernetes documentation** before running the command. A wrong flag
wastes a turn (or worse, the wrapper/hook denies it); a 30-second doc check
doesn't.

Use **WebSearch** to find the relevant page, then **WebFetch** to read it.
kubernetes.io is ordinary rendered web content, so `WebFetch` reaches it directly.
The authoritative references:

- **kubectl command reference** (every subcommand, every flag):
  <https://kubernetes.io/docs/reference/generated/kubectl/kubectl-commands>
- **kubectl overview & usage conventions** (output formats, JSONPath, sorting):
  <https://kubernetes.io/docs/reference/kubectl/>
- **kubectl cheat sheet** (common invocations):
  <https://kubernetes.io/docs/reference/kubectl/cheatsheet/>
- **JSONPath support** (for `-o jsonpath=...`):
  <https://kubernetes.io/docs/reference/kubectl/jsonpath/>

When searching, scope the query to the official site for a faster hit, e.g.
`kubectl get --field-selector site:kubernetes.io` or
`site:kubernetes.io kubectl logs flags`. Remember everything you run is still
read-only via `kubectl-readonly`: confirm syntax from the docs, but only ever run
the read verbs (`get`, `describe`, `logs`, `top`, `events`, `explain`).

You can also confirm a resource's fields without leaving the cluster — the
read-only `explain` verb is allowed: `kubectl-readonly explain pod.spec.containers`.
Use the docs for command/flag syntax and `explain` for live schema.
