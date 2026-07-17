# Crash forensics — states, back-off, and where the evidence lives

Verified against kubernetes.io.

## Container states

Three states: `Waiting` (starting up — pulling image, applying Secrets; a
`Reason` field says why), `Running`, `Terminated` (ran and finished/failed —
carries reason, exit code, start/finish times).

Source: <https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/>

## CrashLoopBackOff mechanics

- After containers exit, the kubelet restarts them with **exponential
  back-off: 10s, 20s, 40s, … capped at 300s (5 minutes)**; the timer resets
  after the container runs 10 minutes without problems. (Feature gates in
  newer clusters can shrink these: `ReduceDefaultCrashLoopBackOffDecay`
  starts at 1s/caps at 60s.)
- `CrashLoopBackOff` in `kubectl` output is a **display status**, not a pod
  phase — it means the back-off delay is currently in effect for a crashing
  container.
- Official investigation order: container logs first (`kubectl logs`), then
  events (`describe pod`).

Source: <https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/>

## The evidence, in order of durability

1. **Previous-container logs** — `kubectl-readonly logs <pod> -n <ns> -p
   --tail=100` (add `-c <container>` for multi-container pods). The current
   instance's log is usually just startup lines; the failure is in the last
   run. Only **one** previous instance is kept.
   Source: <https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/>
2. **`lastState.terminated`** — survives even when `-p` has nothing (or the
   crash predates the kept instance). Documented fields: `exitCode`,
   `reason`, `signal`, `message`, `startedAt`, `finishedAt`. `restartCount`
   sits alongside (kubelet may reset it to 0 across node restarts).

   ```sh
   kubectl-readonly get pod <pod> -n <ns> \
     -o jsonpath='{range .status.containerStatuses[*]}{.name}{"\trestarts="}{.restartCount}{"\t"}{.lastState.terminated}{"\n"}{end}'
   ```

   `reason` is a free-form string per the API ("brief reason from the last
   termination") — `OOMKilled` is the conventional runtime value for
   memory kills, not an enumerated constant; treat unfamiliar values as
   runtime-specific and check the exit code (137 = SIGKILL) alongside.
   Sources: <https://kubernetes.io/docs/reference/kubernetes-api/workload-resources/pod-v1/>,
   <https://kubernetes.io/docs/tasks/debug/debug-application/determine-reason-pod-failure/>
3. **Events** — `BackOff`/`Killing`/`Unhealthy` around the restarts; ~1h TTL.
   Use the `events` skill's references for reasons and queries.

Restart-count triage across a namespace (official quick-reference pattern):

```sh
kubectl-readonly get pods -n <ns> --sort-by='.status.containerStatuses[0].restartCount' \
  | tail -10
```
