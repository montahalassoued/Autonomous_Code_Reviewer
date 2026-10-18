#!/usr/bin/env bash
# =============================================================================
# azure-setup.sh - one-time Azure setup for the Autonomous Code Reviewer.
#
# Architecture: GitHub webhook -> API Container App -> Azure Storage Queue
#               -> worker Container App (same image, runs `python -m app.worker`).
#
# What it creates (all in ONE resource group, so teardown is one command):
#   1. Resource providers registered (Microsoft.App, OperationalInsights,
#      ContainerRegistry, Storage)
#   2. Resource group
#   3. Azure Container Registry (Basic SKU, admin user DISABLED)
#   4. Log Analytics workspace + Container Apps environment
#   5. User-assigned managed identity with AcrPull on the registry
#   6. Storage account (queues review-jobs + review-jobs-poison) + the queue
#      data role for the managed identity
#   NOT YET: the Postgres server the apps need for DATABASE_URL (dedupe and
#   code index). See infra/README.md, "Postgres (not provisioned yet)".
#   7. API Container App   (external ingress, 0.5 vCPU / 1Gi, min=max=1)
#   8. Worker Container App (no ingress, 2 vCPU / 4Gi, min=max=1)
#   9. GitHub Actions OIDC: Entra app + service principal + federated credential
#      + Contributor (resource group scope) + AcrPush (registry scope)
#
# Usage (Azure Cloud Shell or Git Bash, after `az login`):
#   1. Choose ACR_NAME, STORAGE_ACCOUNT_NAME, GITHUB_REPO (env vars or edit CONFIG).
#   2. Export the app secrets in your shell (never put them in this file):
#        # API needs (the token checks that PR authors are collaborators):
#        export GITHUB_WEBHOOK_SECRET='...'
#        export GITHUB_TOKEN='...'
#        export ALLOWED_REPOS='owner/repo'          # repos the webhook accepts, comma-separated
#        # Worker needs:
#        export TARGET_REPO_URL='https://github.com/owner/repo.git'
#        export GROQ_API_KEY='...'
#        export GITHUB_TOKEN='...'
#        # optional (Langfuse tracing, worker only):
#        export LANGFUSE_PUBLIC_KEY='...' LANGFUSE_SECRET_KEY='...'
#   3. Run:   ACR_NAME=... STORAGE_ACCOUNT_NAME=... GITHUB_REPO=owner/repo \
#               bash infra/azure-setup.sh
#      Set PAUSE=1 to stop and wait for Enter between steps.
#
# Re-running is safe: every step checks whether the resource already exists
# and reuses it (create-or-update). Nothing is deleted, except that secrets the
# API no longer needs (from the old single-container layout) are removed from it.
# =============================================================================
set -euo pipefail

# Git Bash on Windows rewrites arguments that start with "/" (like Azure
# resource ids "/subscriptions/...") into Windows paths. This turns it off.
export MSYS_NO_PATHCONV=1

# ------------------------------- CONFIG --------------------------------------
LOCATION="${LOCATION:-westeurope}"                 # Azure region; see README for how to choose
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-code-reviewer}"
ACR_NAME="${ACR_NAME:-}"                           # REQUIRED: globally unique, 5-50 chars, lowercase letters+digits only
STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME:-}"   # REQUIRED: globally unique, 3-24 chars, lowercase letters+digits only
ENV_NAME="${ENV_NAME:-cae-code-reviewer}"          # Container Apps environment
APP_NAME="${CONTAINER_APP_NAME:-${APP_NAME:-ca-code-reviewer}}"   # API Container App
WORKER_APP_NAME="${WORKER_APP_NAME:-ca-code-reviewer-worker}"     # Worker Container App
GITHUB_REPO="${GITHUB_REPO:-}"                     # REQUIRED: "owner/repo" that hosts the workflow
# Optional: if the workflow job uses a GitHub *environment* (environment: prod),
# set its name here and a second federated credential is created for it.
GITHUB_ENVIRONMENT="${GITHUB_ENVIRONMENT:-}"

LOG_WORKSPACE="${LOG_WORKSPACE:-log-code-reviewer}"
IDENTITY_NAME="${IDENTITY_NAME:-id-code-reviewer-acrpull}"
OIDC_APP_NAME="${OIDC_APP_NAME:-github-oidc-code-reviewer}"

# Queue names. They match the defaults in app/jobs/factory.py, so no env var
# is needed in the apps. If you change them here, also set REVIEW_QUEUE_NAME
# on both apps.
QUEUE_NAME="review-jobs"
POISON_QUEUE_NAME="review-jobs-poison"

# API sizing: it only verifies the webhook and enqueues, so it is small.
# 0.5 vCPU / 1.0Gi is a valid Consumption combination.
# Min 1 replica: GitHub gives up after ~10s, and a scale-from-zero start of even
# a light container (image pull + Python start + Azure SDK init) can take a
# similar time. Min 1 keeps the webhook reliable; the cost is small at this size.
API_CPU="0.5"
API_MEMORY="1.0Gi"
API_MIN_REPLICAS=1
API_MAX_REPLICAS=2   # stateless (state is in Storage), so it can scale out safely
TARGET_PORT=8000

# Worker sizing: kept from the semantic-retrieval days (torch reranker, Chroma); the worker now
# only parses the repo and builds a call graph, so lower it once metrics confirm.
# Min=max=1: the call graph is built in memory at worker start, so each extra replica
# would re-clone and re-parse the repo (more vCPU). Raising
# max later is safe (the queue hands each message to one worker), just costly.
WORKER_CPU="2.0"
WORKER_MEMORY="4.0Gi"
WORKER_MIN_REPLICAS=1
WORKER_MAX_REPLICAS=1
# SIGTERM handling: the worker finishes its current job before exiting. Container
# Apps allows up to 600 seconds here (default 30). A job that runs longer is
# killed and simply reappears on the queue after the visibility timeout.
WORKER_TERMINATION_GRACE_S=600
REVIEW_VISIBILITY_TIMEOUT_S=900   # must exceed the longest review
MAX_ATTEMPTS=3

PLACEHOLDER_IMAGE="mcr.microsoft.com/k8se/quickstart:latest"

# ------------------------------- HELPERS -------------------------------------
step() { echo; echo "=== $* ==="; }
pause() { if [ "${PAUSE:-0}" = "1" ]; then read -r -p "Press Enter to continue (Ctrl+C to stop)... "; fi; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ------------------------------- PRE-CHECKS ----------------------------------
[ -n "$ACR_NAME" ] || die "Set ACR_NAME (globally unique, lowercase letters/digits, 5-50 chars). Example: ACR_NAME=acrcodereviewer123 bash infra/azure-setup.sh"
[[ "$ACR_NAME" =~ ^[a-z0-9]{5,50}$ ]] || die "ACR_NAME must be 5-50 lowercase letters/digits only (no dashes)."
[ -n "$STORAGE_ACCOUNT_NAME" ] || die "Set STORAGE_ACCOUNT_NAME (globally unique, lowercase letters/digits, 3-24 chars)."
[[ "$STORAGE_ACCOUNT_NAME" =~ ^[a-z0-9]{3,24}$ ]] || die "STORAGE_ACCOUNT_NAME must be 3-24 lowercase letters/digits only (no dashes)."
[[ "$GITHUB_REPO" =~ ^[^/]+/[^/]+$ ]] || die "Set GITHUB_REPO as owner/repo (e.g. GITHUB_REPO=montahalassoued/autonomous-code-reviewer)."

# App secrets are read from YOUR shell environment, split per app. Required ones
# must be set, because Container Apps refuses empty secret values.
API_REQUIRED_SECRETS=(GITHUB_WEBHOOK_SECRET GITHUB_TOKEN)
WORKER_REQUIRED_SECRETS=(TARGET_REPO_URL GROQ_API_KEY GITHUB_TOKEN)
WORKER_OPTIONAL_SECRETS=(LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY)
missing=0
for v in "${API_REQUIRED_SECRETS[@]}" "${WORKER_REQUIRED_SECRETS[@]}"; do
  if [ -z "${!v:-}" ]; then echo "WARNING: required variable $v is empty in your shell." >&2; missing=1; fi
done
for v in "${WORKER_OPTIONAL_SECRETS[@]}"; do
  if [ -z "${!v:-}" ]; then echo "NOTE: optional variable $v is empty; it will be skipped."; fi
done
[ "$missing" = "0" ] || die "Export the missing variables listed above, then re-run."
[ -n "${ALLOWED_REPOS:-}" ] || die "Export ALLOWED_REPOS='owner/repo' (the webhook ignores every other repo)."

# Env var NAME -> Container Apps secret name (lowercase, dashes; secret names cannot contain '_').
secret_name() { echo "$1" | tr 'A-Z_' 'a-z-'; }

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "Using subscription $SUBSCRIPTION_ID (tenant $TENANT_ID)"
pause

# ------------------------------- STEP 1 --------------------------------------
step "1/9 Register resource providers (first-time subscriptions only; can take a few minutes)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.Storage; do
  az provider register --namespace "$ns" --wait
done
pause

# ------------------------------- STEP 2 --------------------------------------
step "2/9 Resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none
pause

# ------------------------------- STEP 3 --------------------------------------
step "3/9 Azure Container Registry (Basic, admin user disabled)"
if ! az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
    --sku Basic --admin-enabled false -o none
fi
ACR_ID="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
ACR_LOGIN_SERVER="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv)"
pause

# ------------------------------- STEP 4 --------------------------------------
step "4/9 Log Analytics workspace + Container Apps environment"
if ! az monitor log-analytics workspace show --workspace-name "$LOG_WORKSPACE" \
      --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az monitor log-analytics workspace create --workspace-name "$LOG_WORKSPACE" \
    --resource-group "$RESOURCE_GROUP" --location "$LOCATION" -o none
fi
LOG_ID="$(az monitor log-analytics workspace show --workspace-name "$LOG_WORKSPACE" \
  --resource-group "$RESOURCE_GROUP" --query customerId -o tsv)"
LOG_KEY="$(az monitor log-analytics workspace get-shared-keys --workspace-name "$LOG_WORKSPACE" \
  --resource-group "$RESOURCE_GROUP" --query primarySharedKey -o tsv)"

if ! az containerapp env show --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az containerapp env create --name "$ENV_NAME" --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --logs-workspace-id "$LOG_ID" --logs-workspace-key "$LOG_KEY" -o none
fi
unset LOG_KEY
pause

# ------------------------------- STEP 5 --------------------------------------
step "5/9 User-assigned managed identity (shared by both apps) + AcrPull on the registry"
az identity create --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" -o none
IDENTITY_ID="$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
IDENTITY_PRINCIPAL_ID="$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)"
# The app code uses DefaultAzureCredential; with a USER-assigned identity it
# needs this client id (AZURE_CLIENT_ID) to know which identity to use.
IDENTITY_CLIENT_ID="$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv)"

ROLE_CREATED=0
# Creates a role assignment for the managed identity only if it is missing.
assign_identity_role() {   # $1 = role, $2 = scope
  if [ -z "$(az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$2" --role "$1" \
        --query '[0].id' -o tsv)" ]; then
    az role assignment create --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal --role "$1" --scope "$2" -o none
    ROLE_CREATED=1
  fi
}
assign_identity_role "AcrPull" "$ACR_ID"
pause

# ------------------------------- STEP 6 --------------------------------------
step "6/9 Storage account (queues) and the queue data role for the identity"
# Shared-key access is OFF: both apps use the managed identity (Entra auth), so
# no account key can leak. Consequence: az storage commands must use
# --auth-mode login, and the Azure portal data browser needs your own data role.
# Local dev/Azurite uses a connection string; that never touches this account.
if ! az storage account show --name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az storage account create --name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" --kind StorageV2 --sku Standard_LRS \
    --min-tls-version TLS1_2 --allow-blob-public-access false \
    --allow-shared-key-access false -o none
fi
STORAGE_ID="$(az storage account show --name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"

# Pre-create the queues through ARM (control plane). This needs only
# Contributor/Owner on the resource group - the person running the script does
# NOT need data-plane roles. `az storage queue create` would need
# "Storage Queue Data Contributor" for you because shared keys are disabled.
# (The app also creates them on start if missing; pre-creating means the
# identity needs no create permission and any typo shows up here.)
ARM_BASE="https://management.azure.com${STORAGE_ID}"
for q in "$QUEUE_NAME" "$POISON_QUEUE_NAME"; do
  az rest --method put --url "${ARM_BASE}/queueServices/default/queues/${q}?api-version=2023-05-01" \
    --body '{}' -o none
done

assign_identity_role "Storage Queue Data Contributor" "$STORAGE_ID"
if [ "$ROLE_CREATED" = "1" ]; then
  echo "Waiting 60s for the role assignments to propagate before the apps start..."
  sleep 60
fi
pause

# ------------------------------- APP HELPERS ---------------------------------
# Globals filled by the caller before write_app_yaml:
#   YAML_SECRET_VARS  env var names whose value is a secretRef
#   YAML_PLAIN_ENV    "NAME=value" plain env vars
#   YAML_KIND         "api" (HTTP probes, scale, port) or "worker" (command override, no probes)
YAML_SECRET_VARS=()
YAML_PLAIN_ENV=()
YAML_KIND="api"

# Writes the container template YAML. It contains ONLY secretRef names, never values.
# Why YAML: the CLI flags cannot set probes, command/args or terminationGracePeriodSeconds
# in one place, and a YAML update replaces the whole container spec, so it repeats
# image, resources and env.
write_app_yaml() {   # $1 = file, $2 = app name, $3 = image, $4 = cpu, $5 = memory
  local file="$1" name="$2" image="$3" cpu="$4" mem="$5" v kv
  {
    echo "properties:"
    echo "  template:"
    if [ "$YAML_KIND" = "worker" ]; then
      echo "    terminationGracePeriodSeconds: $WORKER_TERMINATION_GRACE_S"
    fi
    echo "    containers:"
    echo "      - name: $name"
    echo "        image: $image"
    if [ "$YAML_KIND" = "worker" ]; then
      echo "        command: [\"python\"]"
      echo "        args: [\"-m\", \"app.worker\"]"
    fi
    echo "        resources:"
    echo "          cpu: $cpu"
    echo "          memory: $mem"
    echo "        env:"
    for v in "${YAML_SECRET_VARS[@]}"; do
      echo "          - name: $v"
      echo "            secretRef: $(secret_name "$v")"
    done
    for kv in "${YAML_PLAIN_ENV[@]}"; do
      echo "          - name: ${kv%%=*}"
      echo "            value: \"${kv#*=}\""
    done
    if [ "$YAML_KIND" = "api" ]; then
      # The API starts in seconds now (no clone/ingest). Startup allows
      # 5s + 6 x 10s = ~65s; liveness/readiness start after startup passes.
      cat <<EOF
        probes:
          - type: Startup
            httpGet:
              path: /health
              port: $TARGET_PORT
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 6
          - type: Liveness
            httpGet:
              path: /health
              port: $TARGET_PORT
            periodSeconds: 30
            timeoutSeconds: 5
            failureThreshold: 3
          - type: Readiness
            httpGet:
              path: /health
              port: $TARGET_PORT
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
    scale:
      minReplicas: $API_MIN_REPLICAS
      maxReplicas: $API_MAX_REPLICAS
EOF
    else
      # No probes: the worker has no HTTP port. Container Apps only adds default
      # probes when ingress exists. Health = the process is running; a crash
      # restarts the container and the queue message reappears.
      cat <<EOF
    scale:
      minReplicas: $WORKER_MIN_REPLICAS
      maxReplicas: $WORKER_MAX_REPLICAS
EOF
    fi
  } > "$file"
}

# Builds the --secrets args for the given env var names (skips empty ones).
# Result goes into SECRET_ARGS and the non-empty names into YAML_SECRET_VARS.
collect_secrets() {   # args = env var names
  SECRET_ARGS=()
  YAML_SECRET_VARS=()
  local v
  for v in "$@"; do
    if [ -n "${!v:-}" ]; then
      SECRET_ARGS+=("$(secret_name "$v")=${!v}")
      YAML_SECRET_VARS+=("$v")
    fi
  done
}

# Creates the app if missing (with secrets + placeholder image), else refreshes secrets.
# Remaining args are extra flags for `az containerapp create` (ingress/size).
ensure_app() {   # $1 = app name, $2 = cpu, $3 = memory, then extra create flags
  local name="$1" cpu="$2" mem="$3"; shift 3
  if ! az containerapp show --name "$name" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
    az containerapp create \
      --name "$name" --resource-group "$RESOURCE_GROUP" \
      --environment "$ENV_NAME" \
      --image "$PLACEHOLDER_IMAGE" \
      --cpu "$cpu" --memory "$mem" \
      --user-assigned "$IDENTITY_ID" \
      --registry-server "$ACR_LOGIN_SERVER" --registry-identity "$IDENTITY_ID" \
      --secrets "${SECRET_ARGS[@]}" "$@" -o none
  else
    echo "Container App $name exists; updating secrets and registry."
    az containerapp secret set --name "$name" --resource-group "$RESOURCE_GROUP" \
      --secrets "${SECRET_ARGS[@]}" -o none
    az containerapp registry set --name "$name" --resource-group "$RESOURCE_GROUP" \
      --server "$ACR_LOGIN_SERVER" --identity "$IDENTITY_ID" -o none
  fi
}

apply_yaml() {   # $1 = app name, $2 = cpu, $3 = memory
  local name="$1" cpu="$2" mem="$3"
  local image yaml_file="./containerapp.generated.yaml"   # relative path: avoids Git Bash/Windows path issues
  image="$(az containerapp show --name "$name" --resource-group "$RESOURCE_GROUP" \
    --query 'properties.template.containers[0].image' -o tsv)"
  write_app_yaml "$yaml_file" "$name" "$image" "$cpu" "$mem"
  az containerapp update --name "$name" --resource-group "$RESOURCE_GROUP" --yaml "$yaml_file" -o none
  rm -f "$yaml_file"
}

# ------------------------------- STEP 7 --------------------------------------
step "7/9 API Container App (webhook -> queue), 0.5 vCPU / 1Gi"
collect_secrets "${API_REQUIRED_SECRETS[@]}"
ensure_app "$APP_NAME" "$API_CPU" "$API_MEMORY" \
  --target-port "$TARGET_PORT" --ingress external \
  --min-replicas "$API_MIN_REPLICAS" --max-replicas "$API_MAX_REPLICAS"

# Old single-container layout stored the LLM keys on this app. The API no longer
# needs them; drop them so they are not kept in two places (GITHUB_TOKEN stays: author check).
for stale in TARGET_REPO_URL GROQ_API_KEY GOOGLE_API_KEY VOYAGE_API_KEY LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
  sname="$(secret_name "$stale")"
  if az containerapp secret list --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
        --query "[?name=='$sname'].name" -o tsv | grep -q .; then
    echo "Removing stale secret $sname from the API app (the env var is dropped by the YAML update below)."
    STALE_REMOVE="${STALE_REMOVE:-} $sname"
  fi
done

YAML_KIND="api"
YAML_PLAIN_ENV=("ENV=production" "JOBS_BACKEND=azure" "ALLOWED_REPOS=$ALLOWED_REPOS"
  "AZURE_STORAGE_ACCOUNT_NAME=$STORAGE_ACCOUNT_NAME" "AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID")
# The YAML update first (it removes the old secretRef env vars), then the secrets
# can be deleted without leaving a dangling reference.
apply_yaml "$APP_NAME" "$API_CPU" "$API_MEMORY"
if [ -n "${STALE_REMOVE:-}" ]; then
  # shellcheck disable=SC2086
  az containerapp secret remove --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --secret-names $STALE_REMOVE -o none
fi
pause

# ------------------------------- STEP 8 --------------------------------------
step "8/9 Worker Container App (queue -> review), no ingress, 2 vCPU / 4Gi"
collect_secrets "${WORKER_REQUIRED_SECRETS[@]}" "${WORKER_OPTIONAL_SECRETS[@]}"
# No --ingress flag = no ingress, no public URL.
ensure_app "$WORKER_APP_NAME" "$WORKER_CPU" "$WORKER_MEMORY" \
  --min-replicas "$WORKER_MIN_REPLICAS" --max-replicas "$WORKER_MAX_REPLICAS"

YAML_KIND="worker"
YAML_PLAIN_ENV=("ENV=production" "JOBS_BACKEND=azure"
  "AZURE_STORAGE_ACCOUNT_NAME=$STORAGE_ACCOUNT_NAME" "AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID"
  "REVIEW_VISIBILITY_TIMEOUT_S=$REVIEW_VISIBILITY_TIMEOUT_S" "MAX_ATTEMPTS=$MAX_ATTEMPTS"
  "LANGFUSE_HOST=${LANGFUSE_HOST:-https://cloud.langfuse.com}")
apply_yaml "$WORKER_APP_NAME" "$WORKER_CPU" "$WORKER_MEMORY"

# Optional, for later: scale the worker with queue length using KEDA. Leave it
# commented while min=max=1. With min 0 every cold start re-clones and
# re-parses the repo (minutes on a large repo) before the first job runs, so only
# do this after the index is persisted or you accept that delay.
#   az containerapp update --name "$WORKER_APP_NAME" --resource-group "$RESOURCE_GROUP" \
#     --min-replicas 1 --max-replicas 3 \
#     --scale-rule-name queue-length --scale-rule-type azure-queue \
#     --scale-rule-metadata accountName="$STORAGE_ACCOUNT_NAME" queueName="$QUEUE_NAME" queueLength=5 \
#     --scale-rule-identity "$IDENTITY_ID"
pause

# ------------------------------- STEP 9 --------------------------------------
step "9/9 GitHub Actions OIDC (no passwords): Entra app, service principal, federated credential, roles"
OIDC_CLIENT_ID="$(az ad app list --display-name "$OIDC_APP_NAME" --query '[0].appId' -o tsv)"
if [ -z "$OIDC_CLIENT_ID" ]; then
  OIDC_CLIENT_ID="$(az ad app create --display-name "$OIDC_APP_NAME" --query appId -o tsv)"
fi

SP_OBJECT_ID="$(az ad sp list --filter "appId eq '$OIDC_CLIENT_ID'" --query '[0].id' -o tsv)"
if [ -z "$SP_OBJECT_ID" ]; then
  SP_OBJECT_ID="$(az ad sp create --id "$OIDC_CLIENT_ID" --query id -o tsv)"
fi

# Federated credential: "trust tokens GitHub issues for pushes to main of this repo".
add_federated_credential() {   # $1 = credential name, $2 = subject
  local name="$1" subject="$2" file="./federated-credential.generated.json"
  if az ad app federated-credential list --id "$OIDC_CLIENT_ID" \
        --query "[?name=='$name'].name" -o tsv | grep -q .; then
    echo "Federated credential '$name' already exists."
    return
  fi
  cat > "$file" <<EOF
{
  "name": "$name",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "audiences": ["api://AzureADTokenExchange"]
}
EOF
  az ad app federated-credential create --id "$OIDC_CLIENT_ID" --parameters "$file" -o none
  rm -f "$file"
}
add_federated_credential "github-main" "repo:${GITHUB_REPO}:ref:refs/heads/main"
if [ -n "$GITHUB_ENVIRONMENT" ]; then
  add_federated_credential "github-env-${GITHUB_ENVIRONMENT}" "repo:${GITHUB_REPO}:environment:${GITHUB_ENVIRONMENT}"
fi

RG_ID="$(az group show --name "$RESOURCE_GROUP" --query id -o tsv)"
assign_role() {   # $1 = role, $2 = scope
  if [ -z "$(az role assignment list --assignee "$SP_OBJECT_ID" --scope "$2" --role "$1" \
        --query '[0].id' -o tsv)" ]; then
    az role assignment create --assignee-object-id "$SP_OBJECT_ID" \
      --assignee-principal-type ServicePrincipal --role "$1" --scope "$2" -o none
  fi
}
# Contributor on the resource group lets the pipeline update BOTH Container Apps
# (new revisions). Narrower option is explained in infra/README.md.
assign_role "Contributor" "$RG_ID"
# AcrPush lets the pipeline push images (az acr login / docker push).
assign_role "AcrPush" "$ACR_ID"

APP_FQDN="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn -o tsv)"

# ------------------------------- SUMMARY -------------------------------------
cat <<EOF

=============================================================================
DONE. Add these to GitHub: Settings > Secrets and variables > Actions

SECRETS (identifiers, not passwords - but keep them as secrets anyway):
  AZURE_CLIENT_ID        = $OIDC_CLIENT_ID
  AZURE_TENANT_ID        = $TENANT_ID
  AZURE_SUBSCRIPTION_ID  = $SUBSCRIPTION_ID

VARIABLES:
  AZURE_RESOURCE_GROUP   = $RESOURCE_GROUP
  ACR_NAME               = $ACR_NAME
  CONTAINER_APP_NAME     = $APP_NAME
  WORKER_APP_NAME        = $WORKER_APP_NAME

Also useful:
  Image reference        = $ACR_LOGIN_SERVER/autonomous-code-reviewer:<git-sha>
  Webhook URL (GitHub)   = https://$APP_FQDN/webhook/github
  Storage account        = $STORAGE_ACCOUNT_NAME (queues: $QUEUE_NAME, $POISON_QUEUE_NAME)
  Federated subject      = repo:${GITHUB_REPO}:ref:refs/heads/main
  Worker logs            = az containerapp logs show -n $WORKER_APP_NAME -g $RESOURCE_GROUP --follow

Reminder: both apps show an unhealthy first revision until the pipeline
deploys the real image (the placeholder listens on port 80, not 8000, and the
worker's command "python -m app.worker" does not exist in the placeholder).
=============================================================================
EOF
