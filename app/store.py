"""계정별 즐겨찾기 서버 저장소.

지원 백엔드 (우선순위 순):
1. Supabase — SUPABASE_URL + SUPABASE_SERVICE_KEY 환경변수.
   favs 테이블 필요: create table favs (email text primary key, data jsonb);
2. Upstash Redis — UPSTASH_REDIS_REST_URL/TOKEN 또는 KV_REST_API_URL/TOKEN
   (Vercel Storage 탭에서 연결 시 자동 주입)
미설정 시 enabled()가 False → 프론트는 브라우저(localStorage) 저장으로 동작.
"""
from __future__ import annotations

import json
import os
from urllib.parse import quote

import requests

_session = requests.Session()

TIMEOUT = 10
MAX_ITEMS = 500


class StoreError(Exception):
    pass


def _supabase_conf() -> tuple[str, str] | None:
    raw = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not raw or not key:
        return None
    # 어떤 형태로 넣어도 동작하도록 스킴+호스트만 사용
    # (예: .../rest/v1, 끝 슬래시, 대시보드 주소 등 뒤에 붙은 경로 제거)
    from urllib.parse import urlsplit
    parts = urlsplit(raw if "://" in raw else f"https://{raw}")
    if not parts.netloc:
        return None
    return f"https://{parts.netloc}", key


def _upstash_conf() -> tuple[str, str] | None:
    url = (os.environ.get("UPSTASH_REDIS_REST_URL")
           or os.environ.get("KV_REST_API_URL") or "").strip().rstrip("/")
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN")
             or os.environ.get("KV_REST_API_TOKEN") or "").strip()
    if url and token:
        return url, token
    return None


def enabled() -> bool:
    return _supabase_conf() is not None or _upstash_conf() is not None


# ------------------------------------------------------------ Supabase

def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _sb_get(email: str) -> dict:
    url, key = _supabase_conf()
    resp = _session.get(
        f"{url}/rest/v1/favs",
        params={"select": "data", "email": f"eq.{email}"},
        headers=_sb_headers(key),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise StoreError(f"Supabase 조회 실패({resp.status_code}): {resp.text[:150]}")
    rows = resp.json()
    if rows and isinstance(rows[0].get("data"), dict):
        return rows[0]["data"]
    return {}


def _sb_set(email: str, favs: dict) -> None:
    url, key = _supabase_conf()
    resp = _session.post(
        f"{url}/rest/v1/favs",
        json={"email": email, "data": favs},
        headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates"},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise StoreError(f"Supabase 저장 실패({resp.status_code}): {resp.text[:150]}")


# ------------------------------------------------------------ Upstash

def _redis_command(cmd: list) -> dict:
    url, token = _upstash_conf()
    resp = _session.post(
        url, json=cmd,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise StoreError(str(data["error"])[:200])
    return data


def _redis_get(email: str) -> dict:
    data = _redis_command(["GET", f"favs:{email}"])
    raw = data.get("result")
    if not raw:
        return {}
    try:
        favs = json.loads(raw)
        return favs if isinstance(favs, dict) else {}
    except json.JSONDecodeError:
        return {}


def _redis_set(email: str, favs: dict) -> None:
    _redis_command(["SET", f"favs:{email}", json.dumps(favs, ensure_ascii=False)])


# ------------------------------------------------------------ 공용 API

def get_favs(email: str) -> dict:
    email = email.lower()
    if _supabase_conf():
        return _sb_get(email)
    if _upstash_conf():
        return _redis_get(email)
    raise StoreError("저장소가 설정되지 않았습니다.")


def set_favs(email: str, favs: dict) -> None:
    if not isinstance(favs, dict):
        raise StoreError("잘못된 형식입니다.")
    if len(favs) > MAX_ITEMS:
        favs = dict(list(favs.items())[:MAX_ITEMS])
    email = email.lower()
    if _supabase_conf():
        return _sb_set(email, favs)
    if _upstash_conf():
        return _redis_set(email, favs)
    raise StoreError("저장소가 설정되지 않았습니다.")
