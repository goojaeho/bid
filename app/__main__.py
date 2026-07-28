"""CLI 진입점.

사용 예:
  python -m app collect                     # 최근 1일치 전체 업무구분 수집
  python -m app collect --days 3            # 최근 3일치
  python -m app collect --category 용역 공사  # 특정 업무구분만
  python -m app collect --keyword 소프트웨어  # 공고명 키워드 필터
  python -m app collect --from 202607010000 --to 202607282359
  python -m app collect --loop --interval 30 # 30분 간격 반복 수집
  python -m app stats                       # DB 현황
"""
from __future__ import annotations

import argparse
import logging
import time

from . import collector, config, db
from .g2b_client import CATEGORIES, G2BClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


def cmd_collect(args: argparse.Namespace) -> None:
    client = G2BClient(config.get_service_key())
    conn = db.connect(config.get_db_path())

    def run_once() -> None:
        results = collector.collect(
            client,
            conn,
            categories=args.category,
            inqry_bgn_dt=args.begin,
            inqry_end_dt=args.end,
            days=args.days,
            keyword=args.keyword,
        )
        total = sum(c["fetched"] for c in results.values())
        new = sum(c["new"] for c in results.values())
        print(f"수집 완료: 총 {total}건 조회, 신규 {new}건")

    if args.loop:
        print(f"{args.interval}분 간격 반복 수집 시작 (중지: Ctrl+C)")
        while True:
            run_once()
            time.sleep(args.interval * 60)
    else:
        run_once()


def cmd_stats(args: argparse.Namespace) -> None:
    conn = db.connect(config.get_db_path())
    rows = db.stats(conn)
    if not rows:
        print("저장된 공고가 없습니다. 먼저 `python -m app collect`를 실행하세요.")
        return
    print(f"{'업무구분':<6} {'건수':>8}  {'가장 오래된 공고':<20} {'최신 공고':<20}")
    for r in rows:
        print(f"{r['category']:<6} {r['cnt']:>8}  {r['oldest'] or '-':<20} {r['newest'] or '-':<20}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="bid", description="나라장터 입찰공고 수집기")
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="입찰공고 수집")
    p_collect.add_argument("--days", type=int, default=1, help="최근 N일치 수집 (기본 1)")
    p_collect.add_argument("--from", dest="begin", help="조회 시작 (YYYYMMDDHHMM)")
    p_collect.add_argument("--to", dest="end", help="조회 종료 (YYYYMMDDHHMM)")
    p_collect.add_argument(
        "--category", nargs="+", choices=CATEGORIES,
        help="업무구분 (기본: 전체)",
    )
    p_collect.add_argument("--keyword", help="공고명 키워드 필터")
    p_collect.add_argument("--loop", action="store_true", help="주기 반복 수집")
    p_collect.add_argument("--interval", type=int, default=30, help="반복 간격(분), 기본 30")
    p_collect.set_defaults(func=cmd_collect)

    p_stats = sub.add_parser("stats", help="저장된 공고 현황")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
