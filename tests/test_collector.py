import json
import sqlite3
import unittest

from app import db
from app.g2b_client import G2BApiError, G2BClient

SAMPLE_ITEM = {
    "bidNtceNo": "20260728001",
    "bidNtceOrd": "00",
    "bidNtceNm": "정보시스템 유지관리 용역",
    "ntceInsttNm": "조달청",
    "dminsttNm": "한국대학교",
    "bidNtceDt": "2026-07-28 09:00:00",
    "bidClseDt": "2026-08-05 18:00:00",
    "opengDt": "2026-08-06 11:00:00",
    "presmptPrce": "150000000",
    "asignBdgtAmt": "165000000",
    "bidNtceDtlUrl": "https://www.g2b.go.kr/detail/20260728001",
}


def sample_response(items, total_count=None):
    return json.dumps({
        "response": {
            "header": {"resultCode": "00", "resultMsg": "정상"},
            "body": {
                "items": items,
                "totalCount": total_count if total_count is not None else len(items),
            },
        }
    }, ensure_ascii=False)


class ParseResponseTest(unittest.TestCase):
    def test_parse_normal(self):
        result = G2BClient._parse_response(sample_response([SAMPLE_ITEM]))
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["items"][0]["bidNtceNo"], "20260728001")

    def test_parse_empty_items(self):
        result = G2BClient._parse_response(sample_response([], total_count=0))
        self.assertEqual(result["items"], [])

    def test_parse_single_item_as_dict(self):
        text = json.dumps({
            "response": {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": SAMPLE_ITEM}, "totalCount": 1},
            }
        })
        result = G2BClient._parse_response(text)
        self.assertEqual(len(result["items"]), 1)

    def test_xml_error_raises(self):
        xml = (
            "<OpenAPI_ServiceResponse><cmmMsgHeader>"
            "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
            "<returnReasonCode>30</returnReasonCode>"
            "</cmmMsgHeader></OpenAPI_ServiceResponse>"
        )
        with self.assertRaises(G2BApiError) as ctx:
            G2BClient._parse_response(xml)
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", str(ctx.exception))

    def test_result_code_error_raises(self):
        text = json.dumps({
            "response": {
                "header": {"resultCode": "07", "resultMsg": "입력범위값 초과 에러"},
                "body": {},
            }
        })
        with self.assertRaises(G2BApiError):
            G2BClient._parse_response(text)


class DbTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_map_item(self):
        row = db.map_item(SAMPLE_ITEM, "용역")
        self.assertEqual(row["bid_ntce_no"], "20260728001")
        self.assertEqual(row["category"], "용역")
        self.assertEqual(row["presmpt_price"], 150000000)
        self.assertEqual(row["title"], "정보시스템 유지관리 용역")

    def test_upsert_dedup(self):
        row = db.map_item(SAMPLE_ITEM, "용역")
        self.assertEqual(db.upsert_notice(self.conn, row), "new")
        self.assertEqual(db.upsert_notice(self.conn, row), "updated")
        count = self.conn.execute("SELECT COUNT(*) FROM bid_notice").fetchone()[0]
        self.assertEqual(count, 1)

    def test_upsert_missing_no_skipped(self):
        row = db.map_item({}, "물품")
        self.assertEqual(db.upsert_notice(self.conn, row), "skipped")


if __name__ == "__main__":
    unittest.main()
