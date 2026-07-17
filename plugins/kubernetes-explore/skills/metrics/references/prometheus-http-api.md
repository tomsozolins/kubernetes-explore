# Prometheus HTTP API via the apiserver proxy

Verified against prometheus.io, kubernetes.io, and thanos.io. All paths below
are appended to the proxy prefix
`/api/v1/namespaces/<ns>/services/<svc>:<port>/proxy` and run with
`kubectl-readonly get --raw '<full-path>'`.

## Proxy URL forms (apiserver)

Documented scheme: `/api/v1/namespaces/<ns>/services/[https:]<service>[:port]/proxy/...`

- `<service>` — default/unnamed port over http
- `<service>:<port>` — port by **name or number** (both documented)
- `https:<service>:` — https on the default port (**trailing colon required**)
- `https:<service>:<port>` — https on a named/numbered port

`--raw` passes the path verbatim, so the query string must be URL-encoded —
and `kubectl get --raw` is **GET-only**: the API's documented POST fallback
for very long queries isn't available through this route, so keep
expressions short (aggregate server-side) instead.

Source: <https://kubernetes.io/docs/tasks/access-application-cluster/access-cluster-services/>

## Endpoints

Source: <https://prometheus.io/docs/prometheus/latest/querying/api/>

### `/api/v1/query` — instant query

| Param | Notes |
|---|---|
| `query` | PromQL, required |
| `time` | evaluation timestamp; defaults to server "now" |
| `timeout` | capped by the server's `-query.timeout` |
| `limit` | max returned series; `0` = disabled |

`data.resultType` is `vector` (usual), `scalar`, `string`, or `matrix`;
vector results are `{"metric": {…labels}, "value": [<unix_time>, "<value>"]}`.
Sample values are JSON **strings**, not numbers — `jq` accordingly
(`.value[1] | tonumber`).

### `/api/v1/query_range` — range query

| Param | Notes |
|---|---|
| `query` | required |
| `start`, `end` | required, inclusive |
| `step` | resolution step, required |
| `timeout`, `limit` | as above |

Always returns `resultType: matrix` (`values` array per series). The server
enforces a cap of ~11,000 points per timeseries — not documented, but it's
the runtime error `exceeded maximum resolution of 11,000 points per
timeseries. Try decreasing the query resolution (?step=XX)`. For this skill's
purposes the practical rule is stricter anyway: keep `step` coarse and the
window short, or you're dumping series into context.

### Discovery endpoints

| Path | Params | Returns |
|---|---|---|
| `/api/v1/series` | `match[]` (required, repeatable), `start`, `end`, `limit` | label sets, no values |
| `/api/v1/labels` | `match[]`, `start`, `end`, `limit` | label names |
| `/api/v1/label/<name>/values` | `match[]`, `start`, `end`, `limit` | label values |

`limit` is documented on all three (`0` = disabled) — use it.

### Response envelope

```json
{"status": "success|error", "data": …,
 "errorType": "…", "error": "…",      // only on error
 "warnings": ["…"], "infos": ["…"]}   // only when present
```

HTTP 2xx + `status: success` is the success signal; `warnings` can accompany
valid data. Timestamps in: RFC3339 or unix seconds (decimals allowed);
timestamps out: always unix seconds.

## PromQL composition rules

Source: <https://prometheus.io/docs/prometheus/latest/querying/basics/>,
<https://prometheus.io/docs/prometheus/latest/querying/functions/>

- Matchers: `=`, `!=`, `=~`, `!~`. **Regex matches are fully anchored** —
  `env=~"foo"` means `^foo$`; use `.*foo.*` for contains.
- A selector needs a metric name or ≥1 matcher that can't match empty:
  `{job=~".*"}` is invalid, `{job=~".+"}` is valid.
- Durations: `ms s m h d w y`, combinable (`1h30m`).
- `rate()` — per-second average over the range, counter-reset aware,
  extrapolates to window edges. Official rule: **always `rate()` first, then
  aggregate** (`sum(rate(x[5m])) by (...)`) — aggregating first hides counter
  resets. Best for alerts and slow-moving counters.
- `irate()` — last two points only; volatile fast-moving counters on graphs,
  not for alerting.
- `increase()` — `rate() × window`, human-readable totals; extrapolation
  means non-integer results are normal.
- `histogram_quantile(0.9, rate(<metric>_bucket[10m]))` — buckets need the
  `le` label and a `+Inf` bucket (else NaN). Quantiles are interpolated.
- The window passed to `rate()` must cover at least two samples; the common
  "≥4× scrape interval" sizing is community guidance, not official.

## Thanos Query deltas

Thanos Query "implements the Prometheus HTTP v1 API" (guaranteed compatible
with Prometheus 2.x API) — same endpoints and envelope, plus optional params:

| Param | Meaning |
|---|---|
| `dedup` | replica deduplication (default true when replica labels configured) |
| `max_source_resolution` | downsampling floor: `auto`, `0` (raw), `5m`, `1h` |
| `partial_response` | tolerate unavailable stores (follows `--query.partial-response`) |
| `storeMatch[]` | limit which stores are queried |

Source: <https://thanos.io/tip/components/query.md/>
