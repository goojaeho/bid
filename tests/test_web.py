import os
import unittest

os.environ.pop("G2B_SERVICE_KEY", None)

from api.index import apply_filters, sort_items


def item(org="한국대학교", price="100000000", notice="2026-07-28 09:00:00",
         close="2026-08-05 18:00:00"):
    return {
        "dminsttNm": org, "ntceInsttNm": "조달청",
        "presmptPrce": price, "bidNtceDt": notice, "bidClseDt": close,
    }


class FilterTest(unittest.TestCase):
    def test_org_filter(self):
        items = [("용역", item(org="서울대학교")), ("용역", item(org="부산시청"))]
        out = apply_filters(items, "학교", None, None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1]["dminsttNm"], "서울대학교")

    def test_org_matches_notice_inst(self):
        items = [("물품", item(org="수요기관"))]
        out = apply_filters(items, "조달청", None, None)
        self.assertEqual(len(out), 1)

    def test_amount_range_in_manwon(self):
        items = [
            ("용역", item(price="50000000")),    # 5천만원 = 5,000만원
            ("용역", item(price="300000000")),   # 3억 = 30,000만원
            ("용역", item(price=None)),          # 금액 없음
        ]
        out = apply_filters(items, "", 10000, 40000)  # 1억~4억
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][1]["presmptPrce"], "300000000")

    def test_no_filters_passthrough(self):
        items = [("공사", item()), ("물품", item(price=None))]
        self.assertEqual(len(apply_filters(items, "", None, None)), 2)


class SortTest(unittest.TestCase):
    def test_latest(self):
        items = [
            ("용역", item(notice="2026-07-26 09:00:00")),
            ("용역", item(notice="2026-07-28 09:00:00")),
        ]
        out = sort_items(items, "latest")
        self.assertEqual(out[0][1]["bidNtceDt"], "2026-07-28 09:00:00")

    def test_deadline_soonest_first_empty_last(self):
        items = [
            ("용역", item(close="")),
            ("용역", item(close="2026-08-10 18:00:00")),
            ("용역", item(close="2026-08-01 18:00:00")),
        ]
        out = sort_items(items, "deadline")
        self.assertEqual(out[0][1]["bidClseDt"], "2026-08-01 18:00:00")
        self.assertEqual(out[-1][1]["bidClseDt"], "")

    def test_amount_desc(self):
        items = [
            ("용역", item(price="100")),
            ("용역", item(price="900")),
            ("용역", item(price=None)),
        ]
        out = sort_items(items, "amount")
        self.assertEqual(out[0][1]["presmptPrce"], "900")
        self.assertIsNone(out[-1][1]["presmptPrce"])


if __name__ == "__main__":
    unittest.main()


class QueryParseTest(unittest.TestCase):
    def setUp(self):
        from api.index import parse_query, query_match
        self.parse = parse_query
        self.match = query_match

    def test_or_with_comma(self):
        groups = self.parse("AI,콘텐츠")
        self.assertEqual(groups, [["ai"], ["콘텐츠"]])
        self.assertTrue(self.match("2026 콘텐츠 제작지원", groups))
        self.assertTrue(self.match("AI 바우처", groups))
        self.assertFalse(self.match("수출 상담회", groups))

    def test_and_with_space(self):
        groups = self.parse("AI 바우처")
        self.assertEqual(groups, [["ai", "바우처"]])
        self.assertTrue(self.match("2026 AI 바우처 지원", groups))
        self.assertFalse(self.match("AI 실증사업", groups))

    def test_combined(self):
        groups = self.parse("AI 바우처, 콘텐츠 제작")
        self.assertTrue(self.match("콘텐츠 제작지원", groups))
        self.assertTrue(self.match("AI 바우처", groups))
        self.assertFalse(self.match("AI 지원사업", groups))

    def test_empty_matches_all(self):
        self.assertTrue(self.match("아무 공고", self.parse("")))

    def test_no_global_cap(self):
        groups = self.parse("a,b,c,d,e")
        self.assertEqual(len(groups), 5)
