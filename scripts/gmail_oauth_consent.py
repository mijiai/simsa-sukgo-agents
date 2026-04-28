"""1회용 Gmail OAuth consent 스크립트.

브라우저로 OAuth 동의 → refresh_token 포함 자격증명 JSON 을
Azure Blob `credentials/gmail_oauth.json` 에 업로드.

사용 절차:
  1. .env 에 GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET 설정
     (Google Cloud Console → OAuth 2.0 Client ID — Desktop app 타입 권장)
  2. uv run python scripts/gmail_oauth_consent.py
  3. 브라우저가 열리면 발신용 Gmail 계정으로 로그인 + 권한 승인
  4. 자격증명이 자동으로 Blob 에 업로드됨 → ACA 가 다음 모니터링 호출 시 즉시 사용

scope: gmail.send 만. 메일 읽기 권한 없음.

대안: AZURE 업로드 안 하고 로컬 파일만 받고 싶으면 --no-upload 플래그.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

from src.agents.monitoring.gmail_client import GMAIL_SEND_SCOPE
from src.config.settings import get_settings
from src.storage.factory import close_storage, get_blob_store


def _build_client_config(client_id: str, client_secret: str) -> dict:
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def run_consent(client_id: str, client_secret: str) -> dict:
    flow = InstalledAppFlow.from_client_config(
        _build_client_config(client_id, client_secret),
        scopes=[GMAIL_SEND_SCOPE],
    )
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise SystemExit(
            "ERROR: refresh_token 누락. Google Cloud Console 에서 앱이 'External' "
            "유형이거나 prompt=consent 가 적용됐는지 확인하세요."
        )
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }


async def upload_to_blob(payload: dict) -> str:
    settings = get_settings()
    blob = get_blob_store()
    path = settings.gmail_credentials_blob_path
    await blob.upload(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    await close_storage()
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Blob 에 업로드하지 않고 로컬 파일에만 저장 (default: gmail_oauth.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("gmail_oauth.json"),
        help="--no-upload 시 로컬 저장 경로",
    )
    args = parser.parse_args()

    load_dotenv()
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit("ERROR: GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET 가 .env 에 없습니다.")

    print("==> 브라우저로 Gmail 동의 화면을 엽니다. 발신용 계정으로 로그인하세요.")
    payload = run_consent(client_id, client_secret)
    print("==> refresh_token 수령 완료.")

    if args.no_upload:
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"==> 로컬 저장 완료: {args.out.resolve()}")
        print("    이 파일을 Azure Storage Explorer 등으로")
        print(f"    Blob `{get_settings().gmail_credentials_blob_path}` 에 업로드하세요.")
        return

    path = asyncio.run(upload_to_blob(payload))
    print(f"==> Azure Blob 업로드 완료: {path}")
    print("    ACA 의 다음 monitor_run_now 호출부터 즉시 사용 가능 (재시작 불필요).")


if __name__ == "__main__":
    main()
