---
name: metrics
context: fork
agent: kubernetes-explore:metrics
allowed-tools: Bash(kubectl-readonly:*), Bash(kubeconfig-generator), WebSearch, WebFetch
model: sonnet
description: >-
  Query Prometheus / Thanos metrics in the current kubectl context's cluster when
  there's no direct route to the server — instant/range queries, series, label
  values — by reaching the HTTP API through the k8s apiserver proxy. Use this
  skill whenever the user wants to run a PromQL query, list metric names, or
  inspect series/labels against a Prometheus or Thanos instance that lives in
  the cluster but isn't reachable from your shell.
---

# metrics (Prometheus/Thanos via the k8s apiserver proxy)

When Prometheus/Thanos isn't reachable from your shell but the cluster is, query
it with `kubectl-readonly get --raw`, reaching its HTTP API through the k8s
apiserver proxy.

The full API detail lives next to this skill in
[`references/prometheus-http-api.md`](references/prometheus-http-api.md) —
every endpoint's parameters and result shapes, the proxy URL forms (including
the `https:` prefix syntax), PromQL composition rules (anchored regexes,
rate-then-aggregate, histogram_quantile), and the Thanos-specific query
params. Read it before composing anything beyond the basic patterns below.

**Use the `/kubectl` skill for the cluster access itself** — it owns the
`kubectl-readonly` wrapper, the pinned kubeconfig, and provisioning. This skill
only adds the Prometheus-specific bits on top: the proxy path and PromQL
URL-encoding below. So if a `--raw` call fails with a missing/expired kubeconfig
or a context mismatch, consult `/kubectl` for the recovery rules before acting:
`kubeconfig-generator` is pre-allowed here so you can re-provision directly for
the unambiguous missing-file/expired-token cases (say you're doing it), but a
**context mismatch** still requires asking the user first — then retry the
query.

## RBAC: proxy access is already granted

The proxy route below needs `get` on `services/proxy` (and `pods/proxy`). The
built-in **`view`** ClusterRole omits the proxy subresources the same way it
omits secrets, so a dedicated **`ai-agent-proxy`** ClusterRole + binding grants
them to the `ai-agent` SA **cluster-wide** (both `services/proxy` and
`pods/proxy`, `get` only). It stays read-only — `get` on a proxy authorizes
GET-method proxying only (no exec/portforward/write), and the
`kubeconfig-generator` read-only gate probes write verbs only, so it still
passes. No per-namespace grant is needed; proxy works in any namespace.

So a `Forbidden ... cannot get resource "services/proxy"` is **unexpected** — it
means the `ai-agent-proxy` binding is missing or was removed, not a wrong
service/port name. Surface it to the user to re-apply; the SA can't (and
shouldn't) self-grant.

**Verifying the grant — beware a `can-i` false positive.** `kubectl auth can-i
get services/proxy` returns a misleading **`yes`** (it reads `services/proxy` as
a literal resource name). Always probe the subresource form, which reports the
truth:

```sh
kubectl-readonly auth can-i get services --subresource=proxy -n monitoring   # yes = granted (expected)
```

## Pick the backend: Prometheus (recent) vs Thanos (long-term)

A cluster may run **Prometheus only**, or **Prometheus + Thanos**. They speak
the identical HTTP API, so the proxy path is the same — only the service name
and what data they hold differ:

- **Prometheus** (`kube-prometheus-stack-prometheus:9090`, port `http-web`) —
  local, short retention. Use for **recent** data (last hours/days).
- **Thanos Query** (commonly `thanos-query:9090` or `thanos-query-frontend:9090`)
  — fans out across Prometheus + object-storage history. Use for **long-range /
  historical** queries. Absent on Prometheus-only clusters.

Don't assume either name — **discover what's deployed first**, then choose by
the time range you need (Thanos when present and the window is long; Prometheus
for recent/short windows):

```sh
kubectl-readonly -n monitoring get svc   # look for thanos-query* and the *-prometheus service
```

## The proxy path

The path is `/api/v1/namespaces/<ns>/services/<svc>:<port>/proxy/<prometheus-api-path>` —
URL-encode the query string. Same path for either backend; swap the service
name. E.g. list metric names matching `gitlab_shell.*`:

```sh
# Prometheus (recent data)
kubectl-readonly get --raw "/api/v1/namespaces/monitoring/services/kube-prometheus-stack-prometheus:9090/proxy/api/v1/label/__name__/values?match%5B%5D=%7B__name__%3D~%22gitlab_shell.*%22%7D"

# Thanos (long-term history; only where thanos-query is deployed)
kubectl-readonly get --raw "/api/v1/namespaces/monitoring/services/thanos-query:9090/proxy/api/v1/label/__name__/values?match%5B%5D=%7B__name__%3D~%22gitlab_shell.*%22%7D"
```

(`match%5B%5D` = `match[]`, `%7B...%7D` = `{...}`, `%3D~` = `=~`, `%22` = `"`.)
The same proxy prefix works for every Prometheus HTTP API endpoint —
`/api/v1/query`, `/api/v1/query_range`, `/api/v1/series`, `/api/v1/labels`,
`/api/v1/label/<NAME>/values`; parameters and result shapes are in the
reference.

Always URL-encode the PromQL: `{` → `%7B`, `}` → `%7D`, `"` → `%22`, `=~` →
`%3D~`, `=` → `%3D`, space → `%20`, `[` → `%5B`, `]` → `%5D`. `kubectl get
--raw` is GET-only (no POST fallback for long queries), so keep expressions
short and aggregate server-side. The apiserver returns the Prometheus JSON
envelope (`{"status":"success","data":{...}}`) verbatim — pipe through `jq`
to extract; sample values are JSON strings, so `tonumber` before comparing.

## Reporting

Relay the metric values you find. Triage failures by their distinct signatures:

- **`Forbidden` ... `cannot get resource "services/proxy"`** → the
  `ai-agent-proxy` grant is missing or was removed (see *RBAC* above), not a bad
  name. Verify with `auth can-i get services --subresource=proxy -n <ns>` and
  have the user re-apply the `ai-agent-proxy` ClusterRole/binding. No service
  name will fix this.
- **`503`/`ServiceUnavailable`** → the service or port name is wrong — list
  services with `kubectl-readonly -n <ns> get svc` and retry against the right
  one (and, for Thanos history, confirm `thanos-query*` actually exists).
