import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ["G2B_SERVICE_KEY"] = "dummy"

from fastapi.testclient import TestClient

import api.index as web
from app import auth, todos

KST = ZoneInfo("Asia/Seoul")


class StatsTest(unittest.TestCase):
    def test_compute_stats(self):
        today = datetime(2026, 7, 29, 15, 0, tzinfo=KST)  # 수요일
        done = [
            "2026-07-29T10:00:00+09:00",  # 오늘
            "2026-07-29T11:00:00+09:00",  # 오늘
            "2026-07-28T09:00:00+09:00",  # 이번 주(월=27일 시작)
            "2026-07-20T09:00:00+09:00",  # 지난주, 이번 달
            "2026-06-30T09:00:00+09:00",  # 지난달
            "잘못된값",
        ]
        st = todos.compute_stats(done, pending_count=3, today=today)
        self.assertEqual(st["today"], 2)
        self.assertEqual(st["week"], 3)
        self.assertEqual(st["month"], 4)
        self.assertEqual(st["pending"], 3)
        self.assertEqual(len(st["daily"]), 14)
        self.assertEqual(st["daily"][-1]["count"], 2)  # 오늘
        self.assertEqual(st["daily"][-2]["count"], 1)  # 어제


class RoutingTest(unittest.TestCase):
    def setUp(self):
        os.environ["GOOGLE_CLIENT_ID"] = "cid"
        os.environ["GOOGLE_CLIENT_SECRET"] = "sec"
        os.environ["ADMIN_EMAILS"] = "boss@company.com"
        self.client = TestClient(web.app)
        self.admin_cookie = {auth.COOKIE_NAME: auth.make_session("boss@company.com")}
        self.user_cookie = {auth.COOKIE_NAME: auth.make_session("guest@gmail.com")}

    def tearDown(self):
        for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ADMIN_EMAILS"):
            os.environ.pop(k, None)

    def test_anonymous_home_redirects_to_login(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/login"))

    def test_non_admin_home_redirects_to_bid(self):
        r = self.client.get("/", cookies=self.user_cookie, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (302, "/bid"))

    def test_legacy_query_redirects_to_bid_with_params(self):
        r = self.client.get("/?q=%ED%99%8D%EB%B3%B4&days=7",
                            cookies=self.admin_cookie, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["location"].startswith("/bid?"))
        self.assertIn("days=7", r.headers["location"])

    def test_admin_home_renders(self):
        with patch.object(todos, "enabled", return_value=True), \
             patch.object(todos, "list_todos", return_value={
                 "pending": [{"id": 1, "title": "테스트 할 일", "created_at": ""}],
                 "done": [{"id": 2, "title": "끝낸 일", "done_at": "2026-07-29T10:00:00+09:00"}],
             }), \
             patch.object(todos, "stats", return_value=todos.compute_stats(
                 ["2026-07-29T10:00:00+09:00"], 1,
                 today=datetime(2026, 7, 29, tzinfo=KST))):
            r = self.client.get("/", cookies=self.admin_cookie)
        self.assertEqual(r.status_code, 200)
        for needle in ["내 작업 공간", "오늘 완료", "최근 14일", "테스트 할 일",
                       "🏠 메인", 'class="bar today"']:
            self.assertIn(needle, r.text)

    def test_bid_page_still_works(self):
        with patch.object(web.G2BClient, "fetch_page",
                          return_value={"total_count": 0, "items": []}):
            r = self.client.get("/bid", cookies=self.user_cookie)
        self.assertEqual(r.status_code, 200)
        self.assertIn("입찰공고 검색", r.text)
        self.assertNotIn("🏠 메인", r.text)  # 비관리자에게 메인 탭 숨김

    def test_todo_api_requires_admin(self):
        r = self.client.post("/api/todos", json={"title": "x"}, cookies=self.user_cookie)
        self.assertFalse(r.json()["ok"])
        saved = {}
        with patch.object(todos, "add_todo", side_effect=lambda e, t: saved.update({e: t})):
            r = self.client.post("/api/todos", json={"title": "새 일"},
                                 cookies=self.admin_cookie)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(saved, {"boss@company.com": "새 일"})


if __name__ == "__main__":
    unittest.main()
