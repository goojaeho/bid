"""Vercel 서버리스 엔트리포인트: 나라장터 실시간 검색 웹.

DB 없이 요청 시마다 나라장터 OpenAPI를 직접 호출한다.
Vercel 프로젝트 환경변수에 G2B_SERVICE_KEY 설정 필요.
"""
from __future__ import annotations

import html
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from app import gov_sources
from app.g2b_client import CATEGORIES, G2BApiError, G2BClient
from app.webui import layout

KST = ZoneInfo("Asia/Seoul")

app = FastAPI(title="나라장터 입찰공고 검색")

DAY_CHOICES = [1, 3, 7, 14, 30]


def esc(v) -> str:
    return html.escape(str(v)) if v else ""


def fmt_amount(v) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "-"


def get_service_key() -> str | None:
    key = os.environ.get("G2B_SERVICE_KEY", "").strip()
    if not key:
        return None
    return unquote(key) if "%" in key else key


SORT_CHOICES = {
    "latest": "공고일 최신순",
    "deadline": "마감 임박순",
    "amount": "금액 높은순",
}


def render(params: dict, body: str) -> str:
    cat_options = '<option value="">전체 구분</option>' + "".join(
        f'<option value="{c}"{" selected" if c == params["cat"] else ""}>{c}</option>'
        for c in CATEGORIES
    )
    day_options = "".join(
        f'<option value="{d}"{" selected" if d == params["days"] else ""}>최근 {d}일</option>'
        for d in DAY_CHOICES
    )
    sort_options = "".join(
        f'<option value="{k}"{" selected" if k == params["sort"] else ""}>{v}</option>'
        for k, v in SORT_CHOICES.items()
    )
    min_amt = params["min_amt"] if params["min_amt"] is not None else ""
    max_amt = params["max_amt"] if params["max_amt"] is not None else ""
    form = f"""<form method="get" action="/" class="card">
  <div class="row">
    <input type="text" name="q" value="{esc(params['q'])}" placeholder="공고명 키워드 (예: 소프트웨어)">
    <select name="cat">{cat_options}</select>
    <select name="days">{day_options}</select>
    <button type="submit">검색</button>
  </div>
  <div class="row">
    <input type="text" name="org" value="{esc(params['org'])}" placeholder="기관명 (예: 학교, 서울시)" class="org">
    <input type="number" name="min_amt" value="{min_amt}" placeholder="최소금액(만원)" min="0" class="amt">
    <span class="tilde">~</span>
    <input type="number" name="max_amt" value="{max_amt}" placeholder="최대금액(만원)" min="0" class="amt">
    <select name="sort">{sort_options}</select>
  </div>
</form>"""
    return layout("나라장터 입찰공고 검색", "입찰공고 검색", "/", form + body)


def _amount_of(item: dict) -> int | None:
    try:
        return int(float(item.get("presmptPrce")))
    except (TypeError, ValueError):
        return None


def apply_filters(
    items: list[tuple[str, dict]],
    org: str,
    min_amt: int | None,
    max_amt: int | None,
) -> list[tuple[str, dict]]:
    """기관명 포함 검색 + 추정가격 범위(만원 단위 입력 → 원 환산) 필터."""
    result = []
    min_won = min_amt * 10000 if min_amt else None
    max_won = max_amt * 10000 if max_amt else None
    for category, it in items:
        if org:
            names = f"{it.get('dminsttNm') or ''} {it.get('ntceInsttNm') or ''}"
            if org not in names:
                continue
        if min_won is not None or max_won is not None:
            amount = _amount_of(it)
            if amount is None:
                continue
            if min_won is not None and amount < min_won:
                continue
            if max_won is not None and amount > max_won:
                continue
        result.append((category, it))
    return result


def sort_items(
    items: list[tuple[str, dict]], sort: str
) -> list[tuple[str, dict]]:
    if sort == "deadline":
        # 마감일 빠른순, 마감일 없는 공고는 뒤로
        return sorted(
            items, key=lambda x: (not x[1].get("bidClseDt"), x[1].get("bidClseDt") or "")
        )
    if sort == "amount":
        return sorted(items, key=lambda x: _amount_of(x[1]) or -1, reverse=True)
    return sorted(items, key=lambda x: x[1].get("bidNtceDt") or "", reverse=True)


def d_day_badge(close: str | None, today) -> str:
    """마감일 문자열('YYYY-MM-DD ...')로 D-day 배지 HTML 생성."""
    if not close:
        return ""
    try:
        close_date = datetime.strptime(str(close)[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""
    diff = (close_date - today).days
    if diff < 0:
        return '<span class="dd past">마감</span>'
    if diff == 0:
        return '<span class="dd hot">오늘</span>'
    cls = "hot" if diff <= 3 else ("warn" if diff <= 7 else "cool")
    return f'<span class="dd {cls}">D-{diff}</span>'


def render_rows(items: list[tuple[str, dict]]) -> str:
    today = datetime.now(KST).date()
    rows = []
    for category, it in items:
        url = it.get("bidNtceDtlUrl") or it.get("bidNtceUrl") or ""
        title = esc(it.get("bidNtceNm"))
        link = f'<a href="{esc(url)}" target="_blank">{title}</a>' if url else title
        cat_cls = f"cat-{category}" if category in CATEGORIES else "cat-default"
        close = it.get("bidClseDt")
        rows.append(
            "<tr>"
            f'<td><span class="cat {cat_cls}">{category}</span></td>'
            f"<td>{link}</td>"
            f"<td>{esc(it.get('dminsttNm'))}</td>"
            f'<td class="date">{esc(str(it.get("bidNtceDt") or "")[:16])}</td>'
            f'<td class="date">{esc(str(close or "")[:16])}{d_day_badge(close, today)}</td>'
            f'<td class="num" data-v="{_amount_of(it) if _amount_of(it) is not None else -1}">'
            f'{fmt_amount(it.get("presmptPrce"))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>구분</th><th>공고명</th><th>수요기관</th>"
        "<th>공고일</th><th>마감일</th><th>추정가격(원)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


@app.get("/", response_class=HTMLResponse)
def search(
    q: str = Query("", max_length=100),
    cat: str = Query(""),
    days: int = Query(7),
    org: str = Query("", max_length=50),
    min_amt: int | None = Query(None, ge=0),
    max_amt: int | None = Query(None, ge=0),
    sort: str = Query("latest"),
):
    days = days if days in DAY_CHOICES else 7
    sort = sort if sort in SORT_CHOICES else "latest"
    params = {
        "q": q, "cat": cat, "days": days, "org": org,
        "min_amt": min_amt, "max_amt": max_amt, "sort": sort,
    }
    key = get_service_key()
    if not key:
        return render(params,
            '<p class="error">G2B_SERVICE_KEY가 설정되지 않았습니다. '
            "Vercel 프로젝트 Settings → Environment Variables에 "
            "공공데이터포털에서 발급받은 키를 추가한 뒤 재배포하세요.</p>")

    now = datetime.now(KST)
    bgn = (now - timedelta(days=days)).strftime("%Y%m%d%H%M")
    end = now.strftime("%Y%m%d%H%M")
    categories = [cat] if cat in CATEGORIES else CATEGORIES

    client = G2BClient(key)

    def fetch(category: str):
        result = client.fetch_page(
            category, bgn, end, num_of_rows=100,
            bid_ntce_nm=q or None, timeout=20,
        )
        return category, result

    items: list[tuple[str, dict]] = []
    total = 0
    errors = []
    with ThreadPoolExecutor(max_workers=len(categories)) as pool:
        for future in [pool.submit(fetch, c) for c in categories]:
            try:
                category, result = future.result()
                total += result["total_count"]
                items.extend((category, it) for it in result["items"])
            except G2BApiError as e:
                errors.append(str(e))
            except Exception as e:  # 네트워크 오류 등
                errors.append(f"{type(e).__name__}: {e}")

    fetched = len(items)
    items = apply_filters(items, org.strip(), min_amt, max_amt)
    items = sort_items(items, sort)

    parts = []
    if errors:
        parts.append(f'<p class="error">{esc("; ".join(errors))}</p>')
    if items:
        shown = len(items)
        notes = []
        if shown < fetched:
            notes.append(f"조회 {fetched:,}건 중 필터 적용 후 {shown:,}건")
        else:
            notes.append(f"검색 결과 {shown:,}건")
        if total > fetched:
            notes.append(f"전체 {total:,}건 중 업무구분별 최신 100건까지 조회")
        parts.append(f'<p class="meta">{" · ".join(notes)}</p>')
        parts.append(render_rows(items))
    elif not errors:
        parts.append(
            '<p class="meta">검색 결과가 없습니다. 기간을 늘리거나 키워드·필터를 조정해보세요.</p>'
        )

    return render(params, "".join(parts))


GOV_REGIONS = ["대구", "경북", "부산", "전국"]
GOV_SORTS = {"deadline": "마감 임박순", "latest": "등록일 최신순"}
GOV_STATES = {"ing": "접수중만", "all": "전체 보기"}


def _gov_form(params: dict) -> str:
    src_options = '<option value="">전체 출처</option>' + "".join(
        f'<option value="{s}"{" selected" if s == params["src"] else ""}>{s}</option>'
        for s in gov_sources.SOURCES
    )
    region_options = '<option value="">전체 지역</option>' + "".join(
        f'<option value="{r}"{" selected" if r == params["region"] else ""}>{r}</option>'
        for r in GOV_REGIONS
    )
    state_options = "".join(
        f'<option value="{k}"{" selected" if k == params["state"] else ""}>{v}</option>'
        for k, v in GOV_STATES.items()
    )
    sort_options = "".join(
        f'<option value="{k}"{" selected" if k == params["sort"] else ""}>{v}</option>'
        for k, v in GOV_SORTS.items()
    )
    return f"""<form method="get" action="/gov" class="card">
  <div class="row">
    <input type="text" name="q" value="{esc(params['q'])}" placeholder="공고명·기관명 키워드 (예: AI, 콘텐츠, 수출)">
    <select name="src">{src_options}</select>
    <select name="region">{region_options}</select>
  </div>
  <div class="row">
    <select name="state">{state_options}</select>
    <select name="sort">{sort_options}</select>
    <button type="submit">검색</button>
  </div>
</form>"""


def _gov_rows(items: list[dict]) -> str:
    today = datetime.now(KST).date()
    rows = []
    for it in items:
        title = esc(it["title"])
        link = f'<a href="{esc(it["url"])}" target="_blank">{title}</a>' if it["url"] else title
        period = ""
        if it["begin"] or it["end"]:
            period = f'{esc(it["begin"] or "")} ~ {esc(it["end"] or "")}'
        elif it["status"]:
            period = esc(it["status"])
        rows.append(
            "<tr>"
            f'<td><span class="cat src-{it["source"]}">{it["source"]}</span></td>'
            f"<td>{link}</td>"
            f"<td>{esc(it['org'])}</td>"
            f"<td>{esc(it['region'])}</td>"
            f'<td class="date">{period}{d_day_badge(it["end"], today)}</td>'
            f'<td class="date">{esc(it["reg_date"] or "-")}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>출처</th><th>공고명</th><th>기관</th><th>지역</th>"
        "<th>접수기간</th><th>등록일</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


@app.get("/gov", response_class=HTMLResponse)
def gov(
    q: str = Query("", max_length=100),
    src: str = Query(""),
    region: str = Query(""),
    state: str = Query("ing"),
    sort: str = Query("deadline"),
):
    state = state if state in GOV_STATES else "ing"
    sort = sort if sort in GOV_SORTS else "deadline"
    params = {"q": q, "src": src, "region": region, "state": state, "sort": sort}

    names = [src] if src in gov_sources.SOURCES else list(gov_sources.SOURCES)
    items: list[dict] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {pool.submit(gov_sources.fetch_source, n): n for n in names}
        for future, name in futures.items():
            try:
                items.extend(future.result())
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")

    today = datetime.now(KST).date().isoformat()
    if q:
        needle = q.strip().lower()
        items = [it for it in items
                 if needle in it["title"].lower() or needle in it["org"].lower()]
    if region:
        items = [it for it in items
                 if region in it["region"] or region in it["title"]]
    if state == "ing":
        items = [it for it in items
                 if (it["end"] and it["end"] >= today)
                 or (not it["end"] and it["status"] != "마감")]

    if sort == "latest":
        items.sort(key=lambda x: x["reg_date"] or "", reverse=True)
    else:
        items.sort(key=lambda x: (x["end"] is None, x["end"] or ""))

    parts = []
    if errors:
        parts.append(f'<p class="error">일부 출처 수집 실패: {esc(", ".join(errors))}</p>')
    missing_keys = []
    if not os.environ.get("BIZINFO_API_KEY", "").strip() and (not src or src == "기업마당"):
        missing_keys.append("기업마당(BIZINFO_API_KEY)")
    if not os.environ.get("KSTARTUP_API_KEY", "").strip() and (not src or src == "K-Startup"):
        missing_keys.append("K-Startup(KSTARTUP_API_KEY)")
    if missing_keys:
        parts.append(
            f'<p class="meta">환경변수 미설정으로 제외된 출처: {esc(", ".join(missing_keys))}</p>'
        )
    if items:
        parts.append(f'<p class="meta">검색 결과 {len(items):,}건 · 출처 {len(names)}곳 실시간 수집</p>')
        parts.append(_gov_rows(items))
    else:
        parts.append('<p class="meta">검색 결과가 없습니다. 키워드·필터를 조정해보세요.</p>')

    return layout("정부과제 검색", "정부과제·지원사업 검색", "/gov", _gov_form(params) + "".join(parts))


@app.get("/health")
def health():
    return {"ok": True, "key_set": get_service_key() is not None}
