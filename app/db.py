"""SQLite 저장소. 공고번호+차수를 기본키로 하여 중복 수집을 방지한다."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bid_notice (
    bid_ntce_no   TEXT NOT NULL,            -- 공고번호
    bid_ntce_ord  TEXT NOT NULL DEFAULT '', -- 공고차수
    category      TEXT NOT NULL,            -- 물품/용역/공사/외자
    title         TEXT,                     -- 공고명
    org_demand    TEXT,                     -- 수요기관
    org_notice    TEXT,                     -- 공고기관
    notice_date   TEXT,                     -- 공고일시
    close_date    TEXT,                     -- 입찰마감일시
    open_date     TEXT,                     -- 개찰일시
    presmpt_price INTEGER,                  -- 추정가격
    budget_amount INTEGER,                  -- 배정예산금액
    region        TEXT,                     -- 참가제한지역
    url           TEXT,                     -- 나라장터 상세 URL
    source        TEXT NOT NULL DEFAULT 'g2b_api',
    raw_json      TEXT,                     -- 원본 응답(스키마 변화 대비)
    collected_at  TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (bid_ntce_no, bid_ntce_ord)
);
CREATE INDEX IF NOT EXISTS idx_bid_notice_date  ON bid_notice(notice_date);
CREATE INDEX IF NOT EXISTS idx_bid_close_date   ON bid_notice(close_date);
CREATE INDEX IF NOT EXISTS idx_bid_category     ON bid_notice(category);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def map_item(item: dict, category: str) -> dict:
    """API 응답 item → bid_notice 행. 알 수 없는 필드는 raw_json에 보존."""
    return {
        "bid_ntce_no": str(item.get("bidNtceNo") or ""),
        "bid_ntce_ord": str(item.get("bidNtceOrd") or ""),
        "category": category,
        "title": item.get("bidNtceNm"),
        "org_demand": item.get("dminsttNm"),
        "org_notice": item.get("ntceInsttNm"),
        "notice_date": item.get("bidNtceDt"),
        "close_date": item.get("bidClseDt"),
        "open_date": item.get("opengDt"),
        "presmpt_price": _to_int(item.get("presmptPrce")),
        "budget_amount": _to_int(item.get("asignBdgtAmt")),
        "region": item.get("prtcptLmtRgnNm") or item.get("rgnLmtBidLocplcJdgmBssNm"),
        "url": item.get("bidNtceDtlUrl") or item.get("bidNtceUrl"),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def upsert_notice(conn: sqlite3.Connection, row: dict) -> str:
    """저장하고 'new' 또는 'updated' 반환. 공고번호가 없으면 'skipped'."""
    if not row["bid_ntce_no"]:
        return "skipped"

    now = datetime.now().isoformat(timespec="seconds")
    exists = conn.execute(
        "SELECT 1 FROM bid_notice WHERE bid_ntce_no=? AND bid_ntce_ord=?",
        (row["bid_ntce_no"], row["bid_ntce_ord"]),
    ).fetchone()

    conn.execute(
        """
        INSERT INTO bid_notice (
            bid_ntce_no, bid_ntce_ord, category, title, org_demand, org_notice,
            notice_date, close_date, open_date, presmpt_price, budget_amount,
            region, url, raw_json, collected_at, updated_at
        ) VALUES (
            :bid_ntce_no, :bid_ntce_ord, :category, :title, :org_demand, :org_notice,
            :notice_date, :close_date, :open_date, :presmpt_price, :budget_amount,
            :region, :url, :raw_json, :now, :now
        )
        ON CONFLICT(bid_ntce_no, bid_ntce_ord) DO UPDATE SET
            category=excluded.category,
            title=excluded.title,
            org_demand=excluded.org_demand,
            org_notice=excluded.org_notice,
            notice_date=excluded.notice_date,
            close_date=excluded.close_date,
            open_date=excluded.open_date,
            presmpt_price=excluded.presmpt_price,
            budget_amount=excluded.budget_amount,
            region=excluded.region,
            url=excluded.url,
            raw_json=excluded.raw_json,
            updated_at=excluded.updated_at
        """,
        {**row, "now": now},
    )
    return "updated" if exists else "new"


def stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT category, COUNT(*) AS cnt,
               MIN(notice_date) AS oldest, MAX(notice_date) AS newest
        FROM bid_notice GROUP BY category ORDER BY cnt DESC
        """
    ).fetchall()
