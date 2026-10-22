# Azure deployment guide

This folder sets up the Azure side of the project: a small **API** container that receives GitHub webhooks and puts review jobs on a queue, and a separate **worker** container that does the slow review work. Both run the same image on **Azure Container Apps**. A GitHub Actions pipeline builds the image, pushes it to **Azure Container Registry (ACR)** and updates both apps.

The only file you run is `infra/azure-setup.sh`. The pipeline lives in `.github/workflows/` and is owned separately.

## Architecture

```text
GitHub (pull_request webhook)
        |  HTTPS POST /webhook/github
        v
+------------------------------+        +-------------------------------+
| API Container App            | claim  | Postgres (you create it)      |
| ca-code-reviewer             |------->|  reviews table: one row per   |
| - verify HMAC signature      |        |  (repo, target, head sha)     |
| - claim dedupe row           |        +-------------------------------+
| - enqueue job, return 202    | enqueue| Azure Storage account         |
| 0.5 vCPU / 1Gi, min 1        |------->|  Queue  review-jobs           |
+------------------------------+        |  Queue  review-jobs-poison    |
                                        +---------------+---------------+
                                                        | receive (visibility timeout 900s)
                                                        v
                                        +-------------------------------+
                                        | Worker Container App          |
                                        | ca-code-reviewer-worker       |
                                        | python -m app.worker          |
                                        | - clone + ingest repo at start|
                                        | - run review graph (LLMs)     |
                                        | - upsert PR comment on GitHub |
                                        | - delete message on success   |
                                        | 2 vCPU / 4Gi, no ingress      |
                                        +-------------------------------+
```

A job is deleted from the queue only after it succeeded. If the worker crashes or the review fails, the message reappears after the visibility timeout and is retried; after `MAX_ATTEMPTS` (3) failures it moves to `review-jobs-poison`. Posting the comment is an upsert, so a retry edits the comment instead of duplicating it. Reasoning: see `docs/DECISIONS.md`, "Why a durable queue + separate worker for reviews".

## What gets created and why

| Resource | What it is | Why we need it |
|---|---|---|
| Resource group | A folder for related Azure resources | Everything lives in one place, so you can delete it all with one command |
| Container Registry (ACR, Basic) | A private store for Docker images | The pipeline pushes `autonomous-code-reviewer:<git-sha>` here; Azure pulls from here |
| Log Analytics workspace | Log storage and search | Container Apps sends both apps' stdout/stderr here |
| Container Apps environment | The shared hosting boundary for apps | Networking and logging; required before creating apps |
| User-assigned managed identity | A passwordless identity, shared by both apps | `AcrPull` on the ACR (image pulls), `Storage Queue Data Contributor` on the storage account (queue access). No keys or connection strings in the apps |
| Storage account (StorageV2, Standard_LRS) | Holds the queues | Queue `review-jobs`, poison queue `review-jobs-poison`. Shared-key access is disabled and TLS 1.2 is the minimum, so only Entra identities can reach it |
| API Container App | The webhook endpoint | Public HTTPS URL, health probes, revisions for rollback |
| Worker Container App | Runs the reviews | Same image, started with `python -m app.worker`; no ingress, no public URL |
| Entra app registration + federated credential | A trust rule: "tokens from GitHub Actions for this repo's `main` branch may act as this identity" | Lets the pipeline log in to Azure with OIDC, with no stored password |

### Settings chosen and why

**API (`CONTAINER_APP_NAME`, default `ca-code-reviewer`)**

- **0.5 vCPU / 1 GiB.** The API only verifies the signature, inserts one `reviews` row in Postgres, enqueues and returns 202. It no longer clones or ingests anything, so it starts in seconds and needs little memory.
- **Min replicas = 1 (recommended), max 2.** GitHub gives up on a webhook after about 10 seconds. Scale-to-zero is now technically possible because startup is fast, but a cold start still includes allocating a replica, pulling the image and starting Python plus the Azure SDKs. That can take several seconds, close enough to the limit to fail some deliveries, and a failed delivery means a PR that silently never gets reviewed. At 0.5 vCPU / 1Gi the always-on cost is small, so we keep 1. The API is stateless (dedupe is in Postgres, the queue in Storage), so a second replica is safe.
- **Startup probe** is short (5s initial delay, 10s period, 6 failures = about 65s) because the app listens almost immediately. Liveness and readiness use `/health`.
- Env vars: `JOBS_BACKEND=azure`, `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_CLIENT_ID` (see below), `ALLOWED_REPOS` (export it before running the script), and secrets `GITHUB_WEBHOOK_SECRET` and `GITHUB_TOKEN` (the webhook asks GitHub whether the PR author is a collaborator). It no longer needs the LLM keys or `TARGET_REPO_URL`; re-running the script removes those stale secrets from the API app.

**Worker (`WORKER_APP_NAME`, default `ca-code-reviewer-worker`)**

- **2 vCPU / 4 GiB** (a valid Consumption combination). Sized for the removed semantic retrieval (torch reranker, Chroma); the worker now only holds the parsed repo and its call graph, so lower it once real usage (metrics) confirms.
- **Command override.** The container runs `python -m app.worker` instead of the image's default (uvicorn). The script sets `command: ["python"]` and `args: ["-m", "app.worker"]` in YAML. Later `az containerapp update --image ...` keeps this command; only the image changes.
- **No ingress and no probes.** It has no HTTP port. If the process dies, Container Apps restarts it and the unfinished message reappears on the queue.
- **Min = max = 1 replica.** The call graph is built in memory when the worker starts (clone, parse). Each extra replica would repeat that work (more vCPU cost). Scaling out is correct but costly; do it only if the queue backs up. A KEDA queue-length scale rule is included, commented out, at the end of step 8 in the script. Do not set min replicas to 0: every cold start would re-clone and re-parse before the first job.
- **Termination grace period 600s.** On a new revision or restart, Container Apps sends SIGTERM and waits up to this long. The worker finishes its current job before exiting. As far as I know 600 seconds is the platform maximum (default 30); verify with the command below. If a review is killed mid-way, nothing is lost: it is retried after the visibility timeout.
- `REVIEW_VISIBILITY_TIMEOUT_S=900` must be longer than the longest review. If it is shorter, a second worker (or the same one after a restart) could receive the same message while the first is still running.
- Secrets: `TARGET_REPO_URL`, `GROQ_API_KEY`, `GITHUB_TOKEN`, optional `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (plus plain `LANGFUSE_HOST`).
- **No persistent volume.** The cloned repo is on the container's temporary disk. Every worker restart or new revision re-clones and re-parses it (no API quota). Queued jobs just wait meanwhile. An Azure Files volume would avoid that but is not set up yet.

**Identity.** Because both apps use a *user-assigned* identity, the Azure SDK's `DefaultAzureCredential` cannot guess which identity to use. The script sets `AZURE_CLIENT_ID` (the identity's client id) on both apps so it picks the right one.

**Storage.** `allow-shared-key-access` is false, so there are no account keys to leak. Consequences: use `--auth-mode login` with `az storage` commands, and the portal's data browser needs your own data-role assignment (see "Inspecting the queues"). Local development with Azurite uses a connection string and never touches this account. The queues are created through the Azure Resource Manager API (`az rest`), which needs only Contributor on the resource group, not a data role. The app also creates them on start if they are missing.

**Postgres (create it before running the script).** The API and the worker need `DATABASE_URL` for the `reviews` table (the API dedupes on it, the worker records each review's status), which replaced the Azure Table dedupe (`docs/DECISIONS.md`). The script does not create a Postgres server: create one, then `export DATABASE_URL='postgresql://user:password@host:5432/dbname?sslmode=require'` before running it. The script stops at once if it is missing, and stores it as a secret on both apps with `DEDUPE_BACKEND=postgres`; the API applies the migrations at startup. There is deliberately no fallback to in-memory dedupe, because it would let two replicas review the same commit twice. Plain Postgres is enough (no migration needs pgvector). Where to host it (Azure Database for PostgreSQL Flexible Server, tier and cost) is an open decision in `docs/ARCHITECTURE.md`.

## Prerequisites

1. An Azure subscription (a free account works for trying this out, but check quota for 2 vCPU plus 0.5 vCPU).
2. **Azure CLI**: install from <https://learn.microsoft.com/cli/azure/install-azure-cli>, or use **Azure Cloud Shell** (the `>_` icon in the Azure portal, choose Bash).
3. Log in and check the subscription:
   ```bash
   az login
   az account show --query "{name:name, id:id}" -o table
   ```
   If it is the wrong subscription: `az account set --subscription "<name or id>"`.
4. Make sure the Container Apps extension is current:
   ```bash
   az extension add --name containerapp --upgrade
   ```
5. Your Entra account needs permission to create app registrations (default for most accounts) and to create role assignments (Owner or User Access Administrator on the resource group/subscription).
6. **Pick globally unique names**:
   - ACR: 5 to 50 characters, lowercase letters and digits only. Check: `az acr check-name --name <yourname> -o table`
   - Storage account: 3 to 24 characters, lowercase letters and digits only. Check: `az storage account check-name --name <yourname> -o table`
   Expected: `NameAvailable` is `True`.
7. Pick a region (`az account list-locations -o table`). The script defaults to `westeurope`; choose one near you that offers Container Apps and that your subscription allows.

## Running the script

Put the secrets in your shell (they are only read from the environment, never written to a file). In Git Bash or Cloud Shell:

```bash
# API
export GITHUB_WEBHOOK_SECRET='choose-a-long-random-string'
export ALLOWED_REPOS='owner/repo'   # the webhook ignores every other repo
# Worker
export TARGET_REPO_URL='https://github.com/owner/repo-to-review.git'
export GROQ_API_KEY='...'
export GITHUB_TOKEN='...'
# optional (worker)
export LANGFUSE_PUBLIC_KEY='...'
export LANGFUSE_SECRET_KEY='...'
export LANGFUSE_HOST='https://cloud.langfuse.com'   # or your self-hosted Langfuse URL
```

If `TARGET_REPO_URL` is a private repo, the clone needs credentials, and `clone_repository` in `rag/ingestion.py` has no credential handling yet: use a public repo for now.

Then run the script, pausing between steps so you can read the output:

```bash
ACR_NAME=<acrname> STORAGE_ACCOUNT_NAME=<storagename> GITHUB_REPO=<owner>/<repo> PAUSE=1 bash infra/azure-setup.sh
```

You can also override `LOCATION`, `RESOURCE_GROUP`, `ENV_NAME`, `APP_NAME`, `WORKER_APP_NAME` the same way, or edit the CONFIG block. Re-running is safe; existing resources are reused and both apps are updated.

Steps in the script:

1. Register providers (first time on a subscription this can take a few minutes).
2. Resource group.
3. ACR (Basic, admin user off).
4. Log Analytics + Container Apps environment.
5. Managed identity + `AcrPull`.
6. Storage account, queues, and the queue data role for the identity (then waits 60 seconds for roles to take effect).
7. API Container App: placeholder image, ingress on port 8000, secrets, env vars, probes (generated YAML, deleted afterwards; it contains no secret values).
8. Worker Container App: placeholder image, no ingress, secrets, env vars, command override and termination grace period (generated YAML).
9. GitHub OIDC: app registration, service principal, federated credential for `repo:OWNER/REPO:ref:refs/heads/main`, `Contributor` on the resource group (covers updating both apps), `AcrPush` on the ACR.

**Expected quirk:** the placeholder image listens on port 80 and has no `python -m app.worker`, so the first revision of both apps shows as unhealthy or failed until the pipeline deploys the real image. This goes away after the first successful deploy.

## GitHub secrets and variables

At the end the script prints these values. Add them in the repo under **Settings, Secrets and variables, Actions**.

**Secrets** (tab "Secrets"):

| Name | Value comes from |
|---|---|
| `AZURE_CLIENT_ID` | Script output (the Entra OIDC app's client id; not the managed identity's) |
| `AZURE_TENANT_ID` | Script output |
| `AZURE_SUBSCRIPTION_ID` | Script output |

**Variables** (tab "Variables"):

| Name | Value comes from |
|---|---|
| `AZURE_RESOURCE_GROUP` | `RESOURCE_GROUP` (default `rg-code-reviewer`) |
| `ACR_NAME` | The ACR name you chose |
| `CONTAINER_APP_NAME` | API app, `APP_NAME` (default `ca-code-reviewer`) |
| `WORKER_APP_NAME` | **New.** Worker app (default `ca-code-reviewer-worker`) |

There is no client secret or password anywhere. The app's own API keys are stored once as Container Apps secrets by the script, not in GitHub.

The federated credential only trusts workflows running on the **main branch**. If the workflow uses a GitHub `environment:` or runs on pull requests, add a matching credential (re-run the script with `GITHUB_ENVIRONMENT=<name>` for environments); otherwise `azure/login` fails with an `AADSTS70021` "no matching federated identity" error.

## Point the GitHub webhook at the app

1. Get the URL:
   ```bash
   az containerapp show -n <APP_NAME> -g <RESOURCE_GROUP> --query properties.configuration.ingress.fqdn -o tsv
   ```
2. In the target repo: **Settings, Webhooks, Add webhook**.
   - Payload URL: `https://<fqdn>/webhook/github`
   - Content type: `application/json`
   - Secret: the same value as `GITHUB_WEBHOOK_SECRET`
   - Events: "Let me select", then **Pull requests** only.
3. After the pipeline has deployed, check `https://<fqdn>/health`. The webhook's "Recent Deliveries" tab shows the response code per delivery; a successful delivery returns 202 (queued). The review itself appears minutes later, once the worker has finished its startup ingestion.

## Logs

API:

```bash
az containerapp logs show -n <APP_NAME> -g <RESOURCE_GROUP> --follow
```

Worker (startup ingestion, "Worker started", "Review done", failures with stack traces):

```bash
az containerapp logs show -n <WORKER_APP_NAME> -g <RESOURCE_GROUP> --follow
```

Add `--type system` to see platform events (image pull, restarts, probe failures). For history and queries, open the Log Analytics workspace and run, for example, `ContainerAppConsoleLogs_CL | where ContainerAppName_s == "<WORKER_APP_NAME>" | order by TimeGenerated desc`. If you set the `LANGFUSE_*` variables, LLM traces also show up in Langfuse.

## Inspecting the queues and replaying a poison message

Because shared-key access is off, your own account needs a data role once (assignment can take a minute to apply):

```bash
SA_ID=$(az storage account show -n <STORAGE_ACCOUNT_NAME> -g <RESOURCE_GROUP> --query id -o tsv)
ME=$(az ad signed-in-user show --query id -o tsv)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Storage Queue Data Contributor" --scope "$SA_ID"
```

How many messages are waiting (approximate count):

```bash
az storage queue stats -n review-jobs --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login
```

If your CLI version has no `stats`, use `az storage message peek` below.

Look at jobs without hiding them (`peek`), in the main and poison queues:

```bash
az storage message peek -q review-jobs        --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login --num-messages 10
az storage message peek -q review-jobs-poison --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login --num-messages 10
```

Messages hold plain JSON (not base64) with the repo, PR number and head sha. Messages the worker could not even parse also end up in the poison queue.

Replay one poison message: first find and fix the cause in the worker logs (otherwise it fails again), then move it back:

```bash
# 1. take the message (hides it for 5 minutes; note id, pop_receipt and content in the output)
az storage message get -q review-jobs-poison --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login
# 2. put the same content on the main queue
az storage message put -q review-jobs --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login --content '<content copied from step 1>'
# 3. delete it from the poison queue
az storage message delete -q review-jobs-poison --account-name <STORAGE_ACCOUNT_NAME> --auth-mode login --id <id> --pop-receipt <pop_receipt>
```

The dedupe row for that PR and head sha already exists, which is fine: replaying puts the job straight on the queue and does not go through the webhook dedupe. To retry a PR through the webhook instead, push a new commit.

## Rolling back

Each deploy creates a new revision of each app. To go back for an app:

```bash
az containerapp revision list -n <APP_NAME> -g <RESOURCE_GROUP> -o table
az containerapp revision activate -n <APP_NAME> -g <RESOURCE_GROUP> --revision <old-revision-name>
```

In single-revision mode (the default), the simplest and most reliable rollback is to redeploy a previous image tag to **both** apps (they share one image, so keep them on the same tag):

```bash
az containerapp update -n <APP_NAME>        -g <RESOURCE_GROUP> --image <ACR_NAME>.azurecr.io/autonomous-code-reviewer:<old-git-sha>
az containerapp update -n <WORKER_APP_NAME> -g <RESOURCE_GROUP> --image <ACR_NAME>.azurecr.io/autonomous-code-reviewer:<old-git-sha>
```

Or re-run the pipeline on the old commit. Images are tagged by git SHA, so old versions stay in the registry. A job format change between versions can leave old messages the new worker cannot parse; they end up in the poison queue.

## Cost notes

Prices change and depend on region, so none are quoted here. Use the **Azure pricing calculator** (<https://azure.microsoft.com/pricing/calculator/>) and enter:

- Container Apps, Consumption plan, two always-on apps: API (0.5 vCPU, 1 GiB, 1 replica) and worker (2 vCPU, 4 GiB, 1 replica). The worker is the main cost. Idle replicas are billed at a reduced rate, but not zero.
- Storage account (Standard_LRS): billed per GB stored and per operation. The worker polls the queue with a back-off up to 10 seconds, so operations are modest but not zero when idle. Queue data here is tiny.
- Container Registry: Basic tier.
- Log Analytics: pay per GB ingested; the logs are small, but check "Daily cap" if worried.

Outside Azure: Groq usage. Each retried job repeats its LLM calls; a worker restart only re-parses the repo, with no LLM call. Set a budget alert under Cost Management, Budgets.

## Tear everything down

This deletes the resource group and everything in it (apps, registry, storage with its queued jobs, logs, identity):

```bash
az group delete --name <RESOURCE_GROUP> --yes --no-wait
```

The Entra app registration is not inside the resource group; remove it separately:

```bash
az ad app delete --id <AZURE_CLIENT_ID>
```

Also delete the GitHub secrets/variables and the webhook.

## Narrower permissions for the pipeline (optional)

The script gives the pipeline `Contributor` on the resource group, which is simple but broad: it can change anything in that group. A narrower setup is `Contributor` on the two Container App resources only, plus `Managed Identity Operator` on the managed identity (needed because the apps use a user-assigned identity), plus `AcrPush` on the ACR. It is more to maintain; for a single-purpose resource group, the broad role is usually acceptable.
