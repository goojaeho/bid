"""개인 할 일 저장소 (Supabase todos 테이블) + 완료 통계.

테이블 (Supabase SQL Editor에서 1회 생성):
  create table todos (
    id bigint generated always as identity primary key,
    email text not null,
    title text not null,
    done boolean not null default false,
    created_at timestamptz not null default now(),
    done_at timestamptz
  );
  create index on todos (email, done, created_at desc);
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from app.store import StoreError, _sb_headers, _supabase_conf

KST = ZoneInfo("Asia/Seoul")
TIMEOUT = 10
MAX_TITLE = 200


def enabled() -> bool:
    return _supabase_conf() is not None


def _request(method: str, path: str, **kw) -> requests.Response:
    conf = _supabase_conf()
    if not conf:
        raise StoreError("Supabase가 설정되지 않았습니다.")
    url, key = conf
    kw.setdefault("timeout", TIMEOUT)
    headers = {**_sb_headers(key), **kw.pop("headers", {})}
    resp = requests.request(method, f"{url}/rest/v1/{path}", headers=headers, **kw)
    if resp.status_code >= 400:
        raise StoreError(f"todos {method} 실패({resp.status_code}): {resp.text[:150]}")
    return resp


def list_todos(email: str) -> dict:
    """미완료 전체 + 최근 완료 20건."""
    pending = _request(
        "GET", "todos",
        params={"select": "id,title,created_at", "email": f"eq.{email}",
                "done": "is.false", "order": "created_at.desc", "limit": "100"},
    ).json()
    done = _request(
        "GET", "todos",
        params={"select": "id,title,done_at", "email": f"eq.{email}",
                "done": "is.true", "order": "done_at.desc", "limit": "20"},
    ).json()
    return {"pending": pending, "done": done}


def add_todo(email: str, title: str) -> None:
    title = (title or "").strip()[:MAX_TITLE]
    if not title:
        raise StoreError("할 일 내용이 비어 있습니다.")
    _request("POST", "todos", json={"email": email, "title": title})


def toggle_todo(email: str, todo_id: int) -> None:
    rows = _request(
        "GET", "todos",
        params={"select": "id,done", "email": f"eq.{email}", "id": f"eq.{todo_id}"},
    ).json()
    if not rows:
        raise StoreError("해당 할 일을 찾을 수 없습니다.")
    now_done = not rows[0]["done"]
    _request(
        "PATCH", "todos",
        params={"email": f"eq.{email}", "id": f"eq.{todo_id}"},
        json={"done": now_done,
              "done_at": datetime.now(KST).isoformat() if now_done else None},
    )


def delete_todo(email: str, todo_id: int) -> None:
    _request("DELETE", "todos",
             params={"email": f"eq.{email}", "id": f"eq.{todo_id}"})


def compute_stats(done_dates: list[str], pending_count: int,
                  today: datetime | None = None) -> dict:
    """완료일 목록(ISO 문자열)으로 통계 집계. 순수 함수 (테스트 용이)."""
    now = today or datetime.now(KST)
    today_d = now.date()
    week_start = today_d - timedelta(days=today_d.weekday())
    month_start = today_d.replace(day=1)

    days = [(today_d - timedelta(days=i)) for i in range(13, -1, -1)]
    counter = Counter()
    week = month = today_count = 0
    for raw in done_dates:
        try:
            d = datetime.fromisoformat(raw).astimezone(KST).date()
        except (ValueError, TypeError):
            continue
        counter[d] += 1
        if d == today_d:
            today_count += 1
        if d >= week_start:
            week += 1
        if d >= month_start:
            month += 1

    return {
        "today": today_count,
        "week": week,
        "month": month,
        "pending": pending_count,
        "daily": [{"date": d.isoformat(), "label": f"{d.month}/{d.day}",
                   "count": counter.get(d, 0)} for d in days],
    }


def stats(email: str, pending_count: int) -> dict:
    since = (datetime.now(KST) - timedelta(days=35)).isoformat()
    rows = _request(
        "GET", "todos",
        params={"select": "done_at", "email": f"eq.{email}",
                "done": "is.true", "done_at": f"gte.{since}", "limit": "1000"},
    ).json()
    return compute_stats([r["done_at"] for r in rows if r.get("done_at")], pending_count)
