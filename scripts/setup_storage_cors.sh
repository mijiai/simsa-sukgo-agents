#!/usr/bin/env bash
# scripts/setup_storage_cors.sh — Azure Storage Blob CORS 룰 설정 (idempotent)
#
# 왜 필요한가:
#   claude.ai artifact 가 create_upload_url 로 받은 SAS URL 에 직접 PUT 하려면
#   브라우저 CORS 가 통과해야 함. 기본 Storage Account 는 CORS 룰이 비어있어
#   preflight OPTIONS 가 차단된다. 이 스크립트는 PUT/GET/HEAD 를 claude.ai
#   origin 에서 허용하도록 룰을 (재)등록한다.
#
# 동작:
#   1. .env 에서 AZURE_STORAGE_CONNECTION_STRING 로드 → AccountName/Key 추출
#   2. 기존 blob CORS 룰 모두 clear (중복 방지)
#   3. CORS_ORIGINS 에 정의된 origin 목록 add
#
# 재실행 안전: clear 후 add 라 항상 동일한 최종 상태.
#
# 사전 요구:
#   - az CLI + 'az login'
#   - 프로젝트 루트에 .env (AZURE_STORAGE_CONNECTION_STRING 포함)
#
# 환경변수 override (선택):
#   CORS_ORIGINS      — 공백 또는 콤마 구분 origin 목록
#                       (default: claude.ai + Vercel frontend 도메인들)
#   CORS_MAX_AGE      — preflight 캐시 초 (default: 3600)
#   CORS_METHODS      — 공백 또는 콤마 구분 메서드 (default: PUT GET HEAD)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"

# Default: claude.ai (artifact iframe) + Vercel (자체 프론트엔드) 도메인.
# 추가 / 변경이 필요하면 CORS_ORIGINS env 로 override.
# 구분자는 공백 또는 콤마 둘 다 허용 (사용자 편의).
CORS_ORIGINS="${CORS_ORIGINS:-https://claude.ai https://*.claude.ai https://simsasukgo-frontend.vercel.app https://*.vercel.app}"
CORS_MAX_AGE="${CORS_MAX_AGE:-3600}"
CORS_METHODS="${CORS_METHODS:-PUT GET HEAD}"

# 콤마 → 공백으로 정규화 (env 입력 형식 자유)
CORS_ORIGINS="${CORS_ORIGINS//,/ }"
CORS_METHODS="${CORS_METHODS//,/ }"

log() { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
err() { printf "\033[1;31m[err]\033[0m %s\n" "$*" >&2; }

command -v az >/dev/null || { err "az CLI not installed. brew install azure-cli"; exit 1; }
az account show -o none 2>/dev/null || { err "Not logged in. Run: az login"; exit 1; }
[[ -f "$ENV_FILE" ]] || { err ".env not found at $ENV_FILE"; exit 1; }

# === .env 로드 (deploy_azure.sh 와 동일 방식) ===
log "Loading .env from $ENV_FILE..."
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  export "$key=$value"
done < <(grep -E "^[A-Z_][A-Z0-9_]*=" "$ENV_FILE")

: "${AZURE_STORAGE_CONNECTION_STRING:?AZURE_STORAGE_CONNECTION_STRING missing in .env}"

# === connection string 파싱 (AccountName / AccountKey) ===
ACCOUNT_NAME=""
ACCOUNT_KEY=""
IFS=';' read -ra parts <<< "$AZURE_STORAGE_CONNECTION_STRING"
for part in "${parts[@]}"; do
  case "$part" in
    AccountName=*) ACCOUNT_NAME="${part#AccountName=}" ;;
    AccountKey=*)  ACCOUNT_KEY="${part#AccountKey=}" ;;
  esac
done
[[ -n "$ACCOUNT_NAME" ]] || { err "AccountName not found in connection string"; exit 1; }
[[ -n "$ACCOUNT_KEY" ]]  || { err "AccountKey not found in connection string"; exit 1; }

log "Target Storage Account: $ACCOUNT_NAME"

# === 기존 CORS 룰 clear (중복 방지, idempotent) ===
log "Clearing existing blob CORS rules..."
az storage cors clear \
  --services b \
  --account-name "$ACCOUNT_NAME" \
  --account-key "$ACCOUNT_KEY" \
  -o none

# === CORS 룰 add ===
# shellcheck disable=SC2206  # word-splitting intended
ORIGIN_ARGS=( $CORS_ORIGINS )
# shellcheck disable=SC2206
METHOD_ARGS=( $CORS_METHODS )

log "Adding CORS rule:"
log "  origins: ${ORIGIN_ARGS[*]}"
log "  methods: ${METHOD_ARGS[*]}"
log "  max-age: $CORS_MAX_AGE"

az storage cors add \
  --services b \
  --methods "${METHOD_ARGS[@]}" \
  --origins "${ORIGIN_ARGS[@]}" \
  --allowed-headers '*' \
  --exposed-headers '*' \
  --max-age "$CORS_MAX_AGE" \
  --account-name "$ACCOUNT_NAME" \
  --account-key "$ACCOUNT_KEY" \
  -o none

# === 결과 확인 ===
log "Current CORS rules:"
az storage cors list \
  --services b \
  --account-name "$ACCOUNT_NAME" \
  --account-key "$ACCOUNT_KEY" \
  -o table

cat <<EOF

============================================================
✅ Storage CORS 설정 완료
============================================================
Account:   $ACCOUNT_NAME
Origins:   ${ORIGIN_ARGS[*]}
Methods:   ${METHOD_ARGS[*]}

artifact 측 자가 검증:
  fetch(upload_url, {
    method: "OPTIONS",        // 브라우저가 자동으로 보내는 preflight
    headers: { "Origin": "https://claude.ai" }
  })
  → 200 OK + Access-Control-Allow-Origin 헤더 확인

룰 변경/축소가 필요하면 CORS_ORIGINS 환경변수 override 후 재실행.
============================================================
EOF
