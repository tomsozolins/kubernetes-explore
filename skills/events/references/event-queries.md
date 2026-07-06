# Querying Kubernetes events

Everything here is verified against kubernetes.io; run all commands through
`kubectl-readonly`.

## Two commands, different filters

| | `kubectl events` (modern) | `kubectl get events` |
|---|---|---|
| Filter to one object | `--for TYPE/NAME` | `--field-selector involvedObject.name=...,involvedObject.kind=...` |
| Filter by type | `--types=Warning,Normal` | `--field-selector type=Warning` |
| Filter by reason / other fields | not supported | `--field-selector reason=...` etc. |
| Sorting | prints a time-ordered table | unsorted unless `--sort-by` is given |

`kubectl events` has **no `--field-selector` and no `--sort-by`** — its only
filters are `--for` and `--types`. The moment you need reason/field filtering
or explicit sorting, switch to `kubectl get events`.

Source: <https://kubernetes.io/docs/reference/kubectl/generated/kubectl_events/>

### `kubectl events` flags

| Flag | Meaning |
|---|---|
| `-A, --all-namespaces` | List across all namespaces (overrides `-n`). |
| `--for string` | Only events pertaining to the specified resource (`pod/web-1`, `deployment/api`). |
| `--types strings` | Only events of the given types (`Warning`, `Normal`). |
| `--no-headers` | Omit the header row. |
| `-o, --output` | `json`, `yaml`, `name`, `go-template…`, `jsonpath…`. |
| `--chunk-size int` | List chunking (default 500). |
| `-w, --watch` | Watch after listing — **never use it here** (unbounded stream). |

## Field-selector keys supported on events

With `kubectl get events --field-selector` (source:
<https://kubernetes.io/docs/concepts/overview/working-with-objects/field-selectors/>):

```
involvedObject.kind          involvedObject.namespace     involvedObject.name
involvedObject.uid           involvedObject.apiVersion    involvedObject.resourceVersion
involvedObject.fieldPath     reason                       reportingComponent
source                       type
```

plus `metadata.name` / `metadata.namespace` (supported on every resource).

Semantics: operators `=`, `==`, `!=` only — no `in`/`notin`/`exists`.
Chain multiple selectors with commas (AND). An unsupported key fails with
`BadRequest` listing the valid ones, so a typo is loud, not silent.

## Worked examples

```sh
# Warnings in a namespace, oldest → newest, capped
kubectl-readonly get events -n <ns> --field-selector type=Warning --sort-by=.lastTimestamp | tail -30

# Everything that happened to one pod (events only — no full describe)
kubectl-readonly events -n <ns> --for pod/<name>

# All scheduling failures cluster-wide
kubectl-readonly get events -A --field-selector reason=FailedScheduling

# Events about a specific Deployment object (not its pods — events attach to
# the object named in involvedObject; pods get their own)
kubectl-readonly get events -n <ns> --field-selector involvedObject.kind=Deployment,involvedObject.name=<name>

# Non-normal noise across the cluster, capped hard
kubectl-readonly get events -A --field-selector type!=Normal --sort-by=.lastTimestamp | tail -40

# Official example: invalid cross-namespace ownerReferences (garbage collector)
kubectl-readonly get events -A --field-selector=reason=OwnerRefInvalidNamespace
```

## The Event object: fields worth extracting

Events are namespaced core/v1 objects; `kubectl` addresses core/v1 field
names (`involvedObject`, `message`, `source`). The newer `events.k8s.io/v1`
API renames `involvedObject`→`regarding` and `message`→`note` — you'll see
those names in API-machinery contexts, not in kubectl field selectors.

For deduplication: repeated occurrences increment `count` (and/or `series.count`
with `series.lastObservedTime`) on a single Event object instead of creating
new ones. Extract them when the timeline matters:

```sh
kubectl-readonly get events -n <ns> --field-selector type=Warning \
  -o jsonpath='{range .items[*]}{.lastTimestamp}{"\t"}{.count}{"\t"}{.reason}{"\t"}{.involvedObject.kind}/{.involvedObject.name}{"\t"}{.message}{"\n"}{end}' | tail -30
```

## Retention

The apiserver retains events for **1 hour by default** (`kube-apiserver
--event-ttl`, default `1h0m0s` — "Amount of time to retain events").
Post-incident forensics older than the TTL must come from durable state
(object status/conditions, `lastState.terminated`), metrics, or logs.

Sources:
<https://kubernetes.io/docs/reference/command-line-tools-reference/kube-apiserver/>,
<https://kubernetes.io/docs/reference/kubernetes-api/core/event-v1/>,
<https://kubernetes.io/docs/reference/using-api/deprecation-guide/>
