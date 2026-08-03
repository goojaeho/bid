"""카카오톡 '나에게 보내기' 알림.

카카오디벨로퍼스 앱의 REST API 키로 OAuth 인증 후,
refresh token(KAKAO_REFRESH_TOKEN 환경변수)으로 access token을 발급받아
내 카카오톡으로 메시지를 보낸다.
"""
from __future__ import annotations

import json
import os

import requests

# REST API 키는 OAuth client_id로 쓰이는 값(공개되어도 메시지 발송은 불가).
# 환경변수로 덮어쓸 수 있다.
DEFAULT_REST_KEY = "178bcecd04c488b9ee716419607e66f1"
def callback_url() -> str:
    from app.auth import base_url
    return f"{base_url()}/kakao/callback"

AUTH_HOST = "https://kauth.kakao.com"
API_HOST = "https://kapi.kakao.com"
TIMEOUT = 15


class KakaoError(Exception):
    pass


def rest_key() -> str:
    return os.environ.get("KAKAO_REST_KEY", "").strip() or DEFAULT_REST_KEY


def refresh_token() -> str | None:
    return os.environ.get("KAKAO_REFRESH_TOKEN", "").strip() or None


def client_secret() -> str | None:
    return os.environ.get("KAKAO_CLIENT_SECRET", "").strip() or None


def authorize_url() -> str:
    return (
        f"{AUTH_HOST}/oauth/authorize?client_id={rest_key()}"
        f"&redirect_uri={callback_url()}&response_type=code&scope=talk_message"
    )


def exchange_code(code: str) -> dict:
    """인가 코드 → 토큰 교환. {'access_token':…, 'refresh_token':…} 반환."""
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_key(),
        "redirect_uri": callback_url(),
        "code": code,
    }
    if client_secret():
        data["client_secret"] = client_secret()
    resp = requests.post(f"{AUTH_HOST}/oauth/token", data=data, timeout=TIMEOUT)
    data = resp.json()
    if "access_token" not in data:
        raise KakaoError(f"토큰 교환 실패: {data.get('error_description') or data}")
    return data


def access_token_from_refresh() -> str:
    token = refresh_token()
    if not token:
        raise KakaoError("KAKAO_REFRESH_TOKEN이 설정되지 않았습니다. /kakao 에서 연결하세요.")
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_key(),
        "refresh_token": token,
    }
    if client_secret():
        data["client_secret"] = client_secret()
    resp = requests.post(f"{AUTH_HOST}/oauth/token", data=data, timeout=TIMEOUT)
    data = resp.json()
    if "access_token" not in data:
        raise KakaoError(f"토큰 갱신 실패(재연결 필요): {data.get('error_description') or data}")
    return data["access_token"]


def send_memo(text: str, link_url: str | None = None,
              button: str = "공고 보기", access_token: str | None = None) -> None:
    """내 카카오톡으로 텍스트 메시지 전송 (text 최대 200자)."""
    from app.auth import base_url
    if link_url is None:
        link_url = base_url()
    token = access_token or access_token_from_refresh()
    template = {
        "object_type": "text",
        "text": text[:200],
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": button,
    }
    resp = requests.post(
        f"{API_HOST}/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if data.get("result_code") != 0:
        raise KakaoError(f"메시지 전송 실패: {data}")
