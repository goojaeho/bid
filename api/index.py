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

from app.g2b_client import CATEGORIES, G2BApiError, G2BClient

KST = ZoneInfo("Asia/Seoul")

app = FastAPI(title="나라장터 입찰공고 검색")

DAY_CHOICES = [1, 3, 7, 14, 30]

PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>나라장터 입찰공고 검색</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
         max-width: 1100px; margin: 0 auto; padding: 16px; }}
  h1 {{ font-size: 1.3rem; }}
  form {{ margin-bottom: 16px; }}
  .row {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
         margin-bottom: 8px; }}
  input[type=text] {{ flex: 1; min-width: 200px; padding: 8px; }}
  input[type=text].org {{ flex: 0 1 220px; min-width: 160px; }}
  input[type=number].amt {{ width: 130px; padding: 8px; }}
  select, button {{ padding: 8px; }}
  button {{ cursor: pointer; font-weight: bold; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
  th, td {{ border-bottom: 1px solid #8884; padding: 6px 8px; text-align: left;
           vertical-align: top; }}
  th {{ white-space: nowrap; }}
  td.num {{ text-align: right; white-space: nowrap; }}
  td.date {{ white-space: nowrap; }}
  .cat {{ display: inline-block; padding: 1px 6px; border-radius: 4px;
         background: #8883; font-size: 0.75rem; white-space: nowrap; }}
  .error {{ color: #c00; padding: 12px; border: 1px solid #c003;
           border-radius: 6px; }}
  .meta {{ color: #888; font-size: 0.8rem; margin: 8px 0; }}
</style>
</head>
<body>
<h1>나라장터 입찰공고 검색</h1>
<form method="get" action="/">
  <div class="row">
    <input type="text" name="q" value="{q}" placeholder="공고명 키워드 (예: 소프트웨어)">
    <select name="cat">{cat_options}</select>
    <select name="days">{day_options}</select>
    <button type="submit">검색</button>
  </div>
  <div class="row">
    <input type="text" name="org" value="{org}" placeholder="기관명 (예: 학교, 서울시)" class="org">
    <input type="number" name="min_amt" value="{min_amt}" placeholder="최소금액(만원)" min="0" class="amt">
    <span>~</span>
    <input type="number" name="max_amt" value="{max_amt}" placeholder="최대금액(만원)" min="0" class="amt">
    <select name="sort">{sort_options}</select>
  </div>
</form>
{body}
</body>
</html>"""


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
    cat_options = '<option value="">전체</option>' + "".join(
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
    return PAGE_TEMPLATE.format(
        q=esc(params["q"]),
        org=esc(params["org"]),
        min_amt=params["min_amt"] if params["min_amt"] is not None else "",
        max_amt=params["max_amt"] if params["max_amt"] is not None else "",
        cat_options=cat_options,
        day_options=day_options,
        sort_options=sort_options,
        body=body,
    )


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


def render_rows(items: list[tuple[str, dict]]) -> str:
    rows = []
    for category, it in items:
        url = it.get("bidNtceDtlUrl") or it.get("bidNtceUrl") or ""
        title = esc(it.get("bidNtceNm"))
        link = f'<a href="{esc(url)}" target="_blank">{title}</a>' if url else title
        rows.append(
            "<tr>"
            f'<td><span class="cat">{category}</span></td>'
            f"<td>{link}</td>"
            f"<td>{esc(it.get('dminsttNm'))}</td>"
            f'<td class="date">{esc(it.get("bidNtceDt"))}</td>'
            f'<td class="date">{esc(it.get("bidClseDt"))}</td>'
            f'<td class="num">{fmt_amount(it.get("presmptPrce"))}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>구분</th><th>공고명</th><th>수요기관</th>"
        "<th>공고일</th><th>마감일</th><th>추정가격(원)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
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


@app.get("/health")
def health():
    return {"ok": True, "key_set": get_service_key() is not None}
