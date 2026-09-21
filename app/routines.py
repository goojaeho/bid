"""매일 반복 루틴 (운동·공부 등) 저장소 + 연속일 계산.

routines.weekdays: 실행 요일을 숫자 문자열로 저장 (0=월 … 6=일).
  매일 = "0123456", 월수금 = "024"
routine_logs: (routine_id, day) 쌍으로 완료 기록. 체크 해제 시 행 삭제.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app import todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")
WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]
AREAS = {"work": "업무", "personal": "개인"}
FIELDS = "id,title,area,category,weekdays,goal,active,created_at"
MAX_STREAK_LOOKBACK = 400  # 연속일 계산 상한 (무한 루프 방지)


def today_kst() -> date:
    return datetime.now(KST).date()


def _clean_weekdays(raw) -> str:
    """'024' 또는 [0,2,4] → 정렬된 '024'. 빈 값이면 매일."""
    if isinstance(raw, (list, tuple)):
        chars = [str(x) for x in raw]
    else:
        chars = list(str(raw or ""))
    days = sorted({c for c in chars if c in "0123456"})
    return "".join(days) or "0123456"


def weekdays_label(weekdays: str) -> str:
    days = _clean_weekdays(weekdays)
    if days == "0123456":
        return "매일"
    if days == "01234":
        return "평일"
    if days == "56":
        return "주말"
    return "·".join(WEEKDAY_LABELS[int(d)] for d in days)


def runs_on(weekdays: str, day: date) -> bool:
    return str(day.weekday()) in _clean_weekdays(weekdays)


# ------------------------------------------------------------ CRUD

def list_routines(email: str, include_inactive: bool = False) -> list[dict]:
    params = {"select": FIELDS, "email": f"eq.{email}",
              "order": "area.asc,created_at.asc", "limit": "100"}
    if not include_inactive:
        params["active"] = "is.true"
    return todos._request("GET", "routines", params=params).json()


def add_routine(email: str, title: str, area: str = "personal",
                category: str = "", weekdays="0123456", goal: str = "") -> dict:
    title = (title or "").strip()[:100]
    if not title:
        raise StoreError("루틴 이름이 비어 있습니다.")
    row = {
        "email": email,
        "title": title,
        "area": area if area in AREAS else "personal",
        "category": (category or "").strip()[:50],
        "weekdays": _clean_weekdays(weekdays),
        "goal": (goal or "").strip()[:50],
    }
    resp = todos._request("POST", "routines", json=row,
                          headers={"Prefer": "return=representation"})
    return resp.json()[0]


def update_routine(email: str, routine_id: int, fields: dict) -> None:
    allowed: dict = {}
    if "title" in fields:
        title = str(fields["title"]).strip()[:100]
        if title:
            allowed["title"] = title
    if fields.get("area") in AREAS:
        allowed["area"] = fields["area"]
    if "category" in fields:
        allowed["category"] = str(fields["category"] or "").strip()[:50]
    if "weekdays" in fields:
        allowed["weekdays"] = _clean_weekdays(fields["weekdays"])
    if "goal" in fields:
        allowed["goal"] = str(fields["goal"] or "").strip()[:50]
    if "active" in fields:
        allowed["active"] = bool(fields["active"])
    if not allowed:
        raise StoreError("수정할 내용이 없습니다.")
    todos._request("PATCH", "routines",
                   params={"email": f"eq.{email}", "id": f"eq.{routine_id}"},
                   json=allowed)


def delete_routine(email: str, routine_id: int) -> None:
    todos._request("DELETE", "routine_logs",
                   params={"email": f"eq.{email}", "routine_id": f"eq.{routine_id}"})
    todos._request("DELETE", "routines",
                   params={"email": f"eq.{email}", "id": f"eq.{routine_id}"})


# ------------------------------------------------------------ 체크 기록

def fetch_logs(email: str, since: date, until: date | None = None) -> list[dict]:
    params = {"select": "routine_id,day", "email": f"eq.{email}",
              "day": f"gte.{since.isoformat()}", "order": "day.desc",
              "limit": "2000"}
    rows = todos._request("GET", "routine_logs", params=params).json()
    if until:
        rows = [r for r in rows if r["day"] <= until.isoformat()]
    return rows


def set_log(email: str, routine_id: int, day: date, done: bool) -> None:
    if done:
        todos._request("POST", "routine_logs",
                       params={"on_conflict": "routine_id,day"},
                       json={"routine_id": routine_id, "day": day.isoformat(),
                             "email": email},
                       headers={"Prefer": "resolution=ignore-duplicates"})
    else:
        todos._request("DELETE", "routine_logs",
                       params={"email": f"eq.{email}",
                               "routine_id": f"eq.{routine_id}",
                               "day": f"eq.{day.isoformat()}"})


# ------------------------------------------------- 통계 (순수 함수)

def compute_streak(weekdays: str, done_days: set, today: date) -> int:
    """실행 요일만 따져 오늘(또는 어제)부터 거슬러 올라간 연속 수행 횟수.

    오늘이 실행일인데 아직 안 했으면 어제까지의 연속을 유지한 것으로 본다.
    """
    days = _clean_weekdays(weekdays)
    streak = 0
    d = today
    if runs_on(days, d) and d.isoformat() not in done_days:
        d -= timedelta(days=1)  # 오늘은 아직 기회가 남음
    for _ in range(MAX_STREAK_LOOKBACK):
        if not runs_on(days, d):
            d -= timedelta(days=1)
            continue
        if d.isoformat() in done_days:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak


def compute_week(weekdays: str, done_days: set, today: date) -> tuple[int, int]:
    """이번 주(월~오늘) 예정 횟수 대비 완료 횟수."""
    start = today - timedelta(days=today.weekday())
    planned = done = 0
    d = start
    while d <= today:
        if runs_on(weekdays, d):
            planned += 1
            if d.isoformat() in done_days:
                done += 1
        d += timedelta(days=1)
    return done, planned


def today_view(email: str, day: date | None = None) -> dict:
    """오늘 해야 할 루틴 + 완료 여부 + 연속일. 화면·알림 공용."""
    day = day or today_kst()
    routines = list_routines(email)
    if not routines:
        return {"items": [], "done": 0, "total": 0, "day": day.isoformat()}
    logs = fetch_logs(email, day - timedelta(days=MAX_STREAK_LOOKBACK), day)
    by_routine: dict[int, set] = {}
    for row in logs:
        by_routine.setdefault(row["routine_id"], set()).add(row["day"])

    items = []
    for r in routines:
        if not runs_on(r.get("weekdays", "0123456"), day):
            continue
        done_days = by_routine.get(r["id"], set())
        week_done, week_planned = compute_week(r.get("weekdays", "0123456"),
                                               done_days, day)
        items.append({
            "id": r["id"],
            "title": r["title"],
            "area": r.get("area", "personal"),
            "category": r.get("category", ""),
            "goal": r.get("goal", ""),
            "weekdays": r.get("weekdays", "0123456"),
            "done": day.isoformat() in done_days,
            "streak": compute_streak(r.get("weekdays", "0123456"), done_days, day),
            "week_done": week_done,
            "week_planned": week_planned,
        })
    done_count = sum(1 for i in items if i["done"])
    return {"items": items, "done": done_count, "total": len(items),
            "day": day.isoformat()}


def counts(email: str) -> dict:
    """대시보드용 요약 (실패해도 화면을 막지 않도록 호출측에서 감싼다)."""
    view = today_view(email)
    return {"done": view["done"], "total": view["total"]}
