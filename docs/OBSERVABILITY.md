# Observability

Two complementary views of the reviewer:

| | What it answers | Where |
|---|---|---|
| **Metrics** (Prometheus + Grafana) | How many, how fast, how often it fails: reviews per hour, LLM latency, tokens, retries | `review/review_metrics.py`, `/metrics`, `infra/monitoring/` |
| **Traces** (Langfuse) | What happened in one review: every agent, LLM call and tool call, with inputs and outputs | `review/review_tracing.py` (on when the Langfuse keys are set) |

## The metrics

The API serves `/metrics` on its own port (8000); the worker serves it on `WORKER_METRICS_PORT` (default 9100). The Nginx gateway forwards only `/webhook/github` and `/health`, so neither is public.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `reviewer_reviews_total` | counter | `outcome` | posted, partial (an agent failed), unreviewable, skipped (closed or superseded), failed (will retry), dead_lettered |
| `reviewer_review_duration_seconds` | histogram | | from picking up a job to the posted review |
| `reviewer_posted_findings_total` | counter | `agent`, `severity` | findings posted on pull requests |
| `reviewer_agent_failures_total` | counter | `agent` | agents left out of a review |
| `reviewer_llm_calls_total` | counter | `provider`, `model`, `outcome` | LLM calls after retries: ok or error |
| `reviewer_llm_call_duration_seconds` | histogram | `provider`, `model` | one successful call |
| `reviewer_llm_retries_total` | counter | `provider`, `model`, `reason` | retried calls, by HTTP status (429, 503) or error type |
| `reviewer_llm_tokens_total` | counter | `provider`, `model`, `direction` | tokens in and out, the quota that runs out |
| `reviewer_webhook_deliveries_total` | counter | `result` | queued, duplicate, ignored, or `http_<status>` when refused |

## Run the dashboard locally

`docker compose up --build -d` also starts Prometheus (http://localhost:9090) and Grafana (http://localhost:3000, user `admin`, password `GRAFANA_ADMIN_PASSWORD` from `.env`, default `admin`). Both listen on localhost only. The dashboard **Reviewer → Code reviewer: reviews and LLM calls** is provisioned from `infra/monitoring/grafana/dashboards/reviewer_overview.json`.

Useful queries in Prometheus:

```promql
sum by (outcome) (increase(reviewer_reviews_total[1h]))                                   # reviews per hour
histogram_quantile(0.95, sum by (le, model) (rate(reviewer_llm_call_duration_seconds_bucket[15m])))   # p95 LLM latency
sum by (model) (increase(reviewer_llm_tokens_total[24h]))                                 # tokens per day, against the daily quota
sum by (reason) (increase(reviewer_llm_retries_total[1h]))                                # 429s mean the per-minute limit is hit
```
