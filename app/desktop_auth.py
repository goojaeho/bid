"""Scoped desktop credentials, separate from browser cookies."""
import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode
from app import auth


def seal(kind: str, fields: dict, ttl: int) -> str:
    if not auth.enabled():
        raise ValueError("로그인 설정이 필요합니다.")
    payload = {**fields, "kind": kind, "exp": int(time.time()) + ttl}
    data = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return data + "." + auth._sign(("jarvis-desktop:" + data).encode())


def read(value: str, kind: str) -> dict:
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("잘못된 인증 정보")
    try:
        data, signature = value.rsplit(".", 1)
        expected = auth._sign(("jarvis-desktop:" + data).encode())
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        fields = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
        if fields["kind"] != kind or fields["exp"] <= time.time():
            raise ValueError()
        return fields
    except Exception as exc:
        raise ValueError("인증 정보가 만료됐거나 올바르지 않습니다.") from exc


def start(port: int, nonce: str, challenge: str) -> str:
    if not 1024 <= port <= 65535 or not re.fullmatch(r"[a-f0-9]{64}", nonce) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
        raise ValueError("잘못된 앱 로그인 요청")
    state = "desktop." + seal("state", {"port": port, "nonce": nonce, "challenge": challenge}, 300)
    return auth.login_url() + "&" + urlencode({"state": state})


def finish(state: str, email: str) -> str:
    data = read(state.removeprefix("desktop."), "state")
    if not auth.is_owner(email):
        raise ValueError("소유자 계정으로 로그인해주세요.")
    grant = seal("grant", {"email": email, "challenge": data["challenge"]}, 60)
    return f"http://127.0.0.1:{data['port']}/callback?" + urlencode({"code": grant, "state": data["nonce"]})


def exchange(grant: str, verifier: str) -> str:
    data = read(grant, "grant")
    if not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier):
        raise ValueError("잘못된 앱 인증")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if not hmac.compare_digest(challenge, data["challenge"]) or not auth.is_owner(data["email"]):
        raise ValueError("잘못된 앱 인증")
    return seal("access", {"email": data["email"]}, auth.SESSION_MAX_AGE)


def owner(header: str) -> str | None:
    if not header.startswith("Bearer "):
        return None
    try:
        email = read(header[7:], "access")["email"]
        return email if auth.is_owner(email) else None
    except (ValueError, KeyError):
        return None
