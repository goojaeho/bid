"""회의록 저장소 (Supabase meetings 테이블).

녹음 조각이 전사될 때마다 transcript에 이어붙이고,
종료 시 Gemini 회의록(minutes jsonb)을 저장한다.
"""
from __future__ import annotations

from app import todos
from app.store import StoreError

FIELDS = "id,title,mode,duration_sec,created_at,minutes"


def create_meeting(email: str, mode: str) -> dict:
    resp = todos._request(
        "POST", "meetings",
        json={"email": email, "mode": mode if mode in ("offline", "online") else "offline"},
        headers={"Prefer": "return=representation"},
    )
    return resp.json()[0]


def get_meeting(email: str, meeting_id: int) -> dict:
    rows = todos._request(
        "GET", "meetings",
        params={"select": FIELDS + ",transcript", "email": f"eq.{email}",
                "id": f"eq.{meeting_id}"},
    ).json()
    if not rows:
        raise StoreError("회의를 찾을 수 없습니다.")
    return rows[0]


def append_transcript(email: str, meeting_id: int, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    current = get_meeting(email, meeting_id).get("transcript") or ""
    merged = (current + "\n\n" + text).strip()[:200000]
    todos._request("PATCH", "meetings",
                   params={"email": f"eq.{email}", "id": f"eq.{meeting_id}"},
                   json={"transcript": merged})


def finish_meeting(email: str, meeting_id: int, title: str,
                   minutes: dict, duration_sec: int) -> None:
    todos._request("PATCH", "meetings",
                   params={"email": f"eq.{email}", "id": f"eq.{meeting_id}"},
                   json={"title": (title or "회의록").strip()[:200],
                         "minutes": minutes,
                         "duration_sec": max(0, int(duration_sec or 0))})


def list_meetings(email: str) -> list[dict]:
    return todos._request(
        "GET", "meetings",
        params={"select": FIELDS, "email": f"eq.{email}",
                "order": "created_at.desc", "limit": "100"},
    ).json()


def delete_meeting(email: str, meeting_id: int) -> None:
    todos._request("DELETE", "meetings",
                   params={"email": f"eq.{email}", "id": f"eq.{meeting_id}"})
