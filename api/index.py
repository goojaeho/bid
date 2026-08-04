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
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

import json

from fastapi.responses import RedirectResponse

from fastapi import Body

from app import auth, gov_sources, kakao, migrations, store, summarize, todos
from app.g2b_client import CATEGORIES, G2BApiError, G2BClient
from app.webui import icon, layout

KST = ZoneInfo("Asia/Seoul")

app = FastAPI(title="나라장터 입찰공고 검색")

migrations.run()  # DATABASE_URL 설정 시 콜드스타트에서 자동 마이그레이션


@app.middleware("http")
async def strip_vercel_rewrite_prefix(request: Request, call_next):
    # Vercel 리라이트가 경로를 /api/index로 바꿔 전달하는 경우 원래 경로로 복원
    path = request.scope.get("path", "")
    if path == "/api/index" or path.startswith("/api/index/"):
        path = path[len("/api/index"):] or "/"
        request.scope["path"] = path

    # 대표 주소(BASE_URL)가 아닌 우리 도메인으로 들어오면 대표 주소로 통일
    # (쿠키/OAuth 콜백이 한 호스트에서만 동작하도록)
    host = request.headers.get("host", "").split(":")[0].lower()
    canonical = auth.base_url().split("//", 1)[-1].split("/")[0].lower()
    if (host and host != canonical
            and (host == "oneaigen.com" or host.endswith(".oneaigen.com")
                 or host.endswith(".vercel.app"))):
        query = str(request.url.query)
        target = f"{auth.base_url()}{path}" + (f"?{query}" if query else "")
        return RedirectResponse(target, status_code=308)
    return await call_next(request)


def user_of(request: Request) -> str | None:
    return auth.read_session(request.cookies.get(auth.COOKIE_NAME))


def gate(request: Request):
    """로그인 강제. GOOGLE_CLIENT_ID 미설정 시에는 통과(설정 전 단계)."""
    if not auth.enabled():
        return None
    if user_of(request):
        return None
    return RedirectResponse("/login", status_code=302)

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

MAX_QUERY_GROUPS = 3  # 나라장터 API 호출 수 제한을 위한 OR 그룹 상한 (입찰공고에만 적용)

# 추천 검색 키워드 (환경변수로 변경 가능)
RECOMMEND_BID = os.environ.get("RECOMMEND_KEYWORDS_BID", "소프트웨어,AI,정보시스템")
RECOMMEND_GOV = os.environ.get(
    "RECOMMEND_KEYWORDS_GOV",
    "기술개발,R&D,글로벌,수출,투자,IR,AI,사업화,소프트웨어",
)


def parse_query(q: str) -> list[list[str]]:
    """검색어 파싱: 쉼표 = OR, 공백 = AND.

    'AI 바우처,콘텐츠' → [['ai', '바우처'], ['콘텐츠']]
    """
    groups = []
    for part in q.split(","):
        terms = [t.lower() for t in part.split() if t.strip()]
        if terms:
            groups.append(terms)
    return groups


def query_match(text: str, groups: list[list[str]]) -> bool:
    """OR 그룹 중 하나라도, 그룹 내 모든 단어가 포함되면 매칭."""
    if not groups:
        return True
    t = (text or "").lower()
    return any(all(term in t for term in group) for group in groups)


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
    form = f"""<form method="get" action="/bid" class="card">
  <div class="row">
    <span class="quick-label">빠른 검색</span>
    <a class="chip" href="/bid?q={quote(RECOMMEND_BID)}&days=7"
       title="추천 키워드: {esc(RECOMMEND_BID)}">{icon("star", 13)} {esc(RECOMMEND_BID.replace(",", " · "))}</a>
  </div>
  <div class="row">
    <input type="text" name="q" value="{esc(params['q'])}" placeholder="키워드 — 쉼표(,)는 또는, 공백은 그리고 (예: 소프트웨어,홍보)">
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
    return layout("나라장터 입찰공고 검색", "입찰공고 검색", "/bid", form + body,
                  user=params.get("_user"),
                  admin=auth.is_admin(params.get("_user")))


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
        payload = esc(json.dumps({
            "t": it.get("bidNtceNm") or "",
            "u": url,
            "s": f"나라장터·{category}",
            "o": it.get("dminsttNm") or "",
            "on": it.get("ntceInsttNm") or "",
            "b": str(it.get("bidNtceDt") or "")[:10],
            "e": str(close or "")[:10],
            "amt": fmt_amount(it.get("presmptPrce")) + "원" if _amount_of(it) else "",
            "ai": 0,
        }, ensure_ascii=False))
        rows.append(
            f'<tr class="xrow" data-item="{payload}" title="클릭하면 상세 정보가 열립니다">'
            '<td class="nowrap"><button type="button" class="row-fav" title="즐겨찾기">☆</button></td>'
            f'<td class="nowrap"><span class="cat {cat_cls}">{category}</span></td>'
            f'<td class="title-cell">{link}</td>'
            f'<td class="nowrap">{esc(it.get("dminsttNm"))}</td>'
            f'<td class="date">{esc(str(it.get("bidNtceDt") or "")[:16])}</td>'
            f'<td class="date">{esc(str(close or "")[:16])}{d_day_badge(close, today)}</td>'
            f'<td class="num" data-v="{_amount_of(it) if _amount_of(it) is not None else -1}">'
            f'{fmt_amount(it.get("presmptPrce"))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>저장</th><th>구분</th><th>공고명</th><th>수요기관</th>"
        "<th>공고일</th><th>마감일</th><th>추정가격(원)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


@app.get("/bid", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query("", max_length=100),
    cat: str = Query(""),
    days: str = Query("7"),
    org: str = Query("", max_length=50),
    min_amt: str = Query(""),
    max_amt: str = Query(""),
    sort: str = Query("latest"),
):
    redirect = gate(request)
    if redirect:
        return redirect
    days = int(days) if days.isdigit() and int(days) in DAY_CHOICES else 7
    min_amt = int(min_amt) if min_amt.strip().isdigit() else None
    max_amt = int(max_amt) if max_amt.strip().isdigit() else None
    sort = sort if sort in SORT_CHOICES else "latest"
    params = {
        "q": q, "cat": cat, "days": days, "org": org,
        "min_amt": min_amt, "max_amt": max_amt, "sort": sort,
        "_user": user_of(request),
    }
    # 첫 방문(쿼리 없음)에는 검색하지 않는다 — 즉시 로딩
    if not request.query_params:
        return render(params,
            '<p class="meta">검색 조건을 입력하고 검색 버튼을 눌러주세요. '
            "키워드 없이 검색하면 기간 내 전체 공고가 조회됩니다.</p>")
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
    groups = parse_query(q)[:MAX_QUERY_GROUPS]
    # OR 그룹별로 가장 긴 단어를 API 검색어로 쓰고, 나머지 조건은 로컬에서 거른다
    api_terms = [max(g, key=len) for g in groups] or [None]

    def fetch(category: str, term: str | None):
        result = client.fetch_page(
            category, bgn, end, num_of_rows=100,
            bid_ntce_nm=term, timeout=20,
        )
        return category, result

    items: list[tuple[str, dict]] = []
    seen: set[tuple[str, str]] = set()
    total = 0
    errors = []
    jobs = [(c, t) for c in categories for t in api_terms]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        for future in [pool.submit(fetch, c, t) for c, t in jobs]:
            try:
                category, result = future.result()
                total += result["total_count"]
                for it in result["items"]:
                    dedup_key = (str(it.get("bidNtceNo")), str(it.get("bidNtceOrd")))
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)
                    if query_match(it.get("bidNtceNm") or "", groups):
                        items.append((category, it))
            except G2BApiError as e:
                if str(e) not in errors:
                    errors.append(str(e))
            except Exception as e:  # 네트워크 오류 등 — 같은 유형은 한 번만 표시
                if "Timeout" in type(e).__name__:
                    msg = "나라장터 API 응답이 지연되고 있습니다. 잠시 후 다시 검색해주세요."
                else:
                    msg = f"{type(e).__name__}: {e}"
                if msg not in errors:
                    errors.append(msg)

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
    <span class="quick-label">빠른 검색</span>
    <a class="chip" href="/gov?q={quote(RECOMMEND_GOV)}&state=ing&sort=deadline"
       title="추천 키워드: {esc(RECOMMEND_GOV)}">{icon("star", 13)} {esc(RECOMMEND_GOV.replace(",", " · "))}</a>
  </div>
  <div class="row">
    <input type="text" name="q" value="{esc(params['q'])}" placeholder="키워드 — 쉼표(,)는 또는, 공백은 그리고 (예: AI,콘텐츠)">
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
        payload = esc(json.dumps({
            "t": it["title"], "u": it["url"] or "", "s": it["source"],
            "o": it["org"], "r": it["region"],
            "b": it["begin"] or "", "e": it["end"] or "", "st": it["status"],
            "tg": it.get("target") or "", "mt": it.get("method") or "",
            "sm": it.get("summary") or "", "ct": it.get("contact") or "",
            "ai": 1,
        }, ensure_ascii=False))
        rows.append(
            f'<tr class="xrow" data-item="{payload}" title="클릭하면 상세 정보가 열립니다">'
            '<td class="nowrap"><button type="button" class="row-fav" title="즐겨찾기">☆</button></td>'
            f'<td class="nowrap"><span class="cat src-{it["source"]}">{it["source"]}</span></td>'
            f'<td class="title-cell">{link}</td>'
            f'<td class="nowrap">{esc(it["org"])}</td>'
            f'<td class="nowrap">{esc(it["region"])}</td>'
            f'<td class="date">{period}{d_day_badge(it["end"], today)}</td>'
            f'<td class="date">{esc(it["reg_date"] or "-")}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>저장</th><th>출처</th><th>공고명</th><th>기관</th><th>지역</th>"
        "<th>접수기간</th><th>등록일</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


@app.get("/gov", response_class=HTMLResponse)
def gov(
    request: Request,
    q: str = Query("", max_length=100),
    src: str = Query(""),
    region: str = Query(""),
    state: str = Query("ing"),
    sort: str = Query("deadline"),
):
    redirect = gate(request)
    if redirect:
        return redirect
    state = state if state in GOV_STATES else "ing"
    sort = sort if sort in GOV_SORTS else "deadline"
    params = {"q": q, "src": src, "region": region, "state": state, "sort": sort}
    # 첫 방문(쿼리 없음)에는 수집하지 않는다 — 즉시 로딩
    if not request.query_params:
        return layout("정부과제 검색", "정부과제·지원사업 검색", "/gov",
            _gov_form(params)
            + '<p class="meta">검색 조건을 선택하고 검색 버튼을 눌러주세요. '
            "8개 출처(기업마당·K-Startup·NIPA·KOCCA·DIP·대구/경북/부산TP)를 실시간으로 수집합니다.</p>",
            user=user_of(request), admin=auth.is_admin(user_of(request)))

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
    groups = parse_query(q)
    if groups:
        items = [it for it in items
                 if query_match(f'{it["title"]} {it["org"]}', groups)]
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

    return layout("정부과제 검색", "정부과제·지원사업 검색", "/gov",
                  _gov_form(params) + "".join(parts), user=user_of(request),
                  admin=auth.is_admin(user_of(request)))


# ------------------------------------------------------------ 카카오 알림

def _notify_keywords() -> list[str]:
    raw = os.environ.get("NOTIFY_KEYWORDS", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def collect_new_notices(keywords: list[str]) -> list[dict]:
    """최근 1일 신규 공고 중 키워드 매칭 건 수집 (정부과제 + 나라장터)."""
    since = (datetime.now(KST) - timedelta(days=1)).date().isoformat()
    matched: list[dict] = []

    def hit(title: str) -> bool:
        t = (title or "").lower()
        return any(k.lower() in t for k in keywords)

    # 정부과제 8개 출처
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(gov_sources.fetch_source, n): n
                   for n in gov_sources.SOURCES}
        for future in futures:
            try:
                for it in future.result():
                    recent = (it["reg_date"] or it["begin"] or "") >= since
                    if recent and hit(it["title"]):
                        matched.append({"title": it["title"], "source": it["source"]})
            except Exception:
                continue

    # 나라장터 최근 1일
    key = get_service_key()
    if key:
        client = G2BClient(key)
        now = datetime.now(KST)
        bgn = (now - timedelta(days=1)).strftime("%Y%m%d%H%M")
        end = now.strftime("%Y%m%d%H%M")
        for category in CATEGORIES:
            try:
                result = client.fetch_page(category, bgn, end, num_of_rows=100)
                for it in result["items"]:
                    if hit(it.get("bidNtceNm") or ""):
                        matched.append({"title": it.get("bidNtceNm"), "source": f"나라장터·{category}"})
            except Exception:
                continue
    return matched


def build_notify_message(matched: list[dict]) -> str:
    lines = [f"[OneAIGen] 신규 공고 {len(matched)}건"]
    for m in matched[:4]:
        title = m["title"][:38] + ("…" if len(m["title"]) > 38 else "")
        lines.append(f"· {title} ({m['source']})")
    if len(matched) > 4:
        lines.append(f"…외 {len(matched) - 4}건")
    return "\n".join(lines)


@app.get("/notify")
def notify(request: Request):
    secret = os.environ.get("CRON_SECRET", "").strip()
    if secret:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {secret}" and request.query_params.get("secret") != secret:
            return {"ok": False, "error": "unauthorized"}

    keywords = _notify_keywords()
    if not keywords:
        return {"ok": False, "error": "NOTIFY_KEYWORDS 환경변수가 비어 있습니다 (예: 홍보,AI)"}
    matched = collect_new_notices(keywords)
    if not matched:
        return {"ok": True, "matched": 0, "sent": False}
    try:
        kakao.send_memo(build_notify_message(matched))
    except kakao.KakaoError as e:
        return {"ok": False, "matched": len(matched), "error": str(e)}
    return {"ok": True, "matched": len(matched), "sent": True}


def _fmt_done_at(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw).astimezone(KST).strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        return ""


TODO_JS = """<script>
function todoPost(url, body) {
  return fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}) })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { alert(d.error || "오류가 발생했습니다."); return; }
      location.reload();
    });
}
document.addEventListener("change", function (e) {
  var cb = e.target.closest(".todo-check");
  if (!cb || !cb.dataset.id) return;
  fetch("/api/todos/" + cb.dataset.id + "/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) {
      alert(d.error || "오류가 발생했습니다.");
      cb.checked = !cb.checked;
      return;
    }
    var li = cb.closest("li");
    if (li) li.querySelectorAll(".tt").forEach(function (t) {
      t.classList.toggle("tdone", cb.checked);
    });
  }).catch(function () { cb.checked = !cb.checked; });
});
document.querySelectorAll(".todo-del").forEach(function (btn) {
  btn.addEventListener("click", function () {
    if (confirm("삭제할까요?")) todoPost("/api/todos/" + btn.dataset.id + "/delete");
  });
});
document.querySelectorAll(".todo-act[data-due]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    todoPost("/api/todos/" + btn.dataset.id + "/update", { due_date: btn.dataset.due });
  });
});
document.querySelectorAll(".todo-sub").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var li = btn.closest("li");
    var next = li.nextElementSibling;
    if (next && next.classList.contains("sub-input-row")) {
      next.querySelector("input").focus();
      return;
    }
    var row = document.createElement("li");
    row.className = "sub-row sub-input-row";
    row.innerHTML = '<span class="sub-mark">&#8627;</span>'
      + '<input type="text" maxlength="200" placeholder="하위 업무 입력 후 Enter — 연속 추가 가능 (날짜 인식: 내일까지 등)">'
      + '<button type="button" class="todo-act sub-close">닫기</button>';
    li.after(row);
    var input = row.querySelector("input");
    var added = 0;
    function close() {
      if (added) location.reload(); else row.remove();
    }
    function submit() {
      var v = input.value.trim();
      if (!v) { close(); return; }
      input.disabled = true;
      fetch("/api/todos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: v,
          parent_id: parseInt(btn.dataset.id, 10),
          area: btn.dataset.area,
        }),
      }).then(function (r) { return r.json(); }).then(function (d) {
        input.disabled = false;
        if (!d.ok) { alert(d.error || "오류가 발생했습니다."); return; }
        added++;
        var doneLi = document.createElement("li");
        doneLi.className = "sub-row";
        var shownTitle = (d.todo && d.todo.title) || v;
        var dueNote = (d.todo && d.todo.due_date) ? ' <span class="done-at">' + escHtml(d.todo.due_date) + "</span>" : "";
        var cbHtml = (d.todo && d.todo.id) ? '<input type="checkbox" class="todo-check" data-id="' + d.todo.id + '">' : "";
        doneLi.innerHTML = '<span class="sub-mark">&#8627;</span>' + cbHtml + '<span class="tt">' + escHtml(shownTitle) + "</span>" + dueNote;
        row.before(doneLi);
        input.value = "";
        input.focus();
      }).catch(function () { input.disabled = false; });
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      if (e.key === "Escape") close();
    });
    row.querySelector(".sub-close").addEventListener("click", close);
    input.focus();
  });
});
document.querySelectorAll(".todo-edit").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var t = prompt("할 일 수정:", btn.dataset.title);
    if (t && t.trim()) todoPost("/api/todos/" + btn.dataset.id + "/update", { title: t.trim() });
  });
});
var form = document.getElementById("todo-form");
if (form) form.addEventListener("submit", function (e) {
  e.preventDefault();
  var title = document.getElementById("t-title").value.trim();
  if (!title) return;
  var due = document.getElementById("t-due").value;
  var cal = document.getElementById("t-cal").checked;
  if (cal && due) {
    var nd = new Date(due); nd.setDate(nd.getDate() + 1);
    var d2 = nd.toISOString().slice(0, 10).replace(/-/g, "");
    window.open("https://calendar.google.com/calendar/render?action=TEMPLATE"
      + "&text=" + encodeURIComponent("[할일] " + title)
      + "&dates=" + due.replace(/-/g, "") + "/" + d2, "_blank");
  }
  todoPost("/api/todos", {
    title: title,
    due: due,
    priority: parseInt(document.getElementById("t-pri").value, 10),
    category: document.getElementById("t-cat").value.trim(),
    area: document.getElementById("t-area").value,
  });
});
</script>"""


def _pri_dot(p) -> str:
    p = p if p in (1, 2, 3) else 2
    return f'<span class="pri pri-{p}" title="우선순위 {todos.PRIORITIES[p]}"></span>'


def _area_chip(t: dict) -> str:
    area = t.get("area") if t.get("area") in todos.AREAS else "work"
    area_icon = icon("briefcase", 11) if area == "work" else icon("leaf", 11)
    label = todos.AREAS[area]
    cat = (t.get("category") or "").strip()
    text = f"{area_icon} {esc(cat)}" if cat else f"{area_icon} {label}"
    return f'<span class="area-chip area-{area}">{text}</span>'


def _todo_cal_link(t: dict) -> str:
    due = t.get("due_date")
    if not due:
        return ""
    try:
        nd = (datetime.fromisoformat(due).date() + timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        return ""
    url = ("https://calendar.google.com/calendar/render?action=TEMPLATE"
           f"&text={quote('[할일] ' + t['title'])}"
           f"&dates={due.replace('-', '')}/{nd}")
    return f'<a class="todo-act" href="{esc(url)}" target="_blank" title="구글 캘린더에 추가">{icon("calendar", 13)}</a>'


def _todo_row(t: dict, today_iso: str, done: bool = False,
              is_child: bool = False, sub_count: int = 0) -> str:
    today_d = datetime.fromisoformat(today_iso).date()
    tomorrow = (today_d + timedelta(days=1)).isoformat()
    actions = ""
    badge = ""
    if not done:
        due = t.get("due_date")
        if due != today_iso:
            actions += (f'<button type="button" class="todo-act" data-due="{today_iso}" '
                        f'data-id="{t["id"]}">오늘</button>')
        if due != tomorrow:
            actions += (f'<button type="button" class="todo-act" data-due="{tomorrow}" '
                        f'data-id="{t["id"]}">내일로</button>')
        actions += _todo_cal_link(t)
        if not is_child and not t.get("parent_id"):
            area = t.get("area") if t.get("area") in todos.AREAS else "work"
            actions += (f'<button type="button" class="todo-act todo-sub" '
                        f'data-id="{t["id"]}" data-area="{area}" '
                        f'title="하위 업무 추가">{icon("plus", 12)} 하위</button>')
        actions += (f'<button type="button" class="todo-act todo-edit" data-id="{t["id"]}" '
                    f'data-title="{esc(t["title"])}">{icon("pencil", 12)}</button>')
        actions += (f'<button type="button" class="todo-del" data-id="{t["id"]}" '
                    f'title="삭제">{icon("x", 14)}</button>')
        badge = d_day_badge(t.get("due_date"), today_d) if t.get("due_date") else ""
    title_cls = "tt tdone" if done else "tt"
    done_at = (f'<span class="done-at">{_fmt_done_at(t.get("done_at"))}</span>'
               if done else "")
    child_mark = (f'<span class="sub-mark">{icon("corner-down-right", 13)}</span>'
                  if is_child else "")
    sub_chip = (f'<span class="area-chip" style="background:#eef0f4;color:#4b5265">'
                f'하위 {sub_count}</span>' if sub_count else "")
    li_cls = ' class="sub-row"' if is_child else ""
    return (
        f'<li{li_cls}>{child_mark}<input type="checkbox" class="todo-check" data-id="{t["id"]}"'
        f'{" checked" if done else ""}>'
        f'{_pri_dot(t.get("priority"))}'
        f'<span class="{title_cls}">{esc(t["title"])}</span>'
        f"{_area_chip(t)}{sub_chip}{badge}{done_at}"
        f'<span style="display:flex;gap:4px;flex-shrink:0">{actions}</span></li>'
    )


def _todo_tree_rows(items: list[dict], all_pending: list[dict], today_iso: str) -> str:
    """뷰에 해당하는 항목들을 상위-하위 트리로 렌더링.

    - 상위 항목 아래에 (뷰와 무관하게) 미완료 하위 업무를 함께 표시
    - 상위가 뷰에 없는 하위 항목은 ↳ 표시와 함께 단독 렌더링
    """
    children_map: dict[int, list[dict]] = {}
    for t in all_pending:
        pid = t.get("parent_id")
        if pid:
            children_map.setdefault(pid, []).append(t)

    shown_child_ids = set()
    rows = []
    for t in items:
        if t.get("parent_id"):
            continue  # 하위는 상위 아래에서 처리
        children = children_map.get(t["id"], [])
        rows.append(_todo_row(t, today_iso, sub_count=len(children)))
        for c in children:
            rows.append(_todo_row(c, today_iso, is_child=True))
            shown_child_ids.add(c["id"])
    # 상위가 이 뷰에 없는 하위 항목은 단독 표시
    for t in items:
        if t.get("parent_id") and t["id"] not in shown_child_ids:
            rows.append(_todo_row(t, today_iso, is_child=True))
    return "".join(rows)


def _home_content(data: dict, st: dict) -> str:
    """메인 대시보드: 통계 타일 + 업무/개인 누적 차트 + 오늘 할 일 요약."""
    today_iso = datetime.now(KST).date().isoformat()
    tiles = "".join([
        f'<div class="stat-tile"><div class="num">{st["today"]:,}</div>'
        f'<div class="lbl">오늘 완료 · 업무 {st["today_work"]} · 개인 {st["today_personal"]}</div></div>',
        f'<div class="stat-tile"><div class="num">{st["week"]:,}</div>'
        f'<div class="lbl">이번 주 완료 · 업무 {st["week_work"]} · 개인 {st["week_personal"]}</div></div>',
        f'<div class="stat-tile"><div class="num">{st["month"]:,}</div>'
        f'<div class="lbl">이번 달 완료</div></div>',
        f'<div class="stat-tile"><div class="num">{st["pending"]:,}</div>'
        f'<div class="lbl">대기 중</div></div>',
    ])

    max_total = max([d["work"] + d["personal"] for d in st["daily"]] + [1])
    cols = []
    for i, d in enumerate(st["daily"]):
        hw = int(d["work"] / max_total * 100)
        hp = int(d["personal"] / max_total * 100)
        segs = ""
        if hp:
            segs += f'<div class="bar-seg personal" style="height:{max(hp,2)}%"></div>'
        if hw and hp:
            segs += '<div class="bar-gap"></div>'
        if hw:
            cls = "bar-seg work" + ("" if hp else " first")
            segs += f'<div class="{cls}" style="height:{max(hw,2)}%"></div>'
        if not segs:
            segs = '<div class="bar zero" style="height:2%"></div>'
        label = d["label"] if (i % 2 == 1 or i == len(st["daily"]) - 1) else ""
        cols.append(
            f'<div class="bar-col" title="{d["label"]} · 업무 {d["work"]} · 개인 {d["personal"]}">'
            f'<div style="display:flex;flex-direction:column;justify-content:flex-end;'
            f'width:100%;max-width:26px;height:100%">{segs}</div>'
            f'<span class="bar-lbl">{label}</span></div>'
        )
    chart = (
        '<div class="card"><b style="font-size:0.92rem">최근 14일 완료 추이</b>'
        f'<div class="bars">{"".join(cols)}</div>'
        '<div class="legend">'
        '<span><span class="dot" style="background:#3557f0"></span>업무</span>'
        '<span><span class="dot" style="background:#0e9384"></span>개인</span>'
        "</div></div>"
    )

    today_items = [t for t in data["pending"]
                   if t.get("due_date") and t["due_date"] <= today_iso]
    rows = "".join(_todo_row(t, today_iso, is_child=bool(t.get("parent_id"))) for t in today_items[:8]) or \
        '<li><span class="meta">오늘 마감인 할 일이 없습니다.</span></li>'
    today_card = f"""<div class="card">
  <div class="row" style="justify-content:space-between">
    <b style="font-size:0.92rem">오늘 할 일 ({len(today_items)})</b>
    <a href="/todo" style="font-size:0.84rem;font-weight:700">할 일 관리 →</a>
  </div>
  <ul class="todo-list" style="margin-top:6px">{rows}</ul>
</div>"""
    return f'<div class="stat-row">{tiles}</div>{today_card}{chart}{TODO_JS}'


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    # 기존 북마크(/?q=...)는 쿼리 유지한 채 입찰 페이지로
    if request.query_params:
        return RedirectResponse(f"/bid?{request.query_params}", status_code=302)
    user = user_of(request)
    if not auth.is_admin(user):
        return RedirectResponse("/bid", status_code=302)
    if not todos.enabled():
        return layout("메인", icon("house", 20) + " 내 작업 공간", "/",
            '<div class="card"><p class="error">Supabase가 설정되지 않았습니다. '
            "SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수를 확인하세요.</p></div>",
            user=user, admin=True)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            data_f = pool.submit(todos.list_todos, user)
            rows_f = pool.submit(todos.stats_rows, user)
            data = data_f.result()
            st = todos.compute_stats(rows_f.result(), len(data["pending"]))
        content = _home_content(data, st)
    except store.StoreError as e:
        content = (f'<div class="card"><p class="error">{esc(str(e))}</p>'
                   '<p class="meta">Supabase SQL Editor에서 todos 테이블을 만들었는지 확인하세요.</p></div>')
    return layout("메인", icon("house", 20) + " 내 작업 공간", "/", content, user=user, admin=True)


def _admin_user(request: Request) -> str | None:
    user = user_of(request)
    return user if (user and auth.is_admin(user)) else None


TODO_VIEWS = {"today": "오늘", "upcoming": "예정", "all": "전체", "done": "완료"}
TODO_AREA_TABS = {"": "전체", "work": icon("briefcase", 13) + " 업무", "personal": icon("leaf", 13) + " 개인"}


def _seg(base: str, options: dict, current: str, keep: dict) -> str:
    links = []
    for value, label in options.items():
        qs = "&".join(f"{k}={quote(str(v))}" for k, v in {**keep, base: value}.items())
        on = ' class="on"' if value == current else ""
        links.append(f'<a href="/todo?{qs}"{on}>{label}</a>')
    return f'<div class="seg">{"".join(links)}</div>'


@app.get("/todo", response_class=HTMLResponse)
def todo_page(request: Request, area: str = Query(""), view: str = Query("today")):
    redirect = gate(request)
    if redirect:
        return redirect
    user = user_of(request)
    if not auth.is_admin(user):
        return RedirectResponse("/bid", status_code=302)
    area = area if area in ("work", "personal") else ""
    view = view if view in TODO_VIEWS else "today"
    today_iso = datetime.now(KST).date().isoformat()

    if not todos.enabled():
        return layout("할 일", icon("list-checks", 20) + " 할 일", "/todo",
            '<div class="card"><p class="error">Supabase가 설정되지 않았습니다.</p></div>',
            user=user, admin=True)
    try:
        data = todos.list_todos(user)
    except store.StoreError as e:
        return layout("할 일", icon("list-checks", 20) + " 할 일", "/todo",
            f'<div class="card"><p class="error">{esc(str(e))}</p>'
            '<p class="meta">Supabase에서 todos 테이블 확장 쿼리를 실행했는지 확인하세요.</p></div>',
            user=user, admin=True)

    pending = [t for t in data["pending"] if not area or t.get("area") == area]
    done = [t for t in data["done"] if not area or t.get("area") == area]
    today_items = [t for t in pending if t.get("due_date") and t["due_date"] <= today_iso]
    upcoming = [t for t in pending if t.get("due_date") and t["due_date"] > today_iso]

    view_counts = {"today": len(today_items), "upcoming": len(upcoming),
                   "all": len(pending), "done": len(done)}
    view_labels = {k: f"{v} {view_counts[k]}" for k, v in TODO_VIEWS.items()}

    area_seg = _seg("area", TODO_AREA_TABS, area, {"view": view})
    view_seg = _seg("view", view_labels, view, {"area": area})

    default_area = area or "work"
    form = f"""<form id="todo-form" class="card">
  <div class="row">
    <input type="text" id="t-title" placeholder="할 일 입력 — '보고서 금요일까지'처럼 쓰면 마감일 자동 인식" maxlength="200" autocomplete="off">
    <button type="submit">추가</button>
  </div>
  <div class="row">
    <select id="t-area">
      <option value="work"{" selected" if default_area == "work" else ""}>업무</option>
      <option value="personal"{" selected" if default_area == "personal" else ""}>개인</option>
    </select>
    <input type="text" id="t-cat" placeholder="카테고리 (예: 제안서, 운동)" style="flex:0 1 180px;min-width:130px">
    <input type="date" id="t-due" title="마감일">
    <select id="t-pri">
      <option value="1">우선순위 높음</option>
      <option value="2" selected>우선순위 보통</option>
      <option value="3">우선순위 낮음</option>
    </select>
    <label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;color:var(--muted)">
      <input type="checkbox" id="t-cal" style="width:16px;height:16px;accent-color:var(--accent)">{icon("calendar", 14)} 캘린더에도 추가
    </label>
  </div>
</form>"""

    if view == "today":
        rows = _todo_tree_rows(today_items, pending, today_iso) or \
            '<li><span class="meta">오늘 마감인 할 일이 없습니다. 🎉</span></li>'
        body = f'<div class="card"><ul class="todo-list">{rows}</ul></div>'
    elif view == "upcoming":
        groups = []
        current_due = None
        for t in upcoming:
            if t["due_date"] != current_due:
                current_due = t["due_date"]
                d = datetime.fromisoformat(current_due).date()
                groups.append(f'</ul><p class="due-group">{d.month}/{d.day} ({["월","화","수","목","금","토","일"][d.weekday()]})</p><ul class="todo-list">')
            groups.append(_todo_row(t, today_iso, is_child=bool(t.get("parent_id"))))
        inner = "".join(groups)[5:] if groups else '<span class="meta">예정된 할 일이 없습니다.</span>'
        body = f'<div class="card">{inner}</ul></div>'
    elif view == "done":
        rows = "".join(_todo_row(t, today_iso, done=True) for t in done) or \
            '<li><span class="meta">완료한 할 일이 없습니다.</span></li>'
        body = f'<div class="card"><ul class="todo-list">{rows}</ul></div>'
    else:  # all
        rows = _todo_tree_rows(pending, pending, today_iso) or \
            '<li><span class="meta">할 일이 없습니다. 위에서 추가해보세요!</span></li>'
        body = f'<div class="card"><ul class="todo-list">{rows}</ul></div>'

    content = (f'<div class="row" style="margin-bottom:12px;justify-content:space-between">'
               f"{area_seg}{view_seg}</div>{form}{body}{TODO_JS}")
    return layout("할 일 관리", icon("list-checks", 20) + " 할 일", "/todo", content, user=user, admin=True)


@app.post("/api/todos")
def api_todo_add(request: Request, body: dict = Body(...)):
    user = _admin_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        created = todos.add_todo(
            user,
            str(body.get("title", "")),
            area=str(body.get("area", "work")),
            category=str(body.get("category", "")),
            due_date=str(body.get("due")) if body.get("due") else None,
            priority=body.get("priority") if isinstance(body.get("priority"), int) else 2,
            parent_id=body.get("parent_id") if isinstance(body.get("parent_id"), int) else None,
        )
        return {"ok": True, "todo": created}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/todos/{todo_id}/update")
def api_todo_update(request: Request, todo_id: int, body: dict = Body(...)):
    user = _admin_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        todos.update_todo(user, todo_id, body)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/todos/{todo_id}/toggle")
def api_todo_toggle(request: Request, todo_id: int):
    user = _admin_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        todos.toggle_todo(user, todo_id)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/todos/{todo_id}/delete")
def api_todo_delete(request: Request, todo_id: int):
    user = _admin_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        todos.delete_todo(user, todo_id)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


PDF_PAGE = """<div class="card">
  <p style="margin:4px 0 6px"><b>PDF 압축</b> — 파일이 서버로 전송되지 않고 이 브라우저 안에서 압축됩니다.
  IR 덱처럼 민감한 문서도 안전하고, 100MB가 넘는 큰 파일도 처리됩니다.</p>
  <div class="row" style="margin-top:12px">
    <input type="file" id="pdf-file" accept="application/pdf,.pdf" style="flex:1;min-width:200px">
    <select id="pdf-preset">
      <option value="/screen">고압축 (화면 공유용)</option>
      <option value="/ebook" selected>균형 (이메일 첨부용 추천)</option>
      <option value="/printer">고화질 (인쇄용)</option>
    </select>
    <button type="button" id="pdf-run">압축하기</button>
  </div>
  <p class="meta" style="margin-top:10px">첫 사용 시 압축 엔진(16MB)을 한 번 내려받습니다.
  대용량 파일(100MB+)은 1~3분 정도 걸릴 수 있어요 — 탭을 닫지 마세요.</p>
  <div id="pdf-status"></div>
  <div id="pdf-result"></div>
</div>
<script>
(function () {
  var GS_CDN = "https://cdn.jsdelivr.net/npm/@jspawn/ghostscript-wasm@0.0.2/";
  var workerCode = [
    'importScripts("' + GS_CDN + 'gs.js");',
    'self.onmessage = function (e) {',
    '  var buf = e.data.buf, preset = e.data.preset;',
    '  Module({ noInitialRun: true,',
    '    locateFile: function (f) { return "' + GS_CDN + '" + f; },',
    '    print: function (t) {',
    '      var m = /^Page ([0-9]+)/.exec(t);',
    '      if (m) postMessage({ type: "page", page: +m[1] });',
    '    },',
    '    printErr: function () {} })',
    '  .then(function (gs) {',
    '    gs.FS.writeFile("in.pdf", new Uint8Array(buf));',
    '    var code = gs.callMain(["-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5",',
    '      "-dPDFSETTINGS=" + preset, "-dNOPAUSE", "-dBATCH",',
    '      "-sOutputFile=out.pdf", "in.pdf"]);',
    '    if (code !== 0) { postMessage({ type: "error", message: "압축 실패 (코드 " + code + ")" }); return; }',
    '    var out = gs.FS.readFile("out.pdf");',
    '    postMessage({ type: "done", out: out.buffer }, [out.buffer]);',
    '  })',
    '  .catch(function (err) { postMessage({ type: "error", message: String(err) }); });',
    '};',
  ].join("\\n");

  function mb(n) { return (n / 1048576).toFixed(2) + " MB"; }
  var statusEl = document.getElementById("pdf-status");
  var resultEl = document.getElementById("pdf-result");
  var runBtn = document.getElementById("pdf-run");
  var timer = null;

  runBtn.addEventListener("click", function () {
    var fileInput = document.getElementById("pdf-file");
    var file = fileInput.files && fileInput.files[0];
    if (!file) { alert("PDF 파일을 선택해주세요."); return; }
    var preset = document.getElementById("pdf-preset").value;
    runBtn.disabled = true;
    resultEl.innerHTML = "";
    var start = Date.now(), lastPage = 0;
    function setStatus(extra) {
      var sec = Math.round((Date.now() - start) / 1000);
      statusEl.innerHTML = '<p class="meta">' + extra + " · " + sec + "초 경과</p>";
    }
    setStatus("압축 엔진 로딩 중…");
    timer = setInterval(function () {
      setStatus(lastPage ? lastPage + "페이지 처리 중…" : "압축 엔진 로딩 중…");
    }, 1000);

    file.arrayBuffer().then(function (buf) {
      var origSize = buf.byteLength;
      var worker = new Worker(URL.createObjectURL(
        new Blob([workerCode], { type: "text/javascript" })));
      worker.onmessage = function (e) {
        var d = e.data;
        if (d.type === "page") { lastPage = d.page; return; }
        clearInterval(timer);
        runBtn.disabled = false;
        worker.terminate();
        if (d.type === "error") {
          statusEl.innerHTML = '<p class="error">' + d.message + "</p>";
          return;
        }
        var outBlob = new Blob([d.out], { type: "application/pdf" });
        var name = file.name.replace(/\\.pdf$/i, "") + "_압축.pdf";
        var saved = 100 * (1 - outBlob.size / origSize);
        statusEl.innerHTML = "";
        resultEl.innerHTML = '<div class="ai-sum" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">'
          + "<span><b>" + mb(origSize) + "</b> → <b>" + mb(outBlob.size) + "</b> ("
          + (saved > 0 ? saved.toFixed(0) + "% 절감" : "절감 없음") + ")</span>"
          + '<a id="pdf-dl" class="btn-outline" style="padding:7px 16px">내려받기</a></div>';
        var a = document.getElementById("pdf-dl");
        a.href = URL.createObjectURL(outBlob);
        a.download = name;
      };
      worker.onerror = function (err) {
        clearInterval(timer);
        runBtn.disabled = false;
        statusEl.innerHTML = '<p class="error">엔진 오류: ' + (err.message || err) + "</p>";
      };
      worker.postMessage({ buf: buf, preset: preset }, [buf]);
    });
  });
})();
</script>"""


@app.get("/pdf", response_class=HTMLResponse)
def pdf_page(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    user = user_of(request)
    if not auth.is_admin(user):
        return RedirectResponse("/bid", status_code=302)
    return layout("PDF 압축", icon("download", 20) + " PDF 압축", "/pdf",
                  PDF_PAGE, user=user, admin=True)


@app.get("/login", response_class=HTMLResponse)
def login_page():
    if not auth.enabled():
        content = ('<div class="card"><p class="error">구글 로그인이 아직 설정되지 않았습니다. '
                   "Vercel 환경변수에 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET을 추가하세요.</p></div>")
    else:
        content = f"""<div class="card" style="text-align:center;padding:40px 20px">
  <p style="margin:0 0 6px;font-weight:800;font-size:1.05rem">회사 전용 서비스입니다</p>
  <p class="meta" style="margin:0 0 20px">구글 계정으로 로그인해주세요.</p>
  <a href="{auth.login_url()}"><button type="button" style="padding:11px 28px">Google 계정으로 로그인</button></a>
</div>"""
    return layout("로그인", "로그인", "", content)


@app.get("/auth/callback")
def auth_callback(code: str = Query("")):
    if not code:
        return RedirectResponse("/login", status_code=302)
    try:
        email = auth.handle_callback(code)
    except auth.AuthError as e:
        return HTMLResponse(layout("로그인 실패", "로그인",
            "", f'<div class="card"><p class="error">{esc(str(e))}</p></div>'))
    if not auth.allowed(email):
        return HTMLResponse(layout("접근 거부", "로그인", "",
            f'<div class="card"><p class="error">{esc(email)} 계정은 접근 권한이 없습니다. '
            "관리자에게 문의하세요.</p></div>"))
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        auth.COOKIE_NAME, auth.make_session(email),
        max_age=auth.SESSION_MAX_AGE, httponly=True, secure=True, samesite="lax",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.get("/favs", response_class=HTMLResponse)
def favs_page(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    storage_note = ("로그인 계정에 저장되어 어느 기기에서나 보입니다."
                    if (store.enabled() and user_of(request))
                    else "현재 이 브라우저에 저장됩니다.")
    content = f"""<div class="card">
  <p class="meta" style="margin:2px 0">목록에서 ☆를 누르면 즐겨찾기에 저장됩니다. {storage_note}</p>
</div>""" + """
<div id="fav-list"></div>
<script>
window.renderFavs = function () {
  var box = document.getElementById("fav-list");
  var map = favs();
  var items = Object.values(map);
  if (!items.length) {
    box.innerHTML = '<p class="meta">저장된 공고가 없습니다.</p>';
    return;
  }
  items.sort(function (a, b) { return (a.e || "9999") < (b.e || "9999") ? -1 : 1; });
  var rows = items.map(function (it) {
    var cal = calUrl(it);
    return '<tr class="xrow" data-item="' + escHtml(JSON.stringify(it)) + '">'
      + '<td class="nowrap"><span class="cat cat-default">' + escHtml(it.s || "") + "</span></td>"
      + '<td class="title-cell"><a href="' + escHtml(it.u) + '" target="_blank">' + escHtml(it.t) + "</a></td>"
      + '<td class="nowrap">' + escHtml(it.o || "") + "</td>"
      + '<td class="date">' + escHtml(it.e || "-") + "</td>"
      + '<td class="nowrap">'
      + (cal ? '<a class="btn-outline" style="padding:4px 10px;font-size:0.8rem" href="' + escHtml(cal) + '" target="_blank">캘린더</a> ' : "")
      + '<button type="button" class="btn-outline fav-del" style="padding:4px 10px;font-size:0.8rem" data-u="' + escHtml(it.u) + '">삭제</button>'
      + "</td></tr>";
  }).join("");
  box.innerHTML = '<div class="table-wrap"><table><thead><tr>'
    + "<th>출처</th><th>공고명</th><th>기관</th><th>마감일</th><th>동작</th>"
    + "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  box.querySelectorAll(".fav-del").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var map = favs(); delete map[btn.dataset.u]; saveFavs(map); window.renderFavs();
    });
  });
};
document.addEventListener("DOMContentLoaded", window.renderFavs);
</script>"""
    return layout("즐겨찾기", icon("star", 20) + " 즐겨찾기", "/favs", content, user=user_of(request),
                  admin=auth.is_admin(user_of(request)))


@app.get("/api/favs")
def get_favs_api(request: Request):
    email = user_of(request)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다."}
    if not store.enabled():
        return {"ok": False, "error": "서버 저장소가 설정되지 않았습니다."}
    try:
        return {"ok": True, "favs": store.get_favs(email)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/favs")
def set_favs_api(request: Request, favs: dict = Body(...)):
    email = user_of(request)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다."}
    if not store.enabled():
        return {"ok": False, "error": "서버 저장소가 설정되지 않았습니다."}
    try:
        store.set_favs(email, favs)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.get("/summarize")
def summarize_endpoint(request: Request, u: str = Query("", max_length=500)):
    if auth.enabled() and not user_of(request):
        return {"ok": False, "error": "로그인이 필요합니다."}
    if not u:
        return {"ok": False, "error": "URL이 없습니다."}
    try:
        return {"ok": True, "summary": summarize.summarize_url(u)}
    except summarize.SummarizeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/kakao", response_class=HTMLResponse)
def kakao_page():
    connected = kakao.refresh_token() is not None
    status = ("카카오 계정이 연결되어 있습니다. ✔" if connected
              else "아직 연결되지 않았습니다.")
    keywords = ", ".join(_notify_keywords()) or "(미설정 — NOTIFY_KEYWORDS 환경변수)"
    content = f"""<div class="card">
  <p style="margin:4px 0 12px"><b>카카오톡 알림 설정</b> — 새 공고가 뜨면 내 카카오톡으로 알림을 받습니다.</p>
  <p class="meta">상태: {status} · 알림 키워드: {esc(keywords)}</p>
  <p style="margin:14px 0 4px">
    <a href="{kakao.authorize_url()}"><button type="button">카카오 계정 연결하기</button></a>
    <a href="/kakao/test" style="margin-left:8px"><button type="button" style="background:#6b7280;border-color:#6b7280">테스트 메시지 보내기</button></a>
  </p>
  <p class="meta" style="margin-top:12px">연결 후 발급되는 토큰을 Vercel 환경변수(KAKAO_REFRESH_TOKEN)에 넣으면
  매일 아침 8시에 신규 공고 알림이 발송됩니다.</p>
</div>"""
    return layout("카카오 알림 설정", "카카오톡 알림", "/kakao", content)


@app.get("/kakao/callback", response_class=HTMLResponse)
def kakao_callback(code: str = Query("")):
    if not code:
        return layout("카카오 연결 실패", "카카오톡 알림", "/kakao",
                      '<div class="card"><p class="error">인가 코드가 없습니다. 다시 시도해주세요.</p></div>')
    try:
        tokens = kakao.exchange_code(code)
        kakao.send_memo(
            "[OneAIGen] 카카오톡 알림 연결 성공! 이제 신규 공고 알림을 받을 수 있습니다.",
            access_token=tokens["access_token"],
        )
        sent_note = "테스트 메시지를 방금 카카오톡으로 보냈습니다. 확인해보세요!"
    except kakao.KakaoError as e:
        return layout("카카오 연결 실패", "카카오톡 알림", "/kakao",
                      f'<div class="card"><p class="error">{esc(str(e))}</p></div>')
    refresh = tokens.get("refresh_token", "")
    content = f"""<div class="card">
  <p style="margin:4px 0"><b>연결 성공!</b> {sent_note}</p>
  <p style="margin:14px 0 6px">마지막 단계 — 아래 토큰을 Vercel 환경변수에 추가하세요:</p>
  <p class="meta">이름: <b>KAKAO_REFRESH_TOKEN</b></p>
  <p style="word-break:break-all;background:#f5f6f8;border-radius:8px;padding:12px;font-size:0.85rem">{esc(refresh)}</p>
  <p class="meta">Vercel → Settings → Environment Variables → 추가 후 Redeploy 하면 매일 아침 알림이 활성화됩니다.</p>
</div>"""
    return layout("카카오 연결 완료", "카카오톡 알림", "/kakao", content)


@app.get("/kakao/test", response_class=HTMLResponse)
def kakao_test():
    try:
        kakao.send_memo("[OneAIGen] 테스트 메시지입니다. 알림 연동이 정상 작동 중입니다.")
        msg = '<p style="margin:4px 0">테스트 메시지를 보냈습니다. 카카오톡을 확인하세요.</p>'
    except kakao.KakaoError as e:
        msg = f'<p class="error">{esc(str(e))}</p>'
    return layout("카카오 테스트", "카카오톡 알림", "/kakao", f'<div class="card">{msg}</div>')


@app.get("/health")
def health():
    return {
        "ok": True,
        "key_set": get_service_key() is not None,
        "login": auth.enabled(),
        "store": store.enabled(),
        "ai": summarize.gemini_key() is not None,
        "kakao": kakao.refresh_token() is not None,
        "db": migrations.db_status(),
    }
