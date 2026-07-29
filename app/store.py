"""계정별 즐겨찾기 서버 저장소 (Upstash Redis REST).

Vercel 대시보드 Storage에서 Upstash Redis를 연결하면 환경변수
(UPSTASH_REDIS_REST_URL/TOKEN 또는 KV_REST_API_URL/TOKEN)가 자동 주입된다.
미설정 시 enabled()가 False → 프론트는 브라우저(localStorage) 저장으로 동작.
"""
from __future__ import annotations

import json
import os

import requests

TIMEOUT = 10
MAX_ITEMS = 500


def _conf() -> tuple[str, str] | None:
    url = (os.environ.get("UPSTASH_REDIS_REST_URL")
           or os.environ.get("KV_REST_API_URL") or "").strip().rstrip("/")
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN")
             or os.environ.get("KV_REST_API_TOKEN") or "").strip()
    if url and token:
        return url, token
    return None


def enabled() -> bool:
    return _conf() is not None


class StoreError(Exception):
    pass


def _command(cmd: list) -> dict:
    conf = _conf()
    if not conf:
        raise StoreError("저장소가 설정되지 않았습니다.")
    url, token = conf
    resp = requests.post(
        url, json=cmd,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if "error" in data:
        raise StoreError(str(data["error"])[:200])
    return data


def get_favs(email: str) -> dict:
    data = _command(["GET", f"favs:{email.lower()}"])
    raw = data.get("result")
    if not raw:
        return {}
    try:
        favs = json.loads(raw)
        return favs if isinstance(favs, dict) else {}
    except json.JSONDecodeError:
        return {}


def set_favs(email: str, favs: dict) -> None:
    if not isinstance(favs, dict):
        raise StoreError("잘못된 형식입니다.")
    if len(favs) > MAX_ITEMS:
        favs = dict(list(favs.items())[:MAX_ITEMS])
    _command(["SET", f"favs:{email.lower()}", json.dumps(favs, ensure_ascii=False)])
