#!/usr/bin/env bash
# scripts/deploy_azure.sh — 심사숙고 MCP 서버 Azure Container Apps 배포 (idempotent)
#
# 동작:
#   1. .env 에서 시크릿/환경변수 로드
#   2. ACR 생성/확인 (admin user 활성화)
#   3. az acr build 로 클라우드에서 Docker 이미지 빌드 (로컬 Docker 불필요)
#   4. ACA Environment 생성/확인
#   5. Container App 생성 또는 update (이미지·시크릿·env vars·스케일 일괄)
#   6. FQDN 출력 (Claude.ai connector URL)
#
# 재실행 시: 기존 리소스는 update, 없으면 create. 안전하게 반복 실행 가능.
#
# 사전 요구:
#   - az CLI (>= 2.85) + 'az login' 완료
#   - 프로젝트 루트에 .env 파일 (시크릿 소스)
#   - Resource Group 이 이미 존재 (없으면 미리 az group create)
#
# 환경변수 override (선택):
#   RG, LOCATION, NAME_PREFIX, ACR_NAME, ACA_ENV, ACA_APP, IMAGE_TAG,
#   TARGET_PORT, MIN_REPLICAS, MAX_REPLICAS, CPU, MEMORY

set -euo pipefail

# === 설정 ===
RG="${RG:-SIMSASUKGO-RG}"
LOCATION="${LOCATION:-eastus2}"
NAME_PREFIX="${NAME_PREFIX:-simsasukgo}"
ACR_NAME="${ACR_NAME:-${NAME_PREFIX}acr}"
ACA_ENV="${ACA_ENV:-${NAME_PREFIX}-aca-env}"
ACA_APP="${ACA_APP:-${NAME_PREFIX}-mcp}"
IMAGE_NAME="${IMAGE_NAME:-${NAME_PREFIX}-mcp}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"
TARGET_PORT="${TARGET_PORT:-8000}"
MIN_REPLICAS="${MIN_REPLICAS:-1}"
MAX_REPLICAS="${MAX_REPLICAS:-3}"
CPU="${CPU:-0.5}"
MEMORY="${MEMORY:-1.0Gi}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"

log() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }

# === 진입 검증 ===
log "Checking prerequisites..."
command -v az >/dev/null || { err "az CLI not installed. brew install azure-cli"; exit 1; }
az account show -o none 2>/dev/null || { err "Not logged in. Run: az login"; exit 1; }

[[ -f "$ENV_FILE" ]] || { err ".env not found at $ENV_FILE"; exit 1; }

if ! az group show --name "$RG" -o none 2>/dev/null; then
  err "Resource Group '$RG' not found in current subscription."
  err "Create it first:  az group create --name $RG --location $LOCATION"
  exit 1
fi

# === .env 로드 (특수 문자 안전) ===
log "Loading .env from $ENV_FILE..."
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  export "$key=$value"
done < <(grep -E "^[A-Z_][A-Z0-9_]*=" "$ENV_FILE")

# 필수 시크릿 검증
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY missing in .env}"
: "${AZURE_STORAGE_CONNECTION_STRING:?AZURE_STORAGE_CONNECTION_STRING missing}"
: "${NAVER_CLIENT_ID:?NAVER_CLIENT_ID missing}"
: "${NAVER_CLIENT_SECRET:?NAVER_CLIENT_SECRET missing}"

# === ACR ===
log "Ensuring ACR '$ACR_NAME'..."
if ! az acr show --name "$ACR_NAME" -o none 2>/dev/null; then
  log "Creating ACR (Basic SKU)..."
  az acr create --resource-group "$RG" --name "$ACR_NAME" --sku Basic --admin-enabled true -o none
else
  az acr update --name "$ACR_NAME" --admin-enabled true -o none
fi

ACR_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)
log "ACR ready: $ACR_SERVER"

# === 이미지 빌드 (클라우드) ===
FULL_IMAGE="$ACR_SERVER/$IMAGE_NAME:$IMAGE_TAG"
log "Building image $FULL_IMAGE via az acr build (cloud, no local Docker needed)..."
az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_NAME:$IMAGE_TAG" \
  --image "$IMAGE_NAME:latest" \
  --file Dockerfile \
  "$PROJECT_ROOT"

# === ACA Environment ===
log "Ensuring ACA Environment '$ACA_ENV'..."
if ! az containerapp env show --name "$ACA_ENV" --resource-group "$RG" -o none 2>/dev/null; then
  log "Creating ACA environment (initial provisioning takes 2-3 min)..."
  az containerapp env create \
    --name "$ACA_ENV" \
    --resource-group "$RG" \
    --location "$LOCATION" \
    -o none
fi

# === 시크릿 + env vars 정의 ===
SECRETS=(
  "anthropic-api-key=${ANTHROPIC_API_KEY}"
  "azure-storage-connection-string=${AZURE_STORAGE_CONNECTION_STRING}"
  "naver-client-id=${NAVER_CLIENT_ID}"
  "naver-client-secret=${NAVER_CLIENT_SECRET}"
)
[[ -n "${LAWSUIT_API_KEY:-}" ]] && SECRETS+=("lawsuit-api-key=${LAWSUIT_API_KEY}")

ENV_VARS=(
  "ANTHROPIC_API_KEY=secretref:anthropic-api-key"
  "AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-connection-string"
  "NAVER_CLIENT_ID=secretref:naver-client-id"
  "NAVER_CLIENT_SECRET=secretref:naver-client-secret"
  "AZURE_STORAGE_BLOB_CONTAINER=${AZURE_STORAGE_BLOB_CONTAINER:-simsasukgo}"
  "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-haiku-4-5-20251001}"
  "REPORT_MODEL=${REPORT_MODEL:-claude-sonnet-4-6}"
  "REPORT_MAX_TOKENS=${REPORT_MAX_TOKENS:-8192}"
  "REPORT_SAS_EXPIRY_HOURS=${REPORT_SAS_EXPIRY_HOURS:-168}"
  "REPORT_SAMPLES_BLOB_PREFIX=${REPORT_SAMPLES_BLOB_PREFIX:-templates/report_samples/}"
  "MCP_HOST=0.0.0.0"
  "MCP_PORT=${TARGET_PORT}"
  "LOG_LEVEL=${LOG_LEVEL:-INFO}"
  "LOG_FORMAT=${LOG_FORMAT:-json}"
)
[[ -n "${LAWSUIT_API_KEY:-}" ]] && ENV_VARS+=("LAWSUIT_API_KEY=secretref:lawsuit-api-key")

# === ACA App: create or update ===
if ! az containerapp show --name "$ACA_APP" --resource-group "$RG" -o none 2>/dev/null; then
  log "Creating Container App '$ACA_APP'..."
  az containerapp create \
    --name "$ACA_APP" \
    --resource-group "$RG" \
    --environment "$ACA_ENV" \
    --image "$FULL_IMAGE" \
    --registry-server "$ACR_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --target-port "$TARGET_PORT" \
    --ingress external \
    --transport auto \
    --cpu "$CPU" \
    --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" \
    --max-replicas "$MAX_REPLICAS" \
    --secrets "${SECRETS[@]}" \
    --env-vars "${ENV_VARS[@]}" \
    -o none
else
  log "Updating Container App '$ACA_APP'..."
  az containerapp registry set \
    --name "$ACA_APP" \
    --resource-group "$RG" \
    --server "$ACR_SERVER" \
    --username "$ACR_USERNAME" \
    --password "$ACR_PASSWORD" \
    -o none
  az containerapp secret set \
    --name "$ACA_APP" \
    --resource-group "$RG" \
    --secrets "${SECRETS[@]}" \
    -o none
  az containerapp update \
    --name "$ACA_APP" \
    --resource-group "$RG" \
    --image "$FULL_IMAGE" \
    --set-env-vars "${ENV_VARS[@]}" \
    --cpu "$CPU" \
    --memory "$MEMORY" \
    --min-replicas "$MIN_REPLICAS" \
    --max-replicas "$MAX_REPLICAS" \
    -o none
fi

# === 출력 ===
FQDN=$(az containerapp show --name "$ACA_APP" --resource-group "$RG" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

cat <<EOF

============================================================
✅ 배포 완료
============================================================
Resource Group:  $RG
Region:          $LOCATION
ACR:             $ACR_SERVER
Image:           $IMAGE_NAME:$IMAGE_TAG
Container App:   $ACA_APP
Replicas:        $MIN_REPLICAS-$MAX_REPLICAS  ($CPU vCPU, $MEMORY)

App URL:         https://$FQDN
Health:          https://$FQDN/health
MCP SSE URL:     https://$FQDN/sse

다음 단계:
  1. curl https://$FQDN/health    # {"status":"ok"} 확인
  2. Claude.ai connector URL → https://$FQDN/sse 로 변경
  3. ngrok / 로컬 서버 종료
  4. (선택) az containerapp logs show -n $ACA_APP -g $RG --follow
============================================================
EOF
