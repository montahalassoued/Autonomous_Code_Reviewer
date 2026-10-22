# API gateway (Nginx)

The only public entry point of the docker-compose stack. GitHub (or ngrok) talks to the gateway on `http://localhost:8000`, and the gateway forwards to the API on the compose network. The API is no longer published on your machine.

| File | Role |
|---|---|
| `nginx.conf` | The rules below, mounted as `/etc/nginx/conf.d/default.conf` |
| `allowlist/any.conf` | Every source may reach the webhook (default, needed behind ngrok) |
| `allowlist/github_hooks.conf` | Only GitHub's webhook IP ranges; written by the refresh script |
| `refresh_github_hooks_allowlist.py` | Rewrites `github_hooks.conf` from `https://api.github.com/meta` |

## What it enforces

| Rule | Value | Why |
|---|---|---|
| Routes | `POST /webhook/github`, `GET /health`; anything else 404, another method 403 | Nothing else should ever be reachable |
| Body size | 5 MiB, then 413 | Same cap as the API's `GITHUB_WEBHOOK_MAX_BODY_BYTES`; refused before Python sees it |
| Rate limit | 10 requests/s per IP, burst 20, then 429; 20 connections per IP | Floods stop at nginx; GitHub never sends that many |
| Timeouts | 10 s for headers, body and sending; 15 s for the API's answer | Slow clients can't hold connections; GitHub gives up after 10 s anyway |
| Source IPs | `GATEWAY_WEBHOOK_ALLOWLIST=any` or `github_hooks` | Optional extra gate on top of the HMAC signature |
| Headers | No nginx version, no `Server: uvicorn`, `X-Content-Type-Options: nosniff` | Less to fingerprint |

The API keeps its own body cap and signature check, so a request that bypasses the gateway is still refused.

## Only GitHub's IPs

Behind ngrok every request comes from the ngrok agent on your machine, so keep `any` locally. Use `github_hooks` only when the gateway faces the internet directly:

```bash
venv/Scripts/python.exe infra/gateway/refresh_github_hooks_allowlist.py   # writes allowlist/github_hooks.conf
# in .env: GATEWAY_WEBHOOK_ALLOWLIST=github_hooks
docker compose up -d gateway                                                # restart with the new list
```

GitHub changes the ranges from time to time: rerun the script, then restart the gateway. If `github_hooks.conf` doesn't exist yet, Docker mounts an empty directory instead and the gateway fails to start. On Azure the API's Container Apps ingress can apply the same ranges as IP restrictions, so this gateway is not deployed there.
