"""개인 할 일 저장소 (Supabase todos 테이블) + 완료 통계.

테이블 (Supabase SQL Editor에서 생성/확장):
  create table todos (
    id bigint generated always as identity primary key,
    email text not null,
    title text not null,
    done boolean not null default false,
    created_at timestamptz not null default now(),
    done_at timestamptz
  );
  alter table todos add column if not exists area text not null default 'work';
  alter table todos add column if not exists category text not null default '';
  alter table todos add column if not exists due_date date;
  alter table todos add column if not exists priority int not null default 2;
  alter table todos add column if not exists parent_id bigint;
  create index if not exists todos_email_idx on todos (email, done, due_date);

area: 'work'(업무) | 'personal'(개인)
priority: 1(높음) | 2(보통) | 3(낮음)
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from app.store import StoreError, _sb_headers, _supabase_conf

KST = ZoneInfo("Asia/Seoul")
TIMEOUT = 10
MAX_TITLE = 200

AREAS = {"work": "업무", "personal": "개인"}
PRIORITIES = {1: "높음", 2: "보통", 3: "낮음"}

FIELDS = "id,title,area,category,due_date,priority,parent_id,created_at"


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


# ------------------------------------------------------- 자연어 날짜 파싱

_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def parse_nl_date(title: str, today: date | None = None) -> tuple[str, str | None]:
    """제목에서 한국어 날짜 표현을 찾아 (정리된 제목, YYYY-MM-DD|None) 반환.

    지원: 오늘/내일/모레, (이번주|다음주)?X요일, N월N일, N/N — 뒤에 '까지' 허용.
    """
    today = today or datetime.now(KST).date()
    text = title

    def cleanup(matched: str) -> str:
        out = text.replace(matched, " ")
        return re.sub(r"\s+", " ", out).strip(" ,")

    m = re.search(r"(오늘|내일|모레)(까지)?", text)
    if m:
        offset = {"오늘": 0, "내일": 1, "모레": 2}[m.group(1)]
        return cleanup(m.group(0)), (today + timedelta(days=offset)).isoformat()

    m = re.search(r"(이번\s?주|다음\s?주)?\s*([월화수목금토일])요일(까지)?", text)
    if m:
        wd = _WEEKDAYS[m.group(2)]
        qualifier = m.group(1) or ""
        if "다음" in qualifier:
            # 다음주 X요일 = 다음 주(월요일 시작)의 해당 요일
            delta = 7 - today.weekday() + wd
        elif "이번" in qualifier:
            delta = wd - today.weekday()  # 이번주 X요일 (지났으면 지난 날짜)
        else:
            delta = (wd - today.weekday()) % 7  # 'X요일' = 돌아오는 요일(오늘 포함)
        return cleanup(m.group(0)), (today + timedelta(days=delta)).isoformat()

    m = re.search(r"(\d{1,2})월\s?(\d{1,2})일(까지)?", text)
    if not m:
        m = re.search(r"\b(\d{1,2})/(\d{1,2})(까지)?\b", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            due = date(today.year, month, day)
        except ValueError:
            return title.strip(), None
        if due < today:  # 지난 날짜면 내년으로
            due = date(today.year + 1, month, day)
        return cleanup(m.group(0)), due.isoformat()

    return title.strip(), None


# ------------------------------------------------------------ CRUD

def list_todos(email: str) -> dict:
    """미완료 전체(마감 임박순) + 최근 완료 30건."""
    pending = _request(
        "GET", "todos",
        params={"select": FIELDS, "email": f"eq.{email}", "done": "is.false",
                "order": "due_date.asc.nullslast,priority.asc,created_at.asc",
                "limit": "200"},
    ).json()
    done = _request(
        "GET", "todos",
        params={"select": FIELDS + ",done_at", "email": f"eq.{email}",
                "done": "is.true", "order": "done_at.desc", "limit": "30"},
    ).json()
    return {"pending": pending, "done": done}


def add_todo(email: str, title: str, area: str = "work", category: str = "",
             due_date: str | None = None, priority: int = 2,
             parent_id: int | None = None, parse_date: bool = True) -> dict:
    title = (title or "").strip()[:MAX_TITLE]
    if not title:
        raise StoreError("할 일 내용이 비어 있습니다.")
    if parse_date and not due_date:
        title, due_date = parse_nl_date(title)
        if not title:
            raise StoreError("할 일 내용이 비어 있습니다.")
    row = {
        "email": email,
        "title": title,
        "area": area if area in AREAS else "work",
        "category": (category or "").strip()[:50],
        "due_date": due_date or None,
        "priority": priority if priority in PRIORITIES else 2,
        "parent_id": parent_id,
    }
    resp = _request("POST", "todos", json=row,
                    headers={"Prefer": "return=representation"})
    created = resp.json()
    return created[0] if created else row


def update_todo(email: str, todo_id: int, fields: dict) -> None:
    allowed = {}
    if "title" in fields:
        title = str(fields["title"]).strip()[:MAX_TITLE]
        if title:
            allowed["title"] = title
    if "due_date" in fields:
        allowed["due_date"] = fields["due_date"] or None
    if "priority" in fields and fields["priority"] in (1, 2, 3):
        allowed["priority"] = fields["priority"]
    if "category" in fields:
        allowed["category"] = str(fields["category"]).strip()[:50]
    if "area" in fields and fields["area"] in AREAS:
        allowed["area"] = fields["area"]
    if not allowed:
        raise StoreError("수정할 내용이 없습니다.")
    _request("PATCH", "todos",
             params={"email": f"eq.{email}", "id": f"eq.{todo_id}"},
             json=allowed)


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


# ------------------------------------------------------------ 통계

def compute_stats(done_rows: list[dict], pending_count: int,
                  today: datetime | None = None) -> dict:
    """완료 행 목록([{done_at, area}])으로 통계 집계. 순수 함수."""
    now = today or datetime.now(KST)
    today_d = now.date()
    week_start = today_d - timedelta(days=today_d.weekday())
    month_start = today_d.replace(day=1)

    days = [(today_d - timedelta(days=i)) for i in range(13, -1, -1)]
    per_day: dict[str, Counter] = {"work": Counter(), "personal": Counter()}
    totals = {"today": 0, "week": 0, "month": 0,
              "today_work": 0, "today_personal": 0,
              "week_work": 0, "week_personal": 0}

    for row in done_rows:
        raw = row.get("done_at")
        area = row.get("area") if row.get("area") in AREAS else "work"
        try:
            d = datetime.fromisoformat(raw).astimezone(KST).date()
        except (ValueError, TypeError):
            continue
        per_day[area][d] += 1
        if d == today_d:
            totals["today"] += 1
            totals[f"today_{area}"] += 1
        if d >= week_start:
            totals["week"] += 1
            totals[f"week_{area}"] += 1
        if d >= month_start:
            totals["month"] += 1

    return {
        **totals,
        "pending": pending_count,
        "daily": [{
            "date": d.isoformat(),
            "label": f"{d.month}/{d.day}",
            "work": per_day["work"].get(d, 0),
            "personal": per_day["personal"].get(d, 0),
        } for d in days],
    }


def stats(email: str, pending_count: int) -> dict:
    since = (datetime.now(KST) - timedelta(days=35)).isoformat()
    rows = _request(
        "GET", "todos",
        params={"select": "done_at,area", "email": f"eq.{email}",
                "done": "is.true", "done_at": f"gte.{since}", "limit": "1000"},
    ).json()
    return compute_stats(rows, pending_count)
