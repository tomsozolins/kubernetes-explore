# Event reason catalog

What each reason means, who emits it, and what to check next. Kubernetes has
**no comprehensive official reason catalog** (the strings live in component
source, e.g. `pkg/kubelet/events/event.go`), so this file separates reasons
grounded in kubernetes.io docs from ones that are real and common but only
documented in source/community material. Report the distinction when it
matters.

## Documented on kubernetes.io

### Scheduling

| Reason | Type | From | Meaning → check next |
|---|---|---|---|
| `FailedScheduling` | Warning | default-scheduler | No node fits the pod. The message says why, e.g. `Node didn't have enough resource: CPU, requested: 1000, used: 1420, capacity: 2000`. Check requests vs node allocatable, taints/tolerations, node/pod affinity, PVC binding. |
| `Scheduled` | Normal | default-scheduler | Pod assigned to a node (`Successfully assigned <ns>/<pod> to <node>`). Useful as the timeline anchor. |

Source: <https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/>

### Container lifecycle (kubelet)

| Reason | Type | Meaning → check next |
|---|---|---|
| `Pulling` / `Pulled` | Normal | Image pull started / succeeded. |
| `Created` / `Started` | Normal | Container created / started. |
| `Unhealthy` | Warning | Liveness/readiness/startup probe failed, e.g. `Liveness probe failed: cat: can't open '/tmp/healthy': No such file or directory`. Check the probe's command/endpoint, app health, probe timeouts/thresholds. Repeated liveness failures lead to `Killing`. |
| `Killing` | Normal | kubelet stopping a container, e.g. `Container <name> failed liveness probe, will be restarted`. Normal type — don't let it hide in a Warnings-only query when investigating restarts. |

Sources:
<https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/>,
<https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/>

### Image pull failures

`ImagePullBackOff` is documented as the container **waiting-state reason**:
the image couldn't be pulled (bad name/tag, private registry without
`imagePullSecrets`), and kubelet retries with growing back-off up to a
compiled-in 300s cap. The accompanying *events* typically carry reasons
`Failed`/`BackOff` (those exact event strings aren't itemized in the docs).
Check: exact image name and tag exist, registry auth, node network to the
registry.

Source: <https://kubernetes.io/docs/concepts/containers/images/>

### Garbage collector

| Reason | Type | Meaning |
|---|---|---|
| `OwnerRefInvalidNamespace` | Warning | An ownerReference crosses namespaces illegally; the official query is `kubectl get events -A --field-selector=reason=OwnerRefInvalidNamespace`. |

Source: <https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/>

## Documented as concepts, but the event string isn't in the docs

- **Eviction (`Evicted`)** — node-pressure eviction is fully documented
  (kubelet fails the pod and terminates it when `memory.available`,
  `nodefs.available`, `pid.available`, etc. cross thresholds; the node also
  reports MemoryPressure/DiskPressure/PIDPressure conditions). `Evicted` is
  the pod's `status.reason`; treat the event string as conventional. Check:
  node conditions, which eviction signal fired, pod QoS class.
  <https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/>
- **OOM kills** — a node-level OOM (kernel oom_killer) is distinct from
  eviction: the kernel kills the highest-oom_score container and kubelet may
  restart it per `restartPolicy`. The durable evidence is the container's
  `lastState.terminated.reason: OOMKilled` — probe that field; don't rely on
  an `OOMKilling` event (not an officially documented reason).
  <https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/>
- **Taint-based eviction** — documented as the pod DisruptionTarget condition
  reason `DeletionByTaintManager` (a NoExecute taint the pod doesn't
  tolerate); `TaintManagerEviction` as an event reason is not in the docs.
  Node-failure evictions relate to the `node.kubernetes.io/not-ready` /
  `unreachable` taints with the auto-added 300s tolerations.
  <https://kubernetes.io/docs/concepts/workloads/pods/pod-condition/>,
  <https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/>

## Commonly observed, NOT in official docs

Real-world reasons you'll meet that kubernetes.io never enumerates — decode
them, but attribute them as "commonly observed":

| Reason | From | Meaning → check next |
|---|---|---|
| `FailedMount` | kubelet | Volume mount failed (`MountVolume.SetUp failed ...`). Check: PVC bound, Secret/ConfigMap referenced by the volume exists, CSI driver pods healthy. |
| `FailedAttachVolume` | attachdetach-controller | Volume attach failed (`AttachVolume.Attach failed ...`) — often the volume is still attached to another node after a node failure, or a cloud/CSI quota/permission issue. |
| `BackOff` | kubelet | Restart back-off (`Back-off restarting failed container`) — the event face of CrashLoopBackOff. Pivot to previous-container logs (`logs -p`) and `lastState.terminated`. |
| `NodeNotReady` | node-controller | Node stopped reporting ready; pods on it will be marked and eventually evicted via the not-ready/unreachable taints. Check node conditions and kubelet health on that node. |
| `FailedCreatePodSandBox` | kubelet | Sandbox (pause container / network) creation failed — usually CNI: IP exhaustion, CNI plugin errors. Check the message and the CNI daemonset. |

When a reason isn't listed here at all, search kubernetes.io first, then the
component's source; say which grounding you found.
