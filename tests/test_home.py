import os
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ["G2B_SERVICE_KEY"] = "dummy"

from fastapi.testclient import TestClient

import api.index as web
from app import auth, english, genie, searches, summarize, todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")


class NlDateTest(unittest.TestCase):
    TODAY = date(2026, 7, 29)  # 수요일

    def parse(self, title):
        return todos.parse_nl_date(title, today=self.TODAY)

    def test_relative_days(self):
        self.assertEqual(self.parse("보고서 오늘"), ("보고서", "2026-07-29"))
        self.assertEqual(self.parse("보고서 내일까지"), ("보고서", "2026-07-30"))
        self.assertEqual(self.parse("모레 회의 준비"), ("회의 준비", "2026-07-31"))

    def test_weekday(self):
        # 수요일 기준: 금요일 = 7/31, 다음주 화요일 = 8/4
        self.assertEqual(self.parse("제안서 금요일까지"), ("제안서", "2026-07-31"))
        self.assertEqual(self.parse("다음주 화요일 미팅"), ("미팅", "2026-08-04"))
        # 오늘 요일과 같으면 오늘
        self.assertEqual(self.parse("수요일 마감")[1], "2026-07-29")

    def test_explicit_date(self):
        self.assertEqual(self.parse("세미나 8월 5일"), ("세미나", "2026-08-05"))
        self.assertEqual(self.parse("결제 8/15까지"), ("결제", "2026-08-15"))
        # 지난 날짜 → 내년
        self.assertEqual(self.parse("갱신 1월 5일")[1], "2027-01-05")

    def test_no_date(self):
        self.assertEqual(self.parse("운동하기"), ("운동하기", None))


class StatsTest(unittest.TestCase):
    def test_compute_stats_by_area(self):
        today = datetime(2026, 7, 29, 15, 0, tzinfo=KST)  # 수요일
        rows = [
            {"done_at": "2026-07-29T10:00:00+09:00", "area": "work"},
            {"done_at": "2026-07-29T11:00:00+09:00", "area": "personal"},
            {"done_at": "2026-07-28T09:00:00+09:00", "area": "work"},
            {"done_at": "2026-07-20T09:00:00+09:00", "area": "work"},
            {"done_at": "잘못된값", "area": "work"},
        ]
        st = todos.compute_stats(rows, pending_count=3, today=today)
        self.assertEqual(st["today"], 2)
        self.assertEqual(st["today_work"], 1)
        self.assertEqual(st["today_personal"], 1)
        self.assertEqual(st["week"], 3)
        self.assertEqual(st["month"], 4)
        self.assertEqual(st["pending"], 3)
        self.assertEqual(len(st["daily"]), 14)
        self.assertEqual(st["daily"][-1], {"date": "2026-07-29", "label": "7/29",
                                           "work": 1, "personal": 1})


class RoutingTest(unittest.TestCase):
    def setUp(self):
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
        os.environ["ADMIN_EMAILS"] = "boss@company.com"
        self.client = TestClient(web.app)
        self.admin_cookie = {auth.COOKIE_NAME: auth.make_session("boss@company.com")}
        self.user_cookie = {auth.COOKIE_NAME: auth.make_session("guest@gmail.com")}
        self.sample = {
            "pending": [
                {"id": 1, "title": "제안서 작성", "area": "work", "category": "제안서",
                 "due_date": datetime.now(KST).date().isoformat(), "priority": 1,
                 "created_at": ""},
                {"id": 2, "title": "운동", "area": "personal", "category": "운동",
                 "due_date": None, "priority": 2, "created_at": ""},
            ],
            "done": [
                {"id": 3, "title": "끝낸 일", "area": "work", "category": "",
                 "due_date": None, "priority": 2, "created_at": "",
                 "done_at": "2026-07-29T10:00:00+09:00"},
            ],
        }
        self.stats = todos.compute_stats(
            [{"done_at": "2026-07-29T10:00:00+09:00", "area": "work"}], 2,
            today=datetime(2026, 7, 29, tzinfo=KST))

    def tearDown(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS"):
            os.environ.pop(k, None)

    def test_anonymous_home_redirects_to_login(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/login"))

    def test_non_admin_redirects_to_bid(self):
        for path in ("/", "/todo"):
            r = self.client.get(path, cookies=self.user_cookie, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (302, "/bid"), path)

    def test_admin_dashboard_renders(self):
        with patch.object(todos, "enabled", return_value=True), \
             patch.object(todos, "list_todos", return_value=self.sample), \
             patch.object(todos, "stats_rows", return_value=[
                 {"done_at": "2026-07-29T10:00:00+09:00", "area": "work"}]):
            r = self.client.get("/", cookies=self.admin_cookie)
        self.assertEqual(r.status_code, 200)
        for needle in ["오늘 완료", "최근 14일", "오늘 할 일", "할 일 관리 →",
                       "bar-seg work", "legend", "list-checks" if False else "할 일"]:
            self.assertIn(needle, r.text)
        # 대시보드에는 입력 폼이 없어야 함
        self.assertNotIn('id="t-title"', r.text)

    def test_todo_page_renders_with_views(self):
        with patch.object(todos, "enabled", return_value=True), \
             patch.object(todos, "list_todos", return_value=self.sample):
            r = self.client.get("/todo", cookies=self.admin_cookie)
            self.assertEqual(r.status_code, 200)
            for needle in ["todo-form", "업무", "개인", "제안서 작성",
                           "캘린더에도 추가", "마감일 자동 인식", "todo-sub"]:
                self.assertIn(needle, r.text)
            # 영역 필터: personal만
            r2 = self.client.get("/todo?area=personal&view=all",
                                 cookies=self.admin_cookie)
            self.assertIn("운동", r2.text)
            self.assertNotIn("제안서 작성", r2.text)

    def test_todo_api_add_with_fields(self):
        r = self.client.post("/api/todos", json={"title": "x"}, cookies=self.user_cookie)
        self.assertFalse(r.json()["ok"])
        captured = {}
        def fake_add(email, title, area="work", category="", due_date=None,
                     priority=2, parent_id=None, parse_date=True):
            captured.update(dict(email=email, title=title, area=area,
                                 category=category, due_date=due_date,
                                 priority=priority))
            return {"id": 9, "title": title}
        with patch.object(todos, "add_todo", side_effect=fake_add):
            r = self.client.post("/api/todos", cookies=self.admin_cookie, json={
                "title": "새 일", "area": "personal", "category": "운동",
                "due": "2026-08-01", "priority": 1})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(captured["area"], "personal")
        self.assertEqual(captured["due_date"], "2026-08-01")
        self.assertEqual(captured["priority"], 1)

    def test_todo_api_subtask(self):
        captured = {}
        def fake_add(email, title, area="work", category="", due_date=None,
                     priority=2, parent_id=None, parse_date=True):
            captured.update(dict(title=title, parent_id=parent_id, area=area))
            return {"id": 10, "title": title}
        with patch.object(todos, "add_todo", side_effect=fake_add):
            r = self.client.post("/api/todos", cookies=self.admin_cookie, json={
                "title": "자료 조사", "parent_id": 1, "area": "work"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(captured["parent_id"], 1)

    def test_todo_api_update(self):
        captured = {}
        with patch.object(todos, "update_todo",
                          side_effect=lambda e, i, f: captured.update({i: f})):
            r = self.client.post("/api/todos/7/update", cookies=self.admin_cookie,
                                 json={"due_date": "2026-08-02"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(captured[7], {"due_date": "2026-08-02"})


class GenieChatTest(unittest.TestCase):
    """지니 대화 저장 API — 소유자(첫 관리자) 전용."""

    def setUp(self):
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
        os.environ["ADMIN_EMAILS"] = "boss@company.com"
        self.client = TestClient(web.app)
        self.owner_cookie = {auth.COOKIE_NAME: auth.make_session("boss@company.com")}
        self.user_cookie = {auth.COOKIE_NAME: auth.make_session("guest@gmail.com")}

    def tearDown(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS"):
            os.environ.pop(k, None)

    def test_non_owner_blocked(self):
        for method, path in (("get", "/api/genie/chats"),
                             ("get", "/api/genie/chats/1"),
                             ("post", "/api/genie/chats/1/delete")):
            r = getattr(self.client, method)(path, cookies=self.user_cookie)
            self.assertFalse(r.json()["ok"], path)

    def test_new_chat_creates_and_saves(self):
        captured = {}
        def fake_create(email, title, messages):
            captured.update(email=email, title=title, messages=messages)
            return 42
        with patch.object(genie, "enabled", return_value=True), \
             patch.object(genie, "create_chat", side_effect=fake_create), \
             patch.object(summarize, "gemini_chat", return_value="안녕!"), \
             patch.object(summarize, "usage_today",
                          return_value={"requests": 1, "tokens": 10,
                                        "limit": 1000, "remaining": 999}):
            r = self.client.post("/api/genie", cookies=self.owner_cookie,
                                 json={"text": "안녕"})
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["chat_id"], 42)
        self.assertEqual(d["reply"], "안녕!")
        self.assertEqual(captured["title"], "안녕")
        self.assertEqual(captured["messages"], [
            {"role": "user", "text": "안녕"},
            {"role": "model", "text": "안녕!"}])

    def test_existing_chat_appends_context(self):
        prior = [{"role": "user", "text": "질문1"},
                 {"role": "model", "text": "답1"}]
        sent, saved = [], {}
        with patch.object(genie, "get_chat",
                          return_value={"id": 7, "messages": list(prior)}), \
             patch.object(genie, "save_messages",
                          side_effect=lambda e, i, m: saved.update({i: m})), \
             patch.object(summarize, "gemini_chat",
                          side_effect=lambda m: (sent.extend(m), "답2")[1]), \
             patch.object(summarize, "usage_today", return_value={}):
            r = self.client.post("/api/genie", cookies=self.owner_cookie,
                                 json={"chat_id": 7, "text": "질문2"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(sent[:2], prior)  # 이전 맥락이 Gemini에 전달됨
        self.assertEqual(len(saved[7]), 4)
        self.assertEqual(saved[7][-1], {"role": "model", "text": "답2"})

    def test_empty_text_rejected(self):
        r = self.client.post("/api/genie", cookies=self.owner_cookie,
                             json={"text": "  "})
        self.assertFalse(r.json()["ok"])


class QuickSearchTest(unittest.TestCase):
    """사용자별 빠른 검색 저장 — 로그인한 모든 사용자."""

    def setUp(self):
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
        self.client = TestClient(web.app)
        self.cookie = {auth.COOKIE_NAME: auth.make_session("guest@gmail.com")}

    def tearDown(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
            os.environ.pop(k, None)

    def test_anonymous_blocked(self):
        for method, path in (("get", "/api/searches"),
                             ("post", "/api/searches"),
                             ("post", "/api/searches/1/delete")):
            r = getattr(self.client, method)(
                path, **({"json": {}} if method == "post" and path == "/api/searches" else {}))
            self.assertFalse(r.json()["ok"], path)

    def test_add_search_validates_and_cleans(self):
        sent = {}
        def fake_request(method, path, **kw):
            sent.update(method=method, path=path, **kw)
            class R:
                def json(self):
                    if method == "GET":
                        return []
                    return [{"id": 5, "label": "SW입찰", "params": {"q": "소프트웨어"}}]
            return R()
        with patch.object(todos, "_request", side_effect=fake_request):
            item = searches.add_search(
                "guest@gmail.com", "bid", " SW입찰 ",
                {"q": "소프트웨어", "days": "7", "bogus": "x", "org": "  "})
        self.assertEqual(item["id"], 5)
        self.assertEqual(sent["json"]["label"], "SW입찰")
        # 허용 파라미터만 저장, 빈 값·미지원 키 제거
        self.assertEqual(sent["json"]["params"], {"q": "소프트웨어", "days": "7"})

    def test_add_search_rejects_bad_input(self):
        with patch.object(todos, "_request"):
            with self.assertRaises(StoreError):
                searches.add_search("e@x.com", "bid", "  ", {"q": "a"})
            with self.assertRaises(StoreError):
                searches.add_search("e@x.com", "nope", "이름", {"q": "a"})
            with self.assertRaises(StoreError):
                searches.add_search("e@x.com", "gov", "이름", {"bogus": "x"})

    def test_api_wiring(self):
        with patch.object(searches, "list_searches",
                          return_value=[{"id": 1, "label": "AI", "params": {"q": "AI"}}]):
            r = self.client.get("/api/searches?page=gov", cookies=self.cookie)
        self.assertEqual(r.json(), {"ok": True, "items": [
            {"id": 1, "label": "AI", "params": {"q": "AI"}}]})
        captured = {}
        with patch.object(searches, "delete_search",
                          side_effect=lambda e, i: captured.update(email=e, sid=i)):
            r = self.client.post("/api/searches/7/delete", cookies=self.cookie)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(captured, {"email": "guest@gmail.com", "sid": 7})


class EnglishTest(unittest.TestCase):
    """스피킹 탭 — 커리큘럼·카드 복습·스트릭 로직 및 접근 제어."""

    def setUp(self):
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
        os.environ["ADMIN_EMAILS"] = "boss@company.com"
        self.client = TestClient(web.app)
        self.owner_cookie = {auth.COOKIE_NAME: auth.make_session("boss@company.com")}
        self.user_cookie = {auth.COOKIE_NAME: auth.make_session("guest@gmail.com")}

    def tearDown(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS"):
            os.environ.pop(k, None)

    def test_scenarios_integrity(self):
        for key, sc in english.SCENARIOS.items():
            self.assertIn(sc["level"], english.LEVELS, key)
            for field in ("title", "desc", "persona", "goals"):
                self.assertTrue(sc.get(field), f"{key}.{field}")

    def test_card_review_intervals(self):
        from datetime import date
        today = date(2026, 8, 8)
        self.assertEqual(english.next_review(3, ok=False, today=today),
                         (1, "2026-08-08"))
        self.assertEqual(english.next_review(1, ok=True, today=today),
                         (2, "2026-08-09"))
        self.assertEqual(english.next_review(4, ok=True, today=today),
                         (5, "2026-08-29"))
        # 최고 상자에서 또 맞아도 상자 5 유지
        self.assertEqual(english.next_review(5, ok=True, today=today)[0], 5)

    def test_streak(self):
        from datetime import date
        today = date(2026, 8, 8)
        days = ["2026-08-08T10:00:00+09:00", "2026-08-07T10:00:00+09:00",
                "2026-08-06T09:00:00+09:00", "2026-08-03T09:00:00+09:00"]
        self.assertEqual(english.compute_streak(days, today=today), 3)
        # 오늘 안 했어도 어제까지 이어졌으면 유지
        self.assertEqual(english.compute_streak(days[1:], today=today), 2)
        # 이틀 비면 0
        self.assertEqual(english.compute_streak(["2026-08-05T09:00:00+09:00"],
                                                today=today), 0)
        self.assertEqual(english.compute_streak([], today=today), 0)

    def test_recommend_least_practiced(self):
        first = english.CURRICULUM[0]
        second = english.CURRICULUM[1]
        self.assertEqual(english.recommend_scenario([]), first)
        self.assertEqual(english.recommend_scenario([first]), second)

    def test_non_owner_blocked(self):
        r = self.client.get("/english", cookies=self.user_cookie,
                            follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/bid"))
        r = self.client.get("/api/english/home", cookies=self.user_cookie)
        self.assertFalse(r.json()["ok"])

    def test_owner_page_renders(self):
        r = self.client.get("/english", cookies=self.owner_cookie)
        self.assertEqual(r.status_code, 200)
        for marker in ("pane-home", "pane-talk", "pane-pitch", "pane-cards",
                       "talk-mic", "투자자 Q&A"):
            self.assertIn(marker, r.text, marker)

    def test_chat_api_flow(self):
        saved = {}
        with patch.object(english, "create_session",
                          return_value={"id": 11, "scenario": "intro",
                                        "messages": []}), \
             patch.object(english, "save_session",
                          side_effect=lambda e, i, **kw: saved.update({i: kw})), \
             patch.object(summarize, "gemini_english_chat",
                          return_value="Hi! Tell me about yourself."), \
             patch.object(summarize, "usage_today", return_value={}):
            r = self.client.post("/api/english/chat", cookies=self.owner_cookie,
                                 json={"scenario": "intro"})
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["session_id"], 11)
        self.assertEqual(saved[11]["messages"],
                         [{"role": "model", "text": "Hi! Tell me about yourself."}])


if __name__ == "__main__":
    unittest.main()
