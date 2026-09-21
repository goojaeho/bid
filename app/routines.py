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
FIELDS = "id,title,area,category,weekdays,weekly_goal,goal,active,created_at"
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


def weekdays_label(weekdays: str, weekly_goal: int = 0) -> str:
    if weekly_goal:
        return f"주 {weekly_goal}회"
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


def _clean_goal(raw) -> int:
    """주 N회 목표 (0 = 요일 지정 방식)."""
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    return n if 1 <= n <= 7 else 0


def add_routine(email: str, title: str, area: str = "personal",
                category: str = "", weekdays="0123456", goal: str = "",
                weekly_goal=0) -> dict:
    title = (title or "").strip()[:100]
    if not title:
        raise StoreError("루틴 이름이 비어 있습니다.")
    row = {
        "email": email,
        "title": title,
        "area": area if area in AREAS else "personal",
        "category": (category or "").strip()[:50],
        "weekdays": _clean_weekdays(weekdays),
        "weekly_goal": _clean_goal(weekly_goal),
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
    if "weekly_goal" in fields:
        allowed["weekly_goal"] = _clean_goal(fields["weekly_goal"])
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


def compute_week_streak(done_days: set, weekly_goal: int, today: date) -> int:
    """주 N회 루틴의 연속 '주' 수. 이번 주가 아직 미달이면 지난주까지로 센다."""
    if weekly_goal <= 0:
        return 0

    def count(start: date, end: date) -> int:
        n = 0
        d = start
        while d <= end:
            if d.isoformat() in done_days:
                n += 1
            d += timedelta(days=1)
        return n

    start = today - timedelta(days=today.weekday())
    streak = 1 if count(start, today) >= weekly_goal else 0
    start -= timedelta(days=7)
    for _ in range(52):
        if count(start, start + timedelta(days=6)) >= weekly_goal:
            streak += 1
            start -= timedelta(days=7)
        else:
            break
    return streak


def today_view(email: str, day: date | None = None) -> dict:
    """오늘 해야 할 루틴 + 완료 여부 + 연속일. 화면·알림 공용."""
    day = day or today_kst()
    routines_rows = list_routines(email)
    if not routines_rows:
        return {"items": [], "done": 0, "total": 0, "day": day.isoformat()}
    logs = fetch_logs(email, day - timedelta(days=MAX_STREAK_LOOKBACK), day)
    by_routine: dict[int, set] = {}
    for row in logs:
        by_routine.setdefault(row["routine_id"], set()).add(row["day"])

    items = []
    for r in routines_rows:
        wk_goal = int(r.get("weekly_goal") or 0)
        weekdays = r.get("weekdays", "0123456")
        # 주 N회 루틴은 매일 후보로 띄우고 주간 달성으로 판단한다
        if not wk_goal and not runs_on(weekdays, day):
            continue
        done_days = by_routine.get(r["id"], set())
        if wk_goal:
            week_done, week_planned = compute_week(
                "0123456", done_days, day)[0], wk_goal
            streak = compute_week_streak(done_days, wk_goal, day)
            streak_text = f"{streak}주" if streak else "0"
        else:
            week_done, week_planned = compute_week(weekdays, done_days, day)
            streak = compute_streak(weekdays, done_days, day)
            streak_text = str(streak)
        today_done = day.isoformat() in done_days
        goal_met = bool(wk_goal) and week_done >= wk_goal
        items.append({
            "id": r["id"],
            "title": r["title"],
            "area": r.get("area", "personal"),
            "category": r.get("category", ""),
            "goal": r.get("goal", ""),
            "weekdays": weekdays,
            "weekly_goal": wk_goal,
            "done": today_done,
            "goal_met": goal_met,
            "streak": streak,
            "streak_text": streak_text,
            "week_done": week_done,
            "week_planned": week_planned,
        })
    # 주 N회는 이번 주 목표를 채웠으면 오늘 안 해도 달성으로 본다
    done_count = sum(1 for i in items if i["done"] or i["goal_met"])
    return {"items": items, "done": done_count, "total": len(items),
            "day": day.isoformat()}


def counts(email: str) -> dict:
    """대시보드용 요약 (실패해도 화면을 막지 않도록 호출측에서 감싼다)."""
    view = today_view(email)
    return {"done": view["done"], "total": view["total"]}
