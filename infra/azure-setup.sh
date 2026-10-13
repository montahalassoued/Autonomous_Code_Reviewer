#!/usr/bin/env bash
# =============================================================================
# azure-setup.sh - one-time Azure setup for the Autonomous Code Reviewer.
#
# What it creates (all in ONE resource group, so teardown is one command):
#   1. Resource providers registered (Microsoft.App, OperationalInsights, ContainerRegistry)
#   2. Resource group
#   3. Azure Container Registry (Basic SKU, admin user DISABLED)
#   4. Log Analytics workspace + Container Apps environment
#   5. User-assigned managed identity with AcrPull on the registry
#   6. The Container App (placeholder image first; the CI pipeline swaps it)
#      with secrets, secretref env vars, 2 vCPU / 4Gi, min=max=1 replica
#   7. Probes (startup/liveness/readiness) applied through a generated YAML
#   8. GitHub Actions OIDC: Entra app + service principal + federated credential
#      + Contributor (resource group scope) + AcrPush (registry scope)
#
# Usage (Azure Cloud Shell or Git Bash, after `az login`):
#   1. Edit the variables in the "CONFIG" block (at least ACR_NAME, GITHUB_REPO).
#   2. Export the app secrets in your shell (never put them in this file):
#        export TARGET_REPO_URL='https://github.com/owner/repo.git'
#        export GROQ_API_KEY='...'  GOOGLE_API_KEY='...'  VOYAGE_API_KEY='...'
#        export GITHUB_TOKEN='...'  GITHUB_WEBHOOK_SECRET='...'
#        # optional: export LANGSMITH_API_KEY='...'
#   3. Run:   bash infra/azure-setup.sh
#      Set PAUSE=1 to stop and wait for Enter between steps:
#                PAUSE=1 bash infra/azure-setup.sh
#
# Re-running is safe: every step checks whether the resource already exists
# and reuses it (create-or-update). Nothing is deleted.
# =============================================================================
set -euo pipefail

# Git Bash on Windows rewrites arguments that start with "/" (like Azure
# resource ids "/subscriptions/...") into Windows paths. This turns it off.
export MSYS_NO_PATHCONV=1

# ------------------------------- CONFIG --------------------------------------
LOCATION="${LOCATION:-westeurope}"                 # Azure region; see README for how to choose
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-code-reviewer}"
ACR_NAME="${ACR_NAME:-}"                           # REQUIRED: globally unique, 5-50 chars, lowercase letters+digits only
ENV_NAME="${ENV_NAME:-cae-code-reviewer}"          # Container Apps environment
APP_NAME="${APP_NAME:-ca-code-reviewer}"           # Container App
GITHUB_REPO="${GITHUB_REPO:-}"                     # REQUIRED: "owner/repo" that hosts the workflow
# Optional: if the workflow job uses a GitHub *environment* (environment: prod),
# set its name here and a second federated credential is created for it.
GITHUB_ENVIRONMENT="${GITHUB_ENVIRONMENT:-}"

LOG_WORKSPACE="${LOG_WORKSPACE:-log-code-reviewer}"
IDENTITY_NAME="${IDENTITY_NAME:-id-code-reviewer-acrpull}"
OIDC_APP_NAME="${OIDC_APP_NAME:-github-oidc-code-reviewer}"

# Container sizing. 2.0 vCPU / 4Gi is a valid Container Apps combination
# (Consumption profile). Torch reranker + Chroma + embeddings need the memory.
CPU="2.0"
MEMORY="4.0Gi"
MIN_REPLICAS=1     # 1 = always warm so GitHub's ~10s webhook timeout is met
MAX_REPLICAS=1     # 1 = state is in-memory/on local disk; >1 replicas would each ingest separately
TARGET_PORT=8000
PLACEHOLDER_IMAGE="mcr.microsoft.com/k8se/quickstart:latest"

# ------------------------------- HELPERS -------------------------------------
step() { echo; echo "=== $* ==="; }
pause() { if [ "${PAUSE:-0}" = "1" ]; then read -r -p "Press Enter to continue (Ctrl+C to stop)... "; fi; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ------------------------------- PRE-CHECKS ----------------------------------
[ -n "$ACR_NAME" ] || die "Set ACR_NAME (globally unique, lowercase letters/digits, 5-50 chars). Example: ACR_NAME=acrcodereviewer123 bash infra/azure-setup.sh"
[[ "$ACR_NAME" =~ ^[a-z0-9]{5,50}$ ]] || die "ACR_NAME must be 5-50 lowercase letters/digits only (no dashes)."
[[ "$GITHUB_REPO" =~ ^[^/]+/[^/]+$ ]] || die "Set GITHUB_REPO as owner/repo (e.g. GITHUB_REPO=montahalassoued/autonomous-code-reviewer)."

# App secrets are read from YOUR shell environment. Required ones must be set,
# because Container Apps refuses empty secret values.
REQUIRED_SECRETS=(TARGET_REPO_URL GROQ_API_KEY GOOGLE_API_KEY VOYAGE_API_KEY GITHUB_TOKEN GITHUB_WEBHOOK_SECRET)
OPTIONAL_SECRETS=(LANGSMITH_API_KEY)
missing=0
for v in "${REQUIRED_SECRETS[@]}"; do
  if [ -z "${!v:-}" ]; then echo "WARNING: required variable $v is empty in your shell." >&2; missing=1; fi
done
for v in "${OPTIONAL_SECRETS[@]}"; do
  if [ -z "${!v:-}" ]; then echo "NOTE: optional variable $v is empty; it will be skipped."; fi
done
[ "$missing" = "0" ] || die "Export the missing variables listed above, then re-run."

# Env var NAME -> Container Apps secret name (lowercase, dashes; secret names cannot contain '_').
secret_name() { echo "$1" | tr 'A-Z_' 'a-z-'; }

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
TENANT_ID="$(az account show --query tenantId -o tsv)"
echo "Using subscription $SUBSCRIPTION_ID (tenant $TENANT_ID)"
pause

# ------------------------------- STEP 1 --------------------------------------
step "1/8 Register resource providers (first-time subscriptions only; can take a few minutes)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
  az provider register --namespace "$ns" --wait
done
pause

# ------------------------------- STEP 2 --------------------------------------
step "2/8 Resource group"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none
pause

# ------------------------------- STEP 3 --------------------------------------
step "3/8 Azure Container Registry (Basic, admin user disabled)"
if ! az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" \
    --sku Basic --admin-enabled false -o none
fi
ACR_ID="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
ACR_LOGIN_SERVER="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv)"
pause

# ------------------------------- STEP 4 --------------------------------------
step "4/8 Log Analytics workspace + Container Apps environment"
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
step "5/8 User-assigned managed identity + AcrPull on the registry"
az identity create --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --location "$LOCATION" -o none
IDENTITY_ID="$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
IDENTITY_PRINCIPAL_ID="$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)"

# Only create the assignment if it is not there yet (keeps re-runs quiet).
if [ -z "$(az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$ACR_ID" \
      --role AcrPull --query '[0].id' -o tsv)" ]; then
  az role assignment create --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull --scope "$ACR_ID" -o none
  echo "Waiting 60s for the role assignment to propagate before the app tries to pull..."
  sleep 60
fi
pause

# ------------------------------- STEP 6 --------------------------------------
step "6/8 Container App with secrets and secretref env vars"

# Build "--secrets" and "--env-vars" arguments from the shell environment.
SECRET_ARGS=()
ENV_ARGS=()
for v in "${REQUIRED_SECRETS[@]}" "${OPTIONAL_SECRETS[@]}"; do
  if [ -n "${!v:-}" ]; then
    SECRET_ARGS+=("$(secret_name "$v")=${!v}")
    ENV_ARGS+=("$v=secretref:$(secret_name "$v")")
  fi
done
# Non-secret settings (plain env vars).
ENV_ARGS+=("ENV=production" "LANGSMITH_PROJECT=${LANGSMITH_PROJECT:-autonomous-code-reviewer}")

if ! az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" -o none 2>/dev/null; then
  # NOTE: the placeholder image listens on port 80, not 8000, so the first
  # revision will show as unhealthy until the pipeline deploys the real image.
  # That is expected; see infra/README.md.
  az containerapp create \
    --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --environment "$ENV_NAME" \
    --image "$PLACEHOLDER_IMAGE" \
    --target-port "$TARGET_PORT" --ingress external \
    --cpu "$CPU" --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
    --user-assigned "$IDENTITY_ID" \
    --registry-server "$ACR_LOGIN_SERVER" --registry-identity "$IDENTITY_ID" \
    --secrets "${SECRET_ARGS[@]}" \
    --env-vars "${ENV_ARGS[@]}" -o none
else
  echo "Container App exists; updating secrets and env vars."
  az containerapp secret set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --secrets "${SECRET_ARGS[@]}" -o none
  az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --cpu "$CPU" --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS" \
    --set-env-vars "${ENV_ARGS[@]}" -o none
  az containerapp registry set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
    --server "$ACR_LOGIN_SERVER" --identity "$IDENTITY_ID" -o none
fi
pause

# ------------------------------- STEP 7 --------------------------------------
step "7/8 Health probes (generated YAML; the CLI flags cannot set probes)"
# Why these numbers (Container Apps limits: period <= 240s, failureThreshold <= 10):
#  - The app does NOT listen on port 8000 until startup (clone + ingest + model
#    load) is finished, because that work runs inside the FastAPI lifespan.
#    So during startup the probe sees "connection refused", which is normal.
#  - startup: initial 60s + 10 failures x 60s period = up to ~11 minutes before
#    the container is killed. Liveness/readiness only begin after startup passes.
#  - Raise failureThreshold/periodSeconds here if your repo takes longer to ingest.
#
# The container spec is replaced as a whole by a YAML update, so the file repeats
# image, resources and env vars. It contains ONLY secretRef names, never values.
CURRENT_IMAGE="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" \
  --query 'properties.template.containers[0].image' -o tsv)"
YAML_FILE="./containerapp.generated.yaml"   # relative path: avoids Git Bash/Windows path issues

{
  echo "properties:"
  echo "  template:"
  echo "    containers:"
  echo "      - name: $APP_NAME"
  echo "        image: $CURRENT_IMAGE"
  echo "        resources:"
  echo "          cpu: $CPU"
  echo "          memory: $MEMORY"
  echo "        env:"
  for v in "${REQUIRED_SECRETS[@]}" "${OPTIONAL_SECRETS[@]}"; do
    if [ -n "${!v:-}" ]; then
      echo "          - name: $v"
      echo "            secretRef: $(secret_name "$v")"
    fi
  done
  echo "          - name: ENV"
  echo "            value: production"
  echo "          - name: LANGSMITH_PROJECT"
  echo "            value: ${LANGSMITH_PROJECT:-autonomous-code-reviewer}"
  cat <<EOF
        probes:
          - type: Startup
            httpGet:
              path: /health
              port: $TARGET_PORT
            initialDelaySeconds: 60
            periodSeconds: 60
            timeoutSeconds: 5
            failureThreshold: 10
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
EOF
} > "$YAML_FILE"

az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --yaml "$YAML_FILE" -o none
rm -f "$YAML_FILE"
pause

# ------------------------------- STEP 8 --------------------------------------
step "8/8 GitHub Actions OIDC (no passwords): Entra app, service principal, federated credential, roles"
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
# Contributor on the resource group lets the pipeline update the Container App
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

Also useful:
  Image reference        = $ACR_LOGIN_SERVER/autonomous-code-reviewer:<git-sha>
  Webhook URL (GitHub)   = https://$APP_FQDN/webhook/github
  Federated subject      = repo:${GITHUB_REPO}:ref:refs/heads/main

Reminder: the app shows an unhealthy first revision until the pipeline
deploys the real image (the placeholder listens on port 80, not 8000).
=============================================================================
EOF
