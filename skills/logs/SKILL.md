---
name: logs
context: fork
agent: kubernetes-explore:logs
user-invocable: false
allowed-tools: Bash(kubectl-readonly:*), Bash(kubeconfig-generator), WebSearch, WebFetch
model: sonnet
description: >-
  Read pod/container logs in the current kubectl context's cluster — targeted,
  bounded, and filtered — via the read-only kubectl-readonly wrapper. Use this
  skill whenever the user wants to see, search, or summarize logs ("show me
  the logs", "any errors in service X", "why did this pod crash — check its
  logs", "grep the last hour of logs for Y"), including previous-container
  logs after a crash/restart.
---

# logs (targeted pod logs via kubectl-readonly)

Retrieve logs with `kubectl-readonly logs`. The non-negotiable rule: **never
stream a full log into context** — every invocation must be bounded
(`--since`/`--tail`) and, when you're hunting a signal, filtered (`rg`) before
the output reaches you.

Two references next to this skill carry the depth — read the one you need:

- [`references/log-queries.md`](references/log-queries.md) — the full flag
  table with the traps (`--tail` silently drops to 10 with a selector,
  `--all-pods` for workload coverage), kubelet rotation limits on what
  `kubectl logs` can actually return, and worked examples.
- [`references/crash-forensics.md`](references/crash-forensics.md) —
  CrashLoopBackOff back-off mechanics, and the evidence chain from
  previous-container logs to `lastState.terminated` when logs are gone.

**Use the `/kubectl` skill for the cluster access itself** — it owns the
`kubectl-readonly` wrapper, the pinned kubeconfig, and provisioning. If a call
fails with a missing/expired kubeconfig or a context mismatch, consult
`/kubectl` for the recovery rules before acting: `kubeconfig-generator` is
pre-allowed here so you can re-provision directly for the unambiguous
missing-file/expired-token cases (say you're doing it), but a **context
mismatch** still requires asking the user first — then retry.

## Find the target first

Don't guess pod names — discover them, then aim:

```sh
kubectl-readonly get pods -A | grep -i <app>                  # find the namespace
kubectl-readonly get pods -n <ns> -o name | grep <component>  # exact pod names
kubectl-readonly get pods -n <ns> -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.containers[*].name}{"\n"}{end}'  # containers per pod
```

Prefer logging by **workload or label selector** over a single pod — it covers
all replicas and survives pod churn:

```sh
kubectl-readonly logs deployment/<name> -n <ns> --all-pods=true --since=15m --tail=200
kubectl-readonly logs -l app=<name> -n <ns> --since=15m --tail=500 --prefix
```

Two traps: without `--all-pods`, the workload form reads **one undocumented
pod** of the set; and with a selector, `--tail` defaults to just 10 — always
pass it explicitly. (`--prefix` labels each line with its source;
`--all-pods` implies it.)

## Bound, then filter

Start narrow and widen only if empty — a huge dump is a bug, not thoroughness:

```sh
kubectl-readonly logs <pod> -n <ns> --since=15m --tail=2000 \
  | rg -i 'error|fatal|exception|panic|"status":5[0-9][0-9]' | tail -40
```

- `--since=15m` / `--since-time=<RFC3339>` — pin the window to the incident.
- `--tail=N` — hard cap even inside the window.
- `--timestamps` — add timestamps when the app's own lines lack them, so you
  can correlate with metrics/events.
- `-c <container>` — required for multi-container pods; list containers first
  (see discovery above). `--all-containers` when you genuinely need every one.
- Never `-f`/`--follow` — it blocks the shell and streams unbounded output.

## Crash loops: the evidence is in the *previous* container

A `CrashLoopBackOff` pod's current logs are usually a few startup lines; the
failure is in the last run. Use `-p`:

```sh
kubectl-readonly logs <pod> -n <ns> -p --tail=100
```

Only **one** previous instance is kept — if `-p` is empty, the durable
evidence is `lastState.terminated` (exit code, reason, timestamps) and
events; the full chain is in
[`references/crash-forensics.md`](references/crash-forensics.md).

## Reporting

- Quote the handful of log lines that carry the signal (with pod name and
  timestamp), not the surrounding noise.
- State the window and filter you used, so absence of evidence is meaningful
  ("no 5xx lines in the last hour of webservice logs" beats "looks fine").
- If logs rotated past the incident or `-p` came back empty, say so — don't
  present a truncated view as the full picture.
