"""구글 OAuth 로그인 + 서명 쿠키 세션.

GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 환경변수가 설정된 경우에만 로그인이 강제된다.
(미설정 시 로그인 없이 동작 — 초기 설정 단계 대비)
ALLOWED_EMAILS(쉼표 구분) 또는 ALLOWED_DOMAIN 으로 접근 계정을 제한할 수 있다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import requests

COOKIE_NAME = "oag_session"


def base_url() -> str:
    """서비스 대표 주소. BASE_URL 환경변수로 전환 (기본: bid.oneaigen.com)."""
    return os.environ.get("BASE_URL", "https://bid.oneaigen.com").strip().rstrip("/")


def callback_url() -> str:
    return f"{base_url()}/auth/callback"
SESSION_MAX_AGE = 30 * 86400  # 30일
TIMEOUT = 15


def client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()


def enabled() -> bool:
    return bool(client_id() and client_secret())


def _secret() -> bytes:
    raw = os.environ.get("SESSION_SECRET", "").strip() or client_secret() or "dev-secret"
    return hashlib.sha256(raw.encode()).digest()


def _sign(data: bytes) -> str:
    return hmac.new(_secret(), data, hashlib.sha256).hexdigest()


def make_session(email: str) -> str:
    payload = json.dumps({"email": email, "exp": int(time.time()) + SESSION_MAX_AGE})
    data = base64.urlsafe_b64encode(payload.encode()).decode()
    return f"{data}.{_sign(data.encode())}"


def read_session(cookie: str | None) -> str | None:
    """쿠키 값에서 이메일 추출. 서명·만료 검증 실패 시 None."""
    if not cookie or "." not in cookie:
        return None
    data, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(data.encode())):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(data.encode()))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("email")


def login_url() -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": callback_url(),
        "response_type": "code",
        "scope": "openid email",
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


class AuthError(Exception):
    pass


def handle_callback(code: str) -> str:
    """인가 코드 → 토큰 교환 → 이메일 검증 후 반환."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": callback_url(),
            "code": code,
        },
        timeout=TIMEOUT,
    )
    data = resp.json()
    id_token = data.get("id_token")
    if not id_token:
        raise AuthError(f"구글 토큰 교환 실패: {data.get('error_description') or data.get('error')}")
    info = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"id_token": id_token},
        timeout=TIMEOUT,
    ).json()
    if info.get("aud") != client_id():
        raise AuthError("토큰 검증 실패 (aud 불일치)")
    email = info.get("email")
    if not email or info.get("email_verified") not in ("true", True):
        raise AuthError("이메일이 확인되지 않은 계정입니다.")
    return email


def is_admin(email: str | None) -> bool:
    """개인 기능(메인 대시보드) 접근 가능 여부. ADMIN_EMAILS 미설정 시 아무도 아님."""
    if not email:
        return False
    admins = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]
    return email.lower() in admins


def allowed(email: str) -> bool:
    emails = [e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
    domain = os.environ.get("ALLOWED_DOMAIN", "").strip().lower().lstrip("@")
    if not emails and not domain:
        return True  # 제한 미설정 시 모든 구글 계정 허용
    email = email.lower()
    if emails and email in emails:
        return True
    if domain and email.endswith("@" + domain):
        return True
    return False
