"""수집 오케스트레이션: 업무구분별로 기간 조회 → DB upsert."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from . import db
from .g2b_client import CATEGORIES, G2BApiError, G2BClient

logger = logging.getLogger(__name__)


def date_range_for_days(days: int) -> tuple[str, str]:
    """최근 N일 조회 구간을 YYYYMMDDHHMM 형식으로 반환."""
    now = datetime.now()
    begin = now - timedelta(days=days)
    return begin.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H%M")


def collect(
    client: G2BClient,
    conn: sqlite3.Connection,
    categories: list[str] | None = None,
    inqry_bgn_dt: str | None = None,
    inqry_end_dt: str | None = None,
    days: int = 1,
    keyword: str | None = None,
) -> dict[str, dict[str, int]]:
    """수집 실행. 업무구분별 {"fetched": n, "new": n, "updated": n} 통계 반환."""
    if not inqry_bgn_dt or not inqry_end_dt:
        inqry_bgn_dt, inqry_end_dt = date_range_for_days(days)

    categories = categories or CATEGORIES
    results: dict[str, dict[str, int]] = {}

    for category in categories:
        counts = {"fetched": 0, "new": 0, "updated": 0, "skipped": 0}
        logger.info("[%s] 수집 시작: %s ~ %s", category, inqry_bgn_dt, inqry_end_dt)
        try:
            for item in client.iter_notices(
                category, inqry_bgn_dt, inqry_end_dt, bid_ntce_nm=keyword
            ):
                counts["fetched"] += 1
                outcome = db.upsert_notice(conn, db.map_item(item, category))
                counts[outcome] += 1
            conn.commit()
        except G2BApiError as e:
            conn.commit()  # 중간까지 수집된 건은 보존
            logger.error("[%s] API 오류로 중단: %s", category, e)
            counts["error"] = 1
        results[category] = counts
        logger.info(
            "[%s] 완료: 조회 %d건 (신규 %d, 갱신 %d)",
            category, counts["fetched"], counts["new"], counts["updated"],
        )
    return results
