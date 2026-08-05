"""자동 DB 마이그레이션.

DATABASE_URL(Supabase Session pooler URI) 환경변수가 설정되어 있으면
콜드스타트 시 미적용 마이그레이션을 자동 실행한다.
미설정 시 조용히 건너뛴다 (수동 SQL 실행 방식과 병행 가능).

모든 문장은 IF NOT EXISTS 등으로 멱등하게 작성한다 —
이미 수동으로 적용한 변경과 겹쳐도 안전하다.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        create table if not exists favs (
          email text primary key,
          data jsonb not null default '{}'::jsonb
        );
    """),
    (2, """
        create table if not exists todos (
          id bigint generated always as identity primary key,
          email text not null,
          title text not null,
          done boolean not null default false,
          created_at timestamptz not null default now(),
          done_at timestamptz
        );
    """),
    (3, """
        alter table todos add column if not exists area text not null default 'work';
        alter table todos add column if not exists category text not null default '';
        alter table todos add column if not exists due_date date;
        alter table todos add column if not exists priority int not null default 2;
        create index if not exists todos_email_idx on todos (email, done, due_date);
    """),
    (4, """
        alter table todos add column if not exists parent_id bigint;
    """),
    (5, """
        create table if not exists reader_jobs (
          job_id text primary key,
          email text not null,
          result jsonb not null,
          created_at timestamptz not null default now()
        );
    """),
    (6, """
        create table if not exists gemini_usage (
          day date not null,
          kind text not null,
          requests int not null default 0,
          tokens bigint not null default 0,
          primary key (day, kind)
        );
        create or replace function bump_gemini_usage(d date, k text, t bigint)
        returns void language sql as $func$
          insert into gemini_usage (day, kind, requests, tokens)
          values (d, k, 1, t)
          on conflict (day, kind) do update
            set requests = gemini_usage.requests + 1,
                tokens = gemini_usage.tokens + excluded.tokens;
        $func$;
    """),
]

_ran = False


def database_url() -> str | None:
    return (os.environ.get("DATABASE_URL") or "").strip() or None


def run() -> None:
    """미적용 마이그레이션 실행. 실패해도 앱 기동을 막지 않는다."""
    global _ran
    if _ran:
        return
    _ran = True
    url = database_url()
    if not url:
        return
    try:
        import psycopg

        with psycopg.connect(url, autocommit=True, connect_timeout=8) as conn:
            conn.execute(
                "create table if not exists schema_migrations "
                "(version int primary key, applied_at timestamptz default now())"
            )
            applied = {row[0] for row in
                       conn.execute("select version from schema_migrations").fetchall()}
            for version, sql in MIGRATIONS:
                if version in applied:
                    continue
                conn.execute(sql)
                conn.execute(
                    "insert into schema_migrations (version) values (%s)", (version,)
                )
                logger.info("마이그레이션 v%d 적용", version)
    except Exception as e:  # 마이그레이션 실패가 서비스 전체를 막으면 안 됨
        logger.error("자동 마이그레이션 실패: %s", e)


def applied_version() -> int | None:
    """적용된 최신 마이그레이션 버전 (DATABASE_URL 미설정/오류 시 None)."""
    status = db_status()
    return int(status[3:]) if status.startswith("ok:") else None


def db_status() -> str:
    """'unset' | 'ok:N' | 'error:<사유 요약>' — 비밀정보는 노출하지 않는다."""
    url = database_url()
    if not url:
        return "unset"
    try:
        import psycopg

        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            row = conn.execute(
                "select coalesce(max(version), 0) from schema_migrations"
            ).fetchone()
            return f"ok:{row[0] if row else 0}"
    except Exception as e:
        reason = str(e).split("\n")[0][:120]
        return f"error:{type(e).__name__}: {reason}"
