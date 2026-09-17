"""First trial: explicit task commands and a briefing from the existing store."""
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import todos


def command(email: str, text: str) -> dict:
    text = re.sub(r"^자비스[야,\s]*", "", text.strip()).rstrip(".!?。 ")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    if text in ("브리핑", "브리핑해봐", "브리핑 해봐", "브리핑해줘", "브리핑 해줘", "오늘 할 일 알려줘"):
        pending = todos.list_todos(email)["pending"]
        dated = sorted((t for t in pending if t.get("due_date")), key=lambda t: t["due_date"])
        relevant = [t for t in dated if t["due_date"] <= (today + timedelta(days=7)).isoformat()]
        lines = [f"미완료 할 일은 {len(pending)}건입니다."]
        for task in relevant[:10]:
            due = task["due_date"]
            label = "기한 지남" if due < today.isoformat() else ("오늘 마감" if due == today.isoformat() else due + " 마감")
            lines.append(f"{label}, {task['title']}.")
        if not relevant:
            lines.append("오늘부터 7일 이내 마감하거나 기한이 지난 할 일은 없습니다.")
        if len(relevant) > 10:
            lines.append(f"그 밖에 {len(relevant) - 10}건은 할 일 화면에서 확인할 수 있습니다.")
        lines.append("이번 시험 브리핑에는 구글 캘린더와 메일은 포함되지 않습니다.")
        return {"ok": True, "kind": "briefing", "message": " ".join(lines)}
    match = re.fullmatch(r"(.+?)\s*(?:등록해\s*줘|등록해|추가해\s*줘|추가해)", text)
    if not match:
        return {"ok": False, "message": "‘내일 보고서 작성 할 일 등록해줘’ 또는 ‘브리핑해봐’라고 말해주세요."}
    title = re.sub(r"\s*할\s*일(?:로|에)?\s*$", "", match[1]).strip()
    if re.search(r"\d+\s*시|오전|오후|미팅|약속|회의(?!\s*준비)|취소|삭제|변경", title):
        return {"ok": False, "message": "이번 시험은 날짜가 있는 할 일 추가만 지원합니다. 시간 지정 약속과 변경은 아직 처리하지 않습니다."}
    if not title or len(title) > 200:
        return {"ok": False, "message": "할 일 내용을 1~200자로 말씀해주세요."}
    created = todos.add_todo(email, title)
    return {"ok": True, "kind": "created", "todo": created,
            "message": f"{created.get('due_date') or '마감일 없이'}, {created['title']} 할 일을 등록했습니다."}
