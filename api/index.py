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
  form {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
         margin-bottom: 16px; }}
  input[type=text] {{ flex: 1; min-width: 200px; padding: 8px; }}
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
  <input type="text" name="q" value="{q}" placeholder="공고명 키워드 (예: 소프트웨어)">
  <select name="cat">{cat_options}</select>
  <select name="days">{day_options}</select>
  <button type="submit">검색</button>
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


def render(q: str, cat: str, days: int, body: str) -> str:
    cat_options = '<option value="">전체</option>' + "".join(
        f'<option value="{c}"{" selected" if c == cat else ""}>{c}</option>'
        for c in CATEGORIES
    )
    day_options = "".join(
        f'<option value="{d}"{" selected" if d == days else ""}>최근 {d}일</option>'
        for d in DAY_CHOICES
    )
    return PAGE_TEMPLATE.format(
        q=esc(q), cat_options=cat_options, day_options=day_options, body=body
    )


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
):
    days = days if days in DAY_CHOICES else 7
    key = get_service_key()
    if not key:
        return render(q, cat, days,
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

    items.sort(key=lambda x: x[1].get("bidNtceDt") or "", reverse=True)

    parts = []
    if errors:
        parts.append(f'<p class="error">{esc("; ".join(errors))}</p>')
    if items:
        shown = len(items)
        note = f" (전체 {total:,}건 중 업무구분별 최신 100건까지 표시)" if total > shown else ""
        parts.append(f'<p class="meta">검색 결과 {shown:,}건{note}</p>')
        parts.append(render_rows(items))
    elif not errors:
        parts.append('<p class="meta">검색 결과가 없습니다. 기간을 늘리거나 키워드를 바꿔보세요.</p>')

    return render(q, cat, days, "".join(parts))


@app.get("/health")
def health():
    return {"ok": True, "key_set": get_service_key() is not None}
