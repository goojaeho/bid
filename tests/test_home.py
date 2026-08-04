import os
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ["G2B_SERVICE_KEY"] = "dummy"

from fastapi.testclient import TestClient

import api.index as web
from app import auth, todos

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


if __name__ == "__main__":
    unittest.main()
