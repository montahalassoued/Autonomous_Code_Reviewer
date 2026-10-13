# Azure deployment guide

This folder sets up the Azure side of the project: one container (the FastAPI reviewer) running on **Azure Container Apps**, fed by a GitHub Actions pipeline that builds the image, pushes it to **Azure Container Registry (ACR)** and updates the app.

The only file you run is `infra/azure-setup.sh`. The pipeline lives in `.github/workflows/` and is owned separately.

## What gets created and why

| Resource | What it is | Why we need it |
|---|---|---|
| Resource group | A folder for related Azure resources | Everything lives in one place, so you can delete it all with one command |
| Container Registry (ACR, Basic) | A private store for Docker images | The pipeline pushes `autonomous-code-reviewer:<git-sha>` here; Azure pulls from here |
| Log Analytics workspace | Log storage and search | Container Apps sends the app's stdout/stderr here |
| Container Apps environment | The shared hosting boundary for apps | Gives the app networking and logging; required before creating an app |
| User-assigned managed identity | A passwordless identity for the app | Has the `AcrPull` role on the ACR, so the app can pull images without the ACR admin user or any password |
| Container App | Runs our one container | Public HTTPS URL, secrets, health probes, revisions for rollback |
| Entra app registration + federated credential | A trust rule: "tokens from GitHub Actions for this repo's `main` branch may act as this identity" | Lets the pipeline log in to Azure with OIDC, with no stored password |

### Settings chosen for this app

- **2 vCPU / 4 GiB.** 2.0 vCPU with 4.0Gi is a valid Consumption-plan combination (memory is always 2x the vCPU on that plan). The app loads a torch reranker model, Chroma and an in-memory knowledge graph, so 4 GiB gives headroom over the 2 GiB minimum we estimated. If real usage (see Logs/Metrics) shows far less, you can lower it.
- **Min replicas = 1.** GitHub gives up on a webhook after about 10 seconds. With scale-to-zero the app would have to clone the repo, ingest it and load the model on the first webhook, which takes minutes, so deliveries would fail. Min 1 keeps it always running. The trade-off is that you pay for the allocated CPU/memory continuously (at a reduced "idle" rate when there is no traffic, but not zero). Scale-to-zero would cost almost nothing when idle but is not workable for webhooks here.
- **Max replicas = 1.** The ingested index and graph live in each container's memory and local disk. A second replica would clone and embed the repo again (extra Voyage/Gemini usage) and nothing coordinates the replicas. Keep 1 until state is moved to shared storage.
- **No persistent volume.** Chroma data and the cloned repo are on the container's temporary disk. Every restart or new revision re-clones and re-ingests (slow, and uses API quota). An Azure Files volume would keep the data, but adds cost and setup, and the app would need to reuse existing data at startup (it currently re-ingests regardless). Not done yet.
- **Startup probe up to about 11 minutes.** The app only starts listening on port 8000 after clone + ingest + model load finish (this work runs inside the FastAPI lifespan). Until then the probe sees "connection refused", which is normal. Liveness and readiness probes only start after the startup probe succeeds. If ingestion of your repo takes longer, raise `periodSeconds` in the YAML section of the script (limits: period max 240s, failureThreshold max 10).

## Prerequisites

1. An Azure subscription (a free account works for trying this out, but check quota for 2 vCPU).
2. **Azure CLI**: install from <https://learn.microsoft.com/cli/azure/install-azure-cli>, or just use **Azure Cloud Shell** (the `>_` icon in the Azure portal, choose Bash), which already has it.
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
5. Your Entra account needs permission to create app registrations (default for most accounts) and to create role assignments on the subscription (Owner or User Access Administrator on the resource group/subscription).
6. **Pick a globally unique ACR name**: 5 to 50 characters, lowercase letters and digits only, no dashes (for example `acrcodereviewer` plus a few digits). Check it:
   ```bash
   az acr check-name --name <yourname> -o table
   ```
   Expected: `NameAvailable` is `True`.
7. Pick a region (`az account list-locations -o table`). The script defaults to `westeurope`; choose one near you that offers Container Apps and that your subscription allows.

## Running the script

Put the secrets in your shell (they are only read from the environment, never written to a file). In Git Bash or Cloud Shell:

```bash
export TARGET_REPO_URL='https://github.com/owner/repo-to-review.git'
export GROQ_API_KEY='...'
export GOOGLE_API_KEY='...'
export VOYAGE_API_KEY='...'
export GITHUB_TOKEN='...'
export GITHUB_WEBHOOK_SECRET='choose-a-long-random-string'
# optional
export LANGFUSE_PUBLIC_KEY='...'
export LANGFUSE_SECRET_KEY='...'
export LANGFUSE_HOST='https://cloud.langfuse.com'   # or your self-hosted Langfuse URL
```

If `TARGET_REPO_URL` is a private repo, the clone needs credentials; check how `clone_repository` in `rag/ingestion.py` handles that before relying on it.

Then run the script, pausing between steps so you can read the output:

```bash
ACR_NAME=<yourname> GITHUB_REPO=<owner>/<repo> PAUSE=1 bash infra/azure-setup.sh
```

You can also override `LOCATION`, `RESOURCE_GROUP`, `ENV_NAME`, `APP_NAME` the same way, or edit the CONFIG block at the top. Re-running is safe; existing resources are reused and the app is updated.

Steps in the script:

1. Register providers (first time on a subscription this can take a few minutes).
2. Resource group.
3. ACR (Basic, admin user off).
4. Log Analytics + Container Apps environment.
5. Managed identity + `AcrPull` (then waits 60 seconds for the role to take effect).
6. Container App with a public placeholder image, ingress on port 8000, secrets and `secretref` env vars.
7. Health probes applied via a generated YAML (deleted afterwards; it contains no secret values).
8. GitHub OIDC: app registration, service principal, federated credential for `repo:OWNER/REPO:ref:refs/heads/main`, `Contributor` on the resource group, `AcrPush` on the ACR.

**Expected quirk:** the placeholder image listens on port 80 while ingress targets 8000, so the first revision shows as unhealthy or failed until the pipeline deploys the real image. This is expected and goes away after the first successful deploy.

## GitHub secrets and variables

At the end the script prints these values. Add them in the repo under **Settings, Secrets and variables, Actions**.

**Secrets** (tab "Secrets"):

| Name | Value comes from |
|---|---|
| `AZURE_CLIENT_ID` | Script output (the Entra app's application/client id) |
| `AZURE_TENANT_ID` | Script output (`az account show --query tenantId -o tsv`) |
| `AZURE_SUBSCRIPTION_ID` | Script output (`az account show --query id -o tsv`) |

**Variables** (tab "Variables"):

| Name | Value comes from |
|---|---|
| `AZURE_RESOURCE_GROUP` | `RESOURCE_GROUP` in the script (default `rg-code-reviewer`) |
| `ACR_NAME` | The ACR name you chose |
| `CONTAINER_APP_NAME` | `APP_NAME` in the script (default `ca-code-reviewer`) |

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
3. After the pipeline has deployed, check `https://<fqdn>/health`. It returns `{"status":"ok"}` once ingestion finished. The webhook's "Recent Deliveries" tab shows the response code for each delivery.

## Logs

Live stream:

```bash
az containerapp logs show -n <APP_NAME> -g <RESOURCE_GROUP> --follow
```

Add `--type system` to see platform events (image pull, probe failures). For history and queries, open the Log Analytics workspace in the portal and run, for example, `ContainerAppConsoleLogs_CL | order by TimeGenerated desc`. If you set the `LANGFUSE_*` variables, LLM traces also show up in Langfuse.

## Rolling back

Each deploy creates a new revision. To go back:

```bash
az containerapp revision list -n <APP_NAME> -g <RESOURCE_GROUP> -o table
az containerapp revision activate -n <APP_NAME> -g <RESOURCE_GROUP> --revision <old-revision-name>
```

If the app is in single-revision mode (the default), the simplest and most reliable rollback is to redeploy a previous image tag:

```bash
az containerapp update -n <APP_NAME> -g <RESOURCE_GROUP> \
  --image <ACR_NAME>.azurecr.io/autonomous-code-reviewer:<old-git-sha>
```

Or re-run the pipeline on the old commit. Because images are tagged by git SHA, old versions stay in the registry.

## Cost notes

Prices change and depend on region, so none are quoted here. Use the **Azure pricing calculator** (<https://azure.microsoft.com/pricing/calculator/>) and enter:

- Container Apps, Consumption plan: 2 vCPU, 4 GiB, 1 replica running all month (min replicas 1).
- Container Registry: Basic tier.
- Log Analytics: pay per GB ingested; the app's logs are small, but check "Daily cap" if worried.

Other costs live outside Azure: Groq, Gemini and Voyage usage. Every restart re-ingests the repo, which re-runs the Gemini chunk contextualization and Voyage embeddings. Set a budget alert under Cost Management, Budgets.

## Tear everything down

This deletes the resource group and everything in it (app, registry, logs, identity):

```bash
az group delete --name <RESOURCE_GROUP> --yes --no-wait
```

The Entra app registration is not inside the resource group; remove it separately:

```bash
az ad app delete --id <AZURE_CLIENT_ID>
```

Also delete the GitHub secrets/variables and the webhook.

## Narrower permissions for the pipeline (optional)

The script gives the pipeline `Contributor` on the resource group, which is simple but broad: it can change anything in that group. A narrower setup is `Contributor` only on the Container App resource plus `Managed Identity Operator` on the managed identity (needed because the app uses a user-assigned identity), plus `AcrPush` on the ACR. It is more to maintain; for a single-purpose resource group, the broad role is usually acceptable.
