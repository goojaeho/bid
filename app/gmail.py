"""회사 Gmail 연동: OAuth(워크스페이스 내부 클라이언트) + 메일 동기화 + 캘린더 등록.

사이트 로그인용 클라이언트와 별개로, 메일 전용 환경변수를 사용한다:
  GOOGLE_MAIL_CLIENT_ID / GOOGLE_MAIL_CLIENT_SECRET  (GCP 내부 앱)
  GMAIL_PUSH_TOPIC   예: projects/my-proj/topics/gmail-push (없으면 수동 새로고침만)
  MAIL_PUSH_SECRET   푸시 엔드포인트 검증용 아무 문자열
"""
from __future__ import annotations

import base64
import html
import os
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from app import auth, todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")
TIMEOUT = 20
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3"
SCOPES = ("openid email "
          "https://www.googleapis.com/auth/gmail.readonly "
          "https://www.googleapis.com/auth/calendar.events")
MAX_FETCH = 20
BODY_CHARS = 4000


class GmailError(Exception):
    pass


def client_id() -> str:
    return os.environ.get("GOOGLE_MAIL_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("GOOGLE_MAIL_CLIENT_SECRET", "").strip()


def push_topic() -> str:
    return os.environ.get("GMAIL_PUSH_TOPIC", "").strip()


def push_secret() -> str:
    return os.environ.get("MAIL_PUSH_SECRET", "").strip()


def enabled() -> bool:
    return bool(client_id() and client_secret())


def callback_url() -> str:
    return f"{auth.base_url()}/gmail/callback"


def connect_url() -> str:
    params = {
        "client_id": client_id(),
        "redirect_uri": callback_url(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


# ------------------------------------------------------------ 토큰 저장/발급

def handle_callback(code: str) -> str:
    """인가 코드 → 토큰 교환 → refresh_token 저장, 연동된 이메일 반환."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "authorization_code", "client_id": client_id(),
              "client_secret": client_secret(), "redirect_uri": callback_url(),
              "code": code},
        timeout=TIMEOUT,
    )
    data = resp.json()
    refresh = data.get("refresh_token")
    id_token = data.get("id_token")
    if not refresh or not id_token:
        raise GmailError(f"토큰 교환 실패: {data.get('error_description') or data.get('error')}")
    info = requests.get("https://oauth2.googleapis.com/tokeninfo",
                        params={"id_token": id_token}, timeout=TIMEOUT).json()
    email = (info.get("email") or "").lower()
    if not email:
        raise GmailError("연동 계정 이메일을 확인하지 못했습니다.")
    todos._request(
        "POST", "google_tokens",
        params={"on_conflict": "email"},
        json={"email": email, "refresh_token": refresh,
              "updated_at": datetime.now(KST).isoformat()},
        headers={"Prefer": "resolution=merge-duplicates"},
    )
    _token_cache.pop(email, None)
    return email


def get_conf(email: str) -> dict | None:
    rows = todos._request(
        "GET", "google_tokens",
        params={"select": "email,refresh_token,history_id,watch_expiry",
                "email": f"eq.{email}"},
    ).json()
    return rows[0] if rows else None


def first_account() -> str | None:
    """연동된 회사 계정 (단일 사용자 전제 — 첫 행)."""
    try:
        rows = todos._request("GET", "google_tokens",
                              params={"select": "email", "limit": "1"}).json()
        return rows[0]["email"] if rows else None
    except StoreError:
        return None


def connected(email: str) -> bool:
    try:
        return get_conf(email) is not None
    except StoreError:
        return False


def disconnect(email: str) -> None:
    todos._request("DELETE", "google_tokens", params={"email": f"eq.{email}"})
    _token_cache.pop(email, None)


_token_cache: dict[str, tuple[float, str]] = {}


def access_token(email: str) -> str:
    cached = _token_cache.get(email)
    if cached and cached[0] > time.time():
        return cached[1]
    conf = get_conf(email)
    if not conf:
        raise GmailError("Gmail이 연동되어 있지 않습니다.")
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={"grant_type": "refresh_token", "client_id": client_id(),
              "client_secret": client_secret(),
              "refresh_token": conf["refresh_token"]},
        timeout=TIMEOUT,
    )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GmailError(f"액세스 토큰 발급 실패: {data.get('error_description') or data.get('error')}")
    _token_cache[email] = (time.time() + int(data.get("expires_in", 3600)) - 60, token)
    return token


def _api(email: str, method: str, url: str, **kw) -> dict:
    kw.setdefault("timeout", TIMEOUT)
    headers = {"Authorization": f"Bearer {access_token(email)}",
               **kw.pop("headers", {})}
    resp = requests.request(method, url, headers=headers, **kw)
    if resp.status_code == 404:
        raise GmailError("not_found")
    if resp.status_code >= 400:
        raise GmailError(f"구글 API 오류({resp.status_code}): {resp.text[:150]}")
    return resp.json() if resp.text else {}


def _patch_conf(email: str, fields: dict) -> None:
    fields["updated_at"] = datetime.now(KST).isoformat()
    todos._request("PATCH", "google_tokens",
                   params={"email": f"eq.{email}"}, json=fields)


# ------------------------------------------------------------ watch (푸시)

def start_watch(email: str) -> None:
    """Gmail 푸시 구독 시작/갱신 (7일 만료). 토픽 미설정 시 건너뜀."""
    if not push_topic():
        return
    data = _api(email, "POST", f"{GMAIL}/watch",
                json={"topicName": push_topic(), "labelIds": ["INBOX"],
                      "labelFilterBehavior": "INCLUDE"})
    fields: dict = {"watch_expiry": int(data.get("expiration") or 0)}
    conf = get_conf(email) or {}
    if not conf.get("history_id") and data.get("historyId"):
        fields["history_id"] = str(data["historyId"])
    _patch_conf(email, fields)


def ensure_watch(email: str) -> None:
    """만료 24시간 전이면 watch 갱신. 실패해도 조용히 넘어간다."""
    if not push_topic():
        return
    try:
        conf = get_conf(email)
        if not conf:
            return
        expiry_ms = int(conf.get("watch_expiry") or 0)
        if expiry_ms < (time.time() + 86400) * 1000:
            start_watch(email)
    except (GmailError, StoreError, ValueError):
        pass


# ------------------------------------------------------------ 메일 가져오기

def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64text(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def extract_body(payload: dict) -> str:
    """메시지 payload에서 본문 텍스트 추출 (text/plain 우선, html 폴백)."""
    plain, htm = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_b64text(data))
            elif mime == "text/html":
                htm.append(_b64text(data))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    text = "\n".join(plain).strip() or _strip_html("\n".join(htm))
    return re.sub(r"\n{3,}", "\n\n", text)[:BODY_CHARS]


def _parse_message(msg: dict) -> dict:
    payload = msg.get("payload") or {}
    try:
        received = datetime.fromtimestamp(
            int(msg.get("internalDate", 0)) / 1000, tz=KST).isoformat()
    except (ValueError, TypeError, OSError):
        received = None
    return {
        "gmail_id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "sender": _header(payload, "From")[:300],
        "subject": _header(payload, "Subject")[:300],
        "snippet": (msg.get("snippet") or "")[:300],
        "received_at": received,
        "body": extract_body(payload),
    }


def fetch_new(email: str) -> list[dict]:
    """마지막 history 이후의 새 메일 (없거나 만료 시 최근 3일 안읽음으로 폴백)."""
    conf = get_conf(email)
    if not conf:
        raise GmailError("Gmail이 연동되어 있지 않습니다.")
    ids: list[str] = []
    new_history = None
    hid = conf.get("history_id")
    if hid:
        try:
            page = None
            while True:
                params = {"startHistoryId": hid, "historyTypes": "messageAdded",
                          "labelId": "INBOX", "maxResults": 100}
                if page:
                    params["pageToken"] = page
                data = _api(email, "GET", f"{GMAIL}/history", params=params)
                new_history = data.get("historyId") or new_history
                for h in data.get("history", []):
                    for added in h.get("messagesAdded", []):
                        mid = (added.get("message") or {}).get("id")
                        if mid:
                            ids.append(mid)
                page = data.get("nextPageToken")
                if not page:
                    break
        except GmailError as e:
            if str(e) != "not_found":  # 404 = history 만료 → 폴백
                raise
            hid = None
    if not hid:
        data = _api(email, "GET", f"{GMAIL}/messages",
                    params={"q": "is:unread newer_than:3d in:inbox",
                            "maxResults": MAX_FETCH})
        ids = [m["id"] for m in data.get("messages", [])]
        prof = _api(email, "GET", f"{GMAIL}/profile")
        new_history = prof.get("historyId")

    seen: set[str] = set()
    ordered = [i for i in ids if not (i in seen or seen.add(i))][-MAX_FETCH:]
    # 이미 저장된 메일 제외
    if ordered:
        existing = todos._request(
            "GET", "mail_items",
            params={"select": "gmail_id",
                    "gmail_id": f"in.({','.join(ordered)})"},
        ).json()
        done = {r["gmail_id"] for r in existing}
        ordered = [i for i in ordered if i not in done]

    messages = []
    for mid in ordered:
        try:
            msg = _api(email, "GET", f"{GMAIL}/messages/{mid}",
                       params={"format": "full"})
            messages.append(_parse_message(msg))
        except GmailError:
            continue
    if new_history:
        _patch_conf(email, {"history_id": str(new_history)})
    return messages


# ------------------------------------------------------------ 저장/목록

ITEM_FIELDS = ("id,gmail_id,thread_id,sender,subject,snippet,received_at,"
               "category,summary,schedule,tasks,status")


def save_items(email: str, items: list[dict]) -> None:
    rows = [{
        "email": email,
        "gmail_id": it["gmail_id"],
        "thread_id": it.get("thread_id", ""),
        "sender": it.get("sender", ""),
        "subject": it.get("subject", ""),
        "snippet": it.get("snippet", ""),
        "received_at": it.get("received_at"),
        "category": it.get("category", "fyi"),
        "summary": it.get("summary", ""),
        "schedule": it.get("schedule"),
        "tasks": it.get("tasks") or [],
    } for it in items]
    if not rows:
        return
    todos._request("POST", "mail_items",
                   params={"on_conflict": "gmail_id"},
                   json=rows,
                   headers={"Prefer": "resolution=ignore-duplicates"})


def list_items(email: str, cat: str = "", limit: int = 100) -> list[dict]:
    params = {"select": ITEM_FIELDS, "email": f"eq.{email}",
              "status": "neq.dismissed",
              "order": "received_at.desc.nullslast", "limit": str(limit)}
    if cat:
        params["category"] = f"eq.{cat}"
    else:
        params["category"] = "neq.promo"
    return todos._request("GET", "mail_items", params=params).json()


def get_item(email: str, item_id: int) -> dict:
    rows = todos._request(
        "GET", "mail_items",
        params={"select": ITEM_FIELDS, "email": f"eq.{email}",
                "id": f"eq.{item_id}"},
    ).json()
    if not rows:
        raise StoreError("메일 항목을 찾을 수 없습니다.")
    return rows[0]


def set_status(email: str, item_id: int, status: str) -> None:
    todos._request("PATCH", "mail_items",
                   params={"email": f"eq.{email}", "id": f"eq.{item_id}"},
                   json={"status": status})


def counts(email: str) -> dict:
    """대시보드용: 미처리 답장필요/일정 건수."""
    rows = todos._request(
        "GET", "mail_items",
        params={"select": "category", "email": f"eq.{email}",
                "status": "eq.new", "category": "in.(reply_needed,schedule)",
                "limit": "200"},
    ).json()
    return {
        "reply": sum(1 for r in rows if r["category"] == "reply_needed"),
        "schedule": sum(1 for r in rows if r["category"] == "schedule"),
    }


# ------------------------------------------------------------ 캘린더 등록

def add_calendar_event(email: str, schedule: dict, description: str = "") -> str:
    title = str(schedule.get("title") or "일정").strip()[:200]
    date = str(schedule.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise GmailError("일정 날짜가 명확하지 않습니다. 캘린더에서 직접 등록해주세요.")
    time_s = str(schedule.get("time") or "").strip()
    body: dict = {"summary": title, "description": description[:1000]}
    if schedule.get("location"):
        body["location"] = str(schedule["location"])[:300]
    if re.match(r"^\d{2}:\d{2}$", time_s):
        start = datetime.fromisoformat(f"{date}T{time_s}:00")
        minutes = int(schedule.get("duration_min") or 60)
        end = start + timedelta(minutes=max(15, min(minutes, 480)))
        body["start"] = {"dateTime": start.isoformat(), "timeZone": "Asia/Seoul"}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": "Asia/Seoul"}
    else:
        body["start"] = {"date": date}
        body["end"] = {"date": date}
    data = _api(email, "POST", f"{CAL}/calendars/primary/events", json=body)
    return data.get("htmlLink", "")


# ------------------------------------------------------------ 동기화 (분석 포함)

CATEGORY_LABELS = {"reply_needed": "답장 필요", "schedule": "일정",
                   "fyi": "참고", "promo": "광고성"}


def sync(email: str) -> dict:
    """새 메일 수집 → Gemini 분석 → 저장 → 중요 메일 카카오 알림."""
    from app import kakao, summarize

    ensure_watch(email)
    fetched = fetch_new(email)
    if not fetched:
        return {"new": 0, "important": 0}
    analyzed = summarize.gemini_mail_analyze([
        {"id": m["gmail_id"], "sender": m["sender"], "subject": m["subject"],
         "body": m["body"]} for m in fetched
    ])
    by_id = {a.get("id"): a for a in analyzed if isinstance(a, dict)}
    for m in fetched:
        a = by_id.get(m["gmail_id"], {})
        m["category"] = a.get("category") if a.get("category") in CATEGORY_LABELS else "fyi"
        m["summary"] = str(a.get("summary") or m["snippet"])[:500]
        m["schedule"] = a.get("schedule") if isinstance(a.get("schedule"), dict) else None
        m["tasks"] = [str(t)[:200] for t in (a.get("tasks") or [])][:10]
    save_items(email, fetched)

    important = [m for m in fetched if m["category"] in ("reply_needed", "schedule")]
    for m in important[:5]:
        try:
            label = CATEGORY_LABELS[m["category"]]
            kakao.send_memo(f"[메일·{label}] {m['sender'].split('<')[0].strip()}: "
                            f"{m['subject']}\n{m['summary']}",
                            link_url=f"{auth.base_url()}/mail")
        except Exception:  # 카카오 미연동/만료 시 알림만 생략
            pass
    return {"new": len(fetched), "important": len(important)}
