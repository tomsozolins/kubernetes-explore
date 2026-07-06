---
name: troubleshoot
model: sonnet
description: >-
  Troubleshoot anything running in the current kubectl context's Kubernetes
  cluster — failing, restarting, or crash-looping pods, errors, slowness,
  OOMKills, firing alerts, scheduling problems, unhealthy or degraded
  workloads. Invoke whenever the user reports a symptom and wants the cause
  found ("what's wrong with the cluster / namespace X / app Y", "why is X
  failing / slow / restarting / erroring", "investigate this alert"), even
  when one obvious probe looks sufficient. Orchestrates a concurrent fan-out
  of the events, logs, and metrics subagents plus a kubectl object-state
  probe, then synthesizes the evidence into a root cause.
---

# Kubernetes troubleshooting

Your job is to turn a symptom into a cause backed by concrete evidence —
metric values, log lines, events, object state — or to confirm health with
the same rigor. This skill is the orchestration layer: the probing itself is
delegated to subagents so their discovery churn never lands in this
conversation.

## Fan out first, always — via the Agent tool

**Never probe serially.** The slow failure mode is starting with one angle,
waiting for it, and only then trying the next — when the answer was sitting
in another signal all along. The complementary failure mode is subtler:
events or logs surface *a* finding quickly (a restart, an error line), and
the remaining probes get skipped as "optional" — leaving the question that
only another signal could answer (how bad, since when, what limit, what
trend) unasked. A restart reason is not a root cause; run the full wave.

Launch the **entire first wave in a single message** — all Agent tool calls
concurrent — and synthesize once they return:

1. **Events** — subagent `kubernetes-explore:events`: warnings in the
   implicated namespace(s), newest last — OOMKills, probe failures,
   evictions, scheduling failures, image/volume problems.
2. **Logs** — subagent `kubernetes-explore:logs`: discover the relevant pods,
   then bounded, filtered searches (`--since`/`--tail` plus an
   `error|fatal|exception|panic` pattern or the symptom's own signature);
   after crashes, previous-container logs.
3. **Metrics** — subagent `kubernetes-explore:metrics`: firing alerts
   (`ALERTS{alertstate="firing"}`), saturation (CPU/memory working set vs
   limits, throttling, restarts trend), error rates for the implicated
   workload. Metrics is where "how bad / since when / against what limit"
   gets a number — it is part of wave 1 even when events or logs already
   named a suspect.
4. **Object state** — a `general-purpose` subagent using the
   **`kubectl-readonly`** command (bare `kubectl` is blocked): pod status and
   restart counts, deployment/statefulset conditions, HPA / autoscaler state,
   node pressure, recent rollouts. Tell it to invoke the
   `kubernetes-explore:kubectl` skill first for the wrapper's usage and
   recovery rules, keep every query bounded, and return a summary.

Each subagent starts cold: its prompt must carry the user's specifics
(namespace, workload names, timeframe, the symptom as reported) and must ask
for a **short summary with the exact values/lines that matter** — never a raw
dump.

## Wave 2 — targeted, driven by wave-1 findings

Only what the evidence calls for; independent follow-ups again go out
concurrently in one message:

- **An alert is firing** → have the object-state probe pull the alert's
  expression from its `PrometheusRule` CRD, then have the metrics subagent
  evaluate that expression for magnitude and trend, not just the boolean.
- **A pod/component is implicated** → drill into its previous-container
  logs, its events, and the metric named by the symptom.
- **Resource pressure suspected** → metrics is primary (working-set memory
  and CPU rate vs `kube_pod_container_resource_limits`, CPU throttling);
  `kubectl-readonly top` only as a quick cross-check.
- **A config/limit value is in question** → prefer reading it from live
  state (CRDs, ConfigMaps via the object-state probe) or metrics over
  guessing defaults.

Stop when the cause is confirmed by evidence — don't run probes that can no
longer change the conclusion.

## Hard constraints

- **Read-only cluster access.** Everything goes through `kubectl-readonly`;
  no exec, no writes, no port-forward. Don't plan steps that need them.
- Application-internal state that isn't exposed as metrics, logs, events, or
  API objects is not readable. Infer from observable signals and say plainly
  when a definitive answer needs access you don't have.

## Reporting

- Lead with the conclusion: what's wrong (or "X looks healthy"), how severe,
  and the single most important piece of evidence.
- Back every claim with the specific metric value, event, log line, or
  object field behind it.
- Separate **confirmed** from **inferred**, and call out where read-only
  access stopped short of certainty.
- Recommend next steps, flagging any that need write or admin access you
  lack.
