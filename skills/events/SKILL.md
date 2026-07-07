---
name: events
context: fork
agent: kubernetes-explore:events
user-invocable: false
allowed-tools: Bash(kubectl-readonly:*), Bash(kubeconfig-generator), WebSearch, WebFetch
model: sonnet
description: >-
  Investigate Kubernetes events in the current kubectl context's cluster —
  warnings, scheduling failures, probe failures, image-pull errors, evictions,
  volume problems — scoped by namespace, object, type, or reason. Use this
  skill whenever the user wants events checked or explained ("any warnings in
  namespace X", "why won't this pod schedule", "what happened to this
  deployment", "show recent eviction events"), even if they don't say
  "events".
---

# events (Kubernetes events via kubectl-readonly)

Query events with `kubectl-readonly`. Two references next to this skill carry
the depth — read the one you need before composing queries:

- [`references/event-queries.md`](references/event-queries.md) — the
  `kubectl events` subcommand vs `kubectl get events` (they filter
  differently), the supported field-selector keys, and worked query examples.
- [`references/event-reasons.md`](references/event-reasons.md) — the reason
  catalog: what each reason means, which component emits it, and what to
  check next; officially documented reasons separated from commonly observed
  ones.

**Use the `/kubectl` skill for the cluster access itself.** If a call fails
with a missing/expired kubeconfig or a context mismatch, consult `/kubectl`
for the recovery rules before acting: `kubeconfig-generator` is pre-allowed
here so you can re-provision directly for the unambiguous
missing-file/expired-token cases (say you're doing it), but a **context
mismatch** still requires asking the user first — then retry.

## The two facts that shape every investigation

1. **Events expire — default TTL is 1 hour** (kube-apiserver `--event-ttl`,
   default `1h0m0s`). An empty result for an incident older than that means
   the trail is *gone*, not that nothing happened. Say so, and pivot to
   durable state: `lastState.terminated` on the pod, object conditions,
   metrics, logs.
2. **Repeats are deduplicated.** A recurring problem shows as one Event with
   an incrementing `count` / `series` (rendered as `(x3 over 20s)` in
   describe output), not N objects. Always report the count and last-seen
   time, not just the message — `x400 over 3d` and `x2 over 10s` are
   different incidents.

## Querying: bound and scope, never dump

Warnings first — most "what's wrong" questions are answered by:

```sh
kubectl-readonly events -n <ns> --types=Warning
kubectl-readonly get events -n <ns> --field-selector type=Warning --sort-by=.lastTimestamp | tail -30
```

One object's story (`--for` covers exactly one resource):

```sh
kubectl-readonly events -n <ns> --for pod/<name>
kubectl-readonly events -n <ns> --for deployment/<name>
```

By reason or involved object across a namespace (field selectors only work
on `get events` — see the queries reference for the supported keys):

```sh
kubectl-readonly get events -A --field-selector reason=FailedScheduling
kubectl-readonly get events -n <ns> --field-selector involvedObject.name=<name>,involvedObject.kind=Pod
```

Rules:

- Never `--watch`/`-w` — it blocks the shell and streams unbounded output.
- Scope to a namespace or object; an all-namespaces query needs a `--types`
  or `--field-selector` filter plus a `tail` cap.
- `kubectl-readonly describe pod <name>` shows the same events scoped to one
  object — fine as part of a targeted probe, but prefer `events --for` when
  events are all you need (describe drags the full spec with it).

## Reporting

- Quote the proving events as reason + involved object + count + last-seen
  time, not raw dump lines.
- State the scope you searched (namespace(s), types, selector) and note the
  ~1h TTL whenever you report an absence of events.
- Decode reasons via the catalog reference; when a reason isn't in it, check
  kubernetes.io before speculating, and say when a reason is commonly
  observed but not officially documented.
