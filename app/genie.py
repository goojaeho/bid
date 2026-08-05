"""지니 대화 저장소 (Supabase genie_chats 테이블).

대화방별로 messages(jsonb)를 통째로 저장한다.
messages: [{"role": "user"|"model", "text": str}, ...]
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")
MAX_TITLE = 60
MAX_MESSAGES = 200  # 대화방당 저장 상한 (초과 시 오래된 턴부터 버림)


def enabled() -> bool:
    return todos.enabled()


def list_chats(email: str) -> list[dict]:
    return todos._request(
        "GET", "genie_chats",
        params={"select": "id,title,updated_at", "email": f"eq.{email}",
                "order": "updated_at.desc", "limit": "50"},
    ).json()


def get_chat(email: str, chat_id: int) -> dict:
    rows = todos._request(
        "GET", "genie_chats",
        params={"select": "id,title,messages", "email": f"eq.{email}",
                "id": f"eq.{chat_id}"},
    ).json()
    if not rows:
        raise StoreError("대화를 찾을 수 없습니다.")
    return rows[0]


def create_chat(email: str, title: str, messages: list[dict]) -> int:
    resp = todos._request(
        "POST", "genie_chats",
        json={"email": email,
              "title": (title or "새 대화").strip()[:MAX_TITLE],
              "messages": messages[-MAX_MESSAGES:]},
        headers={"Prefer": "return=representation"},
    )
    rows = resp.json()
    return rows[0]["id"]


def save_messages(email: str, chat_id: int, messages: list[dict]) -> None:
    todos._request(
        "PATCH", "genie_chats",
        params={"email": f"eq.{email}", "id": f"eq.{chat_id}"},
        json={"messages": messages[-MAX_MESSAGES:],
              "updated_at": datetime.now(KST).isoformat()},
    )


def delete_chat(email: str, chat_id: int) -> None:
    todos._request("DELETE", "genie_chats",
                   params={"email": f"eq.{email}", "id": f"eq.{chat_id}"})
