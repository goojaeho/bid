"""비즈니스 영어 회화 학습 (스피킹 탭) 데이터 계층.

시나리오 커리큘럼 프리셋 + 세션/스크립트/암기카드 CRUD (Supabase).
카드는 Leitner 간격 반복(상자 1~5, 간격 0/1/3/7/21일).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")

# ------------------------------------------------- 커리큘럼 (시나리오 프리셋)

LEVELS = {1: "기본기", 2: "부스 실전", 3: "IR 심화", 4: "프리토킹"}

_BASE_RULES = (
    "Stay in character and in English. Keep each reply to 2-4 sentences and "
    "always end with a question or prompt so the learner keeps speaking. "
    "The learner is a Korean startup founder practicing business English. "
    "Do not correct their English during the conversation — corrections come later."
)

SCENARIOS: dict[str, dict] = {
    "intro": {
        "level": 1, "title": "자기소개 30초",
        "desc": "처음 만난 자리에서 나를 소개하기",
        "persona": "You are a friendly professional meeting the learner for the first time "
                   "at a business event. Start by greeting them and asking them to "
                   "introduce themselves. Ask natural follow-up questions about their "
                   "background and role. " + _BASE_RULES,
        "goals": ["I'm in charge of ...", "Our company specializes in ...",
                  "It's great to finally meet you"],
    },
    "company": {
        "level": 1, "title": "회사·서비스 1분 피치",
        "desc": "우리 회사와 서비스를 1분 안에 설명하기",
        "persona": "You are a curious business acquaintance. Ask the learner what their "
                   "company does, then dig deeper: who are the customers, what problem "
                   "does it solve, what makes it different. " + _BASE_RULES,
        "goals": ["We help X do Y", "What sets us apart is ...", "Our main customers are ..."],
    },
    "smalltalk": {
        "level": 1, "title": "전시회 스몰토크",
        "desc": "인사, 날씨, 행사 이야기로 어색함 깨기",
        "persona": "You are an attendee at a global tech exhibition making small talk "
                   "with the learner near the coffee stand. Chat about the event, travel, "
                   "food, and light topics. " + _BASE_RULES,
        "goals": ["How's the event going for you?", "Is this your first time in ...?",
                  "We should stay in touch"],
    },
    "booth": {
        "level": 2, "title": "부스 방문객 응대",
        "desc": "부스에 온 방문객에게 제품 설명하기",
        "persona": "You are a visitor walking up to the learner's exhibition booth. "
                   "You know nothing about their product. Ask what it is, how it works, "
                   "who uses it, and show realistic interest or skepticism. " + _BASE_RULES,
        "goals": ["Let me walk you through ...", "Would you like a quick demo?",
                  "May I ask what brings you here today?"],
    },
    "demo": {
        "level": 2, "title": "데모 시연 설명",
        "desc": "제품 데모를 단계별로 보여주며 설명하기",
        "persona": "You are a potential customer watching the learner demo their product. "
                   "Ask them to show you the main features step by step, and interrupt "
                   "with practical questions (pricing, integration, setup time). " + _BASE_RULES,
        "goals": ["First, let me show you ...", "As you can see here ...",
                  "This feature allows you to ..."],
    },
    "pricing": {
        "level": 2, "title": "가격·파트너십 문의 대응",
        "desc": "가격, 조건, 협력 제안에 답하기",
        "persona": "You are a business development manager interested in the learner's "
                   "product. Negotiate: ask about pricing plans, discounts, partnership "
                   "models, and next steps. Push back politely on vague answers. " + _BASE_RULES,
        "goals": ["Our pricing starts at ...", "We're open to discussing ...",
                  "Let me get back to you on that"],
    },
    "followup": {
        "level": 2, "title": "명함 교환·팔로업 약속",
        "desc": "미팅 마무리와 다음 약속 잡기",
        "persona": "You are wrapping up a good booth conversation with the learner. "
                   "Exchange contact info and discuss concrete next steps: a follow-up "
                   "call, sending materials, scheduling a meeting. " + _BASE_RULES,
        "goals": ["Here's my card", "I'll send you the deck by ...",
                  "Does next Tuesday work for you?"],
    },
    "pitch": {
        "level": 3, "title": "투자자 미팅 피칭",
        "desc": "문제·솔루션·시장을 투자자에게 피칭",
        "persona": "You are a partner at a global venture capital fund taking a first "
                   "meeting with the learner. Ask them to pitch: the problem, solution, "
                   "market size, and traction. Probe for specifics and numbers. " + _BASE_RULES,
        "goals": ["The problem we're solving is ...", "Our market is worth ...",
                  "We've grown X% over ..."],
    },
    "qna": {
        "level": 3, "title": "투자자 Q&A",
        "desc": "BM·경쟁사·팀·지표 질문에 답하기",
        "persona": "You are a sharp VC doing Q&A after the learner's pitch. Grill them "
                   "on business model, competitors, team, unit economics, and metrics. "
                   "One pointed question at a time. " + _BASE_RULES,
        "goals": ["Our revenue model is ...", "Unlike our competitors, we ...",
                  "That's a great question — ..."],
    },
    "objection": {
        "level": 3, "title": "어려운 질문 반론 대응",
        "desc": "회의적인 질문에 침착하게 대응하기",
        "persona": "You are a skeptical investor challenging the learner: 'the market is "
                   "too small', 'big players will copy you', 'why now?'. Be tough but "
                   "professional, and acknowledge good answers. " + _BASE_RULES,
        "goals": ["I understand your concern, but ...", "That's exactly why we ...",
                  "The data actually shows ..."],
    },
    "trend": {
        "level": 4, "title": "업계 트렌드 토론",
        "desc": "AI·콘텐츠 등 업계 이슈로 자유 토론",
        "persona": "You are a well-informed industry peer chatting about tech trends "
                   "(AI, content, startups) over coffee. Share opinions, ask what the "
                   "learner thinks, and respectfully disagree sometimes. " + _BASE_RULES,
        "goals": ["In my view ...", "I see it a bit differently", "That said, ..."],
    },
    "dinner": {
        "level": 4, "title": "네트워킹 디너 대화",
        "desc": "저녁 자리에서 길게 이어지는 대화",
        "persona": "You are seated next to the learner at a networking dinner. Have a "
                   "long, relaxed conversation: work, culture, travel, family-friendly "
                   "topics. Keep it flowing naturally. " + _BASE_RULES,
        "goals": ["Speaking of which ...", "That reminds me of ...", "How about you?"],
    },
    "freetalk": {
        "level": 4, "title": "즉석 프리토킹",
        "desc": "AI가 던지는 무작위 비즈니스 주제로 길게 말하기",
        "persona": "You are a conversation coach running a free-talking session. Throw "
                   "the learner interesting business-related questions and push them to "
                   "elaborate: 'tell me more', 'why do you think so?'. " + _BASE_RULES,
        "goals": ["Let me think about that", "To put it another way ...",
                  "There are three reasons ..."],
    },
}

# 커리큘럼 순서 (데일리 추천 순환용)
CURRICULUM = list(SCENARIOS.keys())

# Leitner 상자별 다음 복습 간격(일)
BOX_INTERVALS = {1: 0, 2: 1, 3: 3, 4: 7, 5: 21}
MAX_BOX = 5


def today_kst():
    return datetime.now(KST).date()


# ------------------------------------------------------------ 세션

SESSION_FIELDS = "id,scenario,level,messages,feedback,created_at"


def create_session(email: str, scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise StoreError("알 수 없는 시나리오입니다.")
    resp = todos._request(
        "POST", "english_sessions",
        json={"email": email, "scenario": scenario,
              "level": SCENARIOS[scenario]["level"], "messages": []},
        headers={"Prefer": "return=representation"},
    )
    return resp.json()[0]


def get_session(email: str, session_id: int) -> dict:
    rows = todos._request(
        "GET", "english_sessions",
        params={"select": SESSION_FIELDS, "email": f"eq.{email}",
                "id": f"eq.{session_id}"},
    ).json()
    if not rows:
        raise StoreError("연습 세션을 찾을 수 없습니다.")
    return rows[0]


def save_session(email: str, session_id: int, messages: list | None = None,
                 feedback: dict | None = None) -> None:
    patch: dict = {}
    if messages is not None:
        patch["messages"] = messages[-100:]
    if feedback is not None:
        patch["feedback"] = feedback
    if not patch:
        return
    todos._request("PATCH", "english_sessions",
                   params={"email": f"eq.{email}", "id": f"eq.{session_id}"},
                   json=patch)


def recent_sessions(email: str, limit: int = 60) -> list[dict]:
    return todos._request(
        "GET", "english_sessions",
        params={"select": "id,scenario,level,feedback,created_at",
                "email": f"eq.{email}", "order": "created_at.desc",
                "limit": str(limit)},
    ).json()


# ------------------------------------------------- 스트릭·오늘의 추천 (순수 함수)

def compute_streak(session_dates: list[str], today=None) -> int:
    """연습한 날짜(ISO, 중복 허용) 목록으로 연속 학습일 계산.

    오늘 안 했어도 어제까지 이어졌으면 스트릭 유지로 본다.
    """
    today = today or today_kst()
    days = set()
    for raw in session_dates:
        try:
            days.add(datetime.fromisoformat(raw).astimezone(KST).date())
        except (ValueError, TypeError):
            continue
    if not days:
        return 0
    start = today if today in days else today - timedelta(days=1)
    if start not in days:
        return 0
    streak = 0
    d = start
    while d in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


def recommend_scenario(done_scenarios: list[str]) -> str:
    """커리큘럼 순서대로, 가장 연습 횟수가 적은 시나리오를 추천."""
    counts = {key: 0 for key in CURRICULUM}
    for s in done_scenarios:
        if s in counts:
            counts[s] += 1
    return min(CURRICULUM, key=lambda k: (counts[k], CURRICULUM.index(k)))


# ------------------------------------------------------------ 스크립트

SCRIPT_FIELDS = "id,title,source,sentences,updated_at"


def list_scripts(email: str) -> list[dict]:
    return todos._request(
        "GET", "english_scripts",
        params={"select": SCRIPT_FIELDS, "email": f"eq.{email}",
                "order": "updated_at.desc", "limit": "50"},
    ).json()


def save_script(email: str, title: str, source: str,
                sentences: list[dict]) -> dict:
    title = (title or "").strip()[:100] or "제목 없음"
    resp = todos._request(
        "POST", "english_scripts",
        json={"email": email, "title": title, "source": (source or "")[:8000],
              "sentences": sentences[:100]},
        headers={"Prefer": "return=representation"},
    )
    return resp.json()[0]


def delete_script(email: str, script_id: int) -> None:
    todos._request("DELETE", "english_scripts",
                   params={"email": f"eq.{email}", "id": f"eq.{script_id}"})


# ------------------------------------------------------------ 암기 카드

CARD_FIELDS = "id,front,back,note,box,due_date"


def list_cards(email: str, due_only: bool = False) -> list[dict]:
    params = {"select": CARD_FIELDS, "email": f"eq.{email}",
              "order": "due_date.asc,id.asc", "limit": "300"}
    if due_only:
        params["due_date"] = f"lte.{today_kst().isoformat()}"
    return todos._request("GET", "english_cards", params=params).json()


def add_card(email: str, front: str, back: str, note: str = "") -> dict:
    front = (front or "").strip()[:300]
    back = (back or "").strip()[:300]
    if not front or not back:
        raise StoreError("카드 앞면/뒷면을 모두 입력해주세요.")
    resp = todos._request(
        "POST", "english_cards",
        json={"email": email, "front": front, "back": back,
              "note": (note or "").strip()[:300],
              "due_date": today_kst().isoformat()},
        headers={"Prefer": "return=representation"},
    )
    return resp.json()[0]


def next_review(box: int, ok: bool, today=None) -> tuple[int, str]:
    """복습 결과에 따른 (다음 상자, 다음 복습일). 순수 함수.

    다시(again) → 상자 1, 오늘 다시. 알겠음(good) → 다음 상자, 간격만큼 뒤.
    """
    today = today or today_kst()
    if not ok:
        return 1, today.isoformat()
    new_box = min(int(box) + 1, MAX_BOX)
    return new_box, (today + timedelta(days=BOX_INTERVALS[new_box])).isoformat()


def review_card(email: str, card_id: int, ok: bool) -> dict:
    rows = todos._request(
        "GET", "english_cards",
        params={"select": "id,box", "email": f"eq.{email}", "id": f"eq.{card_id}"},
    ).json()
    if not rows:
        raise StoreError("카드를 찾을 수 없습니다.")
    box, due = next_review(rows[0]["box"], ok)
    todos._request("PATCH", "english_cards",
                   params={"email": f"eq.{email}", "id": f"eq.{card_id}"},
                   json={"box": box, "due_date": due})
    return {"box": box, "due_date": due}


def delete_card(email: str, card_id: int) -> None:
    todos._request("DELETE", "english_cards",
                   params={"email": f"eq.{email}", "id": f"eq.{card_id}"})
