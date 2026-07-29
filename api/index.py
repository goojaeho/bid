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

from app import auth, gov_sources, kakao, summarize
from app.g2b_client import CATEGORIES, G2BApiError, G2BClient
from app.webui import layout

KST = ZoneInfo("Asia/Seoul")

app = FastAPI(title="나라장터 입찰공고 검색")


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
    form = f"""<form method="get" action="/" class="card">
  <div class="row">
    <span class="quick-label">빠른 검색</span>
    <a class="chip" href="/?q={quote(RECOMMEND_BID)}&days=7"
       title="추천 키워드: {esc(RECOMMEND_BID)}">⭐ {esc(RECOMMEND_BID.replace(",", " · "))}</a>
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
    return layout("나라장터 입찰공고 검색", "입찰공고 검색", "/", form + body,
                  user=params.get("_user"))


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
        "<th>구분</th><th>공고명</th><th>수요기관</th>"
        "<th>공고일</th><th>마감일</th><th>추정가격(원)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


@app.get("/", response_class=HTMLResponse)
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
    <span class="quick-label">빠른 검색</span>
    <a class="chip" href="/gov?q={quote(RECOMMEND_GOV)}&state=ing&sort=deadline"
       title="추천 키워드: {esc(RECOMMEND_GOV)}">⭐ {esc(RECOMMEND_GOV.replace(",", " · "))}</a>
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
        "<th>출처</th><th>공고명</th><th>기관</th><th>지역</th>"
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
            user=user_of(request))

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
                  _gov_form(params) + "".join(parts), user=user_of(request))


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
    content = """<div class="card">
  <p class="meta" style="margin:2px 0">공고 상세 패널에서 ☆ 즐겨찾기를 누르면 이 브라우저에 저장됩니다.</p>
</div>
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
      + (cal ? '<a class="btn-outline" style="padding:4px 10px;font-size:0.8rem" href="' + escHtml(cal) + '" target="_blank">📅</a> ' : "")
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
    return layout("즐겨찾기", "⭐ 즐겨찾기", "/favs", content, user=user_of(request))


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
    status = ("✅ 카카오 계정이 연결되어 있습니다." if connected
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
  <p style="margin:4px 0"><b>✅ 연결 성공!</b> {sent_note}</p>
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
        msg = '<p style="margin:4px 0">✅ 테스트 메시지를 보냈습니다. 카카오톡을 확인하세요.</p>'
    except kakao.KakaoError as e:
        msg = f'<p class="error">{esc(str(e))}</p>'
    return layout("카카오 테스트", "카카오톡 알림", "/kakao", f'<div class="card">{msg}</div>')


@app.get("/health")
def health():
    return {"ok": True, "key_set": get_service_key() is not None}
