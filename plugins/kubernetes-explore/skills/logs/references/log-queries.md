# kubectl logs — flags and retrieval limits

Verified against kubernetes.io; run everything through `kubectl-readonly`.

## Flags

Source: <https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/>

| Flag | Default | Notes |
|---|---|---|
| `--since <dur>` | all logs | Relative window (`5s`, `2m`, `3h`). Mutually exclusive with `--since-time`. |
| `--since-time <RFC3339>` | all logs | Absolute start. Mutually exclusive with `--since`. |
| `--tail <n>` | `-1` (all) | **Flips to 10 when `-l/--selector` is used** — pass it explicitly to be deterministic. |
| `-p, --previous` | false | Previous instance of the container (crash forensics). |
| `-c, --container <name>` | — | Required for multi-container pods; omit for single-container. |
| `--all-containers` | false | Every container in the pod(s). |
| `--all-pods` | false | Every pod of a workload (`deployment/x --all-pods=true`); **implies `--prefix`**. |
| `--prefix` | false | Prefix lines with pod/container source. |
| `--timestamps` | false | Add timestamps — do this when correlating with events/metrics. |
| `--limit-bytes <n>` | no limit | Hard byte cap; combines with `--tail`. |
| `-l, --selector <q>` | — | Label query (`=`, `!=`, `in`, `notin`). |
| `--max-log-requests <n>` | 5 | Concurrency cap with a selector. |
| `-f, --follow` | false | **Never use here** — unbounded stream. |
| `--pod-running-timeout` | 20s | Wait for at least one running pod. |

Workload form: `kubectl logs deployment/<name>` (also `job/`, etc.). Without
`--all-pods` it reads a **single pod** — and which one it picks is
undocumented, so for multi-replica workloads use `--all-pods=true` or a
selector with `--prefix` when coverage matters.

## Retrieval limits that shape queries

Source: <https://kubernetes.io/docs/concepts/cluster-administration/logging/>

- **Only the latest rotated log file is served.** Kubelet rotates at
  `containerLogMaxSize` (default **10Mi**), keeping `containerLogMaxFiles`
  (default 5) — but `kubectl logs` returns at most the current file. A busy
  pod's `--since=6h` may silently cover far less than 6 hours; if the volume
  looks truncated, say so.
- **One previous instance.** After a restart the kubelet keeps exactly one
  terminated container's logs (`-p` reads it). Two restarts ago is gone.
- **Logs don't survive the pod.** Eviction/deletion removes the container
  logs with it — durable history requires a log aggregation stack, not
  kubelet.

## Worked examples (from the official page, adapted)

```sh
# Bounded window + cap + filter — the default shape for investigation
kubectl-readonly logs <pod> -n <ns> --since=15m --tail=2000 \
  | rg -i 'error|fatal|exception|panic|"status":5[0-9][0-9]' | tail -40

# All replicas of a workload, lines labeled by source
kubectl-readonly logs deployment/<name> -n <ns> --all-pods=true --since=15m --tail=200

# By label, explicit tail (selector default is only 10), bounded concurrency
kubectl-readonly logs -l app=<name> -n <ns> --prefix --tail=200 --max-log-requests=10

# Specific container; timestamps for correlation
kubectl-readonly logs <pod> -n <ns> -c <container> --since-time=2026-07-03T06:00:00Z --timestamps

# Byte-capped safety net for extremely chatty pods
kubectl-readonly logs <pod> -n <ns> --tail=500 --limit-bytes=200000
```
