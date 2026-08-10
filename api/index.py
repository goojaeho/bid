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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import json

from fastapi.responses import RedirectResponse

from fastapi import Body

from app import (auth, english, genie, gov_sources, kakao, migrations, searches,
                 store, summarize, todos)
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

# 사용자별 빠른 검색: 칩 목록 로드 + 현재 조건 저장 (bid/gov 공용)
QUICK_JS = """<script>
(function () {
  var slot = document.getElementById("quick-user");
  var saveBtn = document.getElementById("quick-save");
  if (!slot || !saveBtn) return;
  var page = slot.dataset.page;

  function load() {
    fetch("/api/searches?page=" + page).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        slot.innerHTML = "";
        d.items.forEach(function (it) {
          var a = document.createElement("a");
          a.className = "chip chip-user";
          a.href = "/" + page + "?" + new URLSearchParams(it.params).toString();
          a.title = Object.keys(it.params).map(function (k) {
            return k + "=" + it.params[k];
          }).join(", ");
          var t = document.createElement("span");
          t.textContent = it.label;
          a.appendChild(t);
          var x = document.createElement("button");
          x.type = "button";
          x.className = "chip-x";
          x.textContent = "\\u00d7";
          x.title = "삭제";
          x.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (!confirm('"' + it.label + '" 빠른 검색을 삭제할까요?')) return;
            fetch("/api/searches/" + it.id + "/delete", { method: "POST" })
              .then(load);
          });
          a.appendChild(x);
          slot.appendChild(a);
        });
      }).catch(function () {});
  }

  saveBtn.addEventListener("click", function () {
    var params = {};
    new FormData(saveBtn.closest("form")).forEach(function (v, k) {
      if (String(v).trim() !== "") params[k] = String(v);
    });
    if (!params.q && !params.org && !params.src && !params.region) {
      alert("저장할 검색 조건(키워드·기관 등)을 먼저 입력해주세요.");
      return;
    }
    var label = prompt("빠른 검색 이름을 입력하세요", params.q || "");
    if (!label || !label.trim()) return;
    fetch("/api/searches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: page, label: label.trim(), params: params }),
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { alert(d.error || "저장에 실패했습니다."); return; }
      load();
    }).catch(function () { alert("네트워크 오류 — 다시 시도해주세요."); });
  });

  load();
})();
</script>"""


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
    <span id="quick-user" data-page="bid" class="quick-slot"></span>
    <button type="button" id="quick-save" class="chip chip-save"
            title="현재 검색 조건을 빠른 검색으로 저장">{icon("plus", 13)} 저장</button>
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
</form>""" + QUICK_JS
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
    <span id="quick-user" data-page="gov" class="quick-slot"></span>
    <button type="button" id="quick-save" class="chip chip-save"
            title="현재 검색 조건을 빠른 검색으로 저장">{icon("plus", 13)} 저장</button>
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
</form>""" + QUICK_JS


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
    sub_chip = (f'<span class="area-chip" style="background:var(--fill);color:var(--sub)">'
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
        '<span><span class="dot" style="background:#3182f6"></span>업무</span>'
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
</script>
<div class="card">
  <p style="margin:4px 0 6px"><b>PDF 편집</b> — 페이지를 눈으로 보면서 클릭으로 고르고,
  여러 PDF를 합치고, 드래그로 순서를 바꿉니다. 파일은 서버로 전송되지 않습니다.</p>
  <div class="row" style="margin-top:12px">
    <input type="file" id="edit-file" accept="application/pdf,.pdf" multiple style="display:none">
    <button type="button" id="edit-add">+ PDF 파일 추가</button>
    <span id="edit-count" class="meta" style="margin:0"></span>
  </div>
  <div class="row" id="edit-tools" hidden>
    <button type="button" id="edit-save-sel" class="chip chip-save">선택만 저장</button>
    <button type="button" id="edit-del-sel" class="chip chip-save">선택 삭제 후 저장</button>
    <button type="button" id="edit-rotate" class="chip chip-save">선택 회전 90°</button>
    <button type="button" id="edit-save-all" class="chip chip-save">전체 저장 (병합)</button>
    <button type="button" id="edit-split-zip" class="chip chip-save">한 페이지씩 분할 (zip)</button>
  </div>
  <div id="edit-grid" class="pdf-grid"></div>
  <p class="meta" id="edit-hint" style="margin-top:10px">페이지 클릭 = 선택 / 드래그 = 순서 이동 ·
  파일을 여러 개 추가하면 이어 붙어서 [전체 저장]으로 병합됩니다.</p>
  <div id="edit-status"></div>
  <div id="edit-result"></div>
</div>
<div id="viewer" hidden>
  <div class="viewer-top">
    <span id="viewer-num">- / -</span>
    <span class="viewer-tools">
      <button type="button" id="viewer-zout" title="축소">−</button>
      <span id="viewer-zoom">100%</span>
      <button type="button" id="viewer-zin" title="확대">+</button>
      <button type="button" id="viewer-close" title="닫기 (ESC)">✕</button>
    </span>
  </div>
  <div class="viewer-body" id="viewer-body"><canvas id="viewer-canvas"></canvas></div>
  <button type="button" id="viewer-prev" class="viewer-nav" title="이전 (←)">‹</button>
  <button type="button" id="viewer-next" class="viewer-nav" title="다음 (→)">›</button>
</div>
<style>
  #viewer { position: fixed; inset: 0; z-index: 1000; background: rgba(25,31,40,0.93);
            display: flex; flex-direction: column; }
  #viewer[hidden] { display: none; }
  .viewer-top { display: flex; justify-content: space-between; align-items: center;
                padding: 12px 18px; color: #fff; font-size: 0.9rem; flex-shrink: 0; }
  .viewer-tools { display: inline-flex; align-items: center; gap: 8px; }
  .viewer-top button { background: rgba(255,255,255,0.14); border: none; color: #fff;
                       border-radius: 8px; padding: 6px 13px; font-size: 0.95rem;
                       cursor: pointer; line-height: 1; }
  .viewer-top button:hover { background: rgba(255,255,255,0.28); }
  #viewer-zoom { min-width: 46px; text-align: center; color: rgba(255,255,255,0.8); }
  .viewer-body { flex: 1; overflow: auto; display: flex; padding: 0 64px 24px; }
  #viewer-canvas { margin: auto; background: #fff; border-radius: 4px;
                   box-shadow: 0 8px 40px rgba(0,0,0,0.5); }
  .viewer-nav { position: fixed; top: 50%; transform: translateY(-50%); z-index: 1001;
                background: rgba(255,255,255,0.14); color: #fff; border: none;
                width: 44px; height: 68px; font-size: 1.7rem; border-radius: 10px;
                cursor: pointer; padding: 0; }
  .viewer-nav:hover { background: rgba(255,255,255,0.3); }
  #viewer-prev { left: 10px; }
  #viewer-next { right: 10px; }
  .zoom-btn { position: absolute; top: 10px; right: 10px; border: none; cursor: pointer;
              background: rgba(25,31,40,0.55); color: #fff; border-radius: 6px;
              width: 24px; height: 22px; padding: 0; display: flex;
              align-items: center; justify-content: center; opacity: 0; transition: opacity 0.12s; }
  .pdf-tile:hover .zoom-btn { opacity: 1; }
  .zoom-btn:hover { background: var(--accent); }
  .pdf-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
              gap: 10px; margin-top: 14px; }
  .pdf-tile { position: relative; border: 2px solid var(--line); border-radius: 10px;
              padding: 6px 6px 4px; cursor: pointer; background: var(--card); user-select: none; }
  .pdf-tile.sel { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(49,130,246,0.15); }
  .pdf-tile.drag-over { border-style: dashed; border-color: var(--accent); }
  .pdf-tile canvas { width: 100%; display: block; border-radius: 6px; background: #fff;
                     min-height: 80px; }
  .pdf-tile .pn { position: absolute; top: 10px; left: 10px; background: rgba(25,31,40,0.65);
                  color: #fff; font-size: 0.7rem; font-weight: 700; border-radius: 6px;
                  padding: 1px 7px; }
  .pdf-tile.sel .pn { background: var(--accent); }
  .pdf-tile .tag { display: flex; align-items: center; gap: 5px; margin-top: 5px;
                   font-size: 0.68rem; color: var(--muted); overflow: hidden;
                   white-space: nowrap; text-overflow: ellipsis; }
  .pdf-tile .tag i { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
</style>
<script src="https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/fflate@0.8.2/umd/index.js"></script>
<script>
(function () {
  if (window.pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";
  }
  var DOC_COLORS = ["#3182f6", "#05a06d", "#e8720c", "#8345d6", "#d6479c", "#0c8599"];
  var docs = [];   // {name, lib, js, color}
  var pages = [];  // {doc, page, rot, sel}
  var grid = document.getElementById("edit-grid");
  var statusEl = document.getElementById("edit-status");
  var resultEl = document.getElementById("edit-result");
  var tools = document.getElementById("edit-tools");
  var countEl = document.getElementById("edit-count");
  var fileEl = document.getElementById("edit-file");
  var busy = false;

  function setStatus(msg, isError) {
    statusEl.innerHTML = msg
      ? '<p class="' + (isError ? "error" : "meta") + '">' + msg + "</p>" : "";
  }
  function showDownload(blob, filename, label) {
    resultEl.innerHTML = '<div class="ai-sum" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">'
      + "<span>" + label + "</span>"
      + '<a class="btn-outline dl" style="padding:7px 16px">내려받기</a></div>';
    var a = resultEl.querySelector("a.dl");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
  }
  function updateCount() {
    var sel = pages.filter(function (p) { return p.sel; }).length;
    countEl.textContent = pages.length
      ? "총 " + pages.length + "페이지" + (sel ? " · " + sel + "장 선택됨" : "") : "";
    tools.hidden = !pages.length;
  }

  // ---------- 썸네일 렌더 (보일 때만)
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      observer.unobserve(en.target);
      renderThumb(en.target);
    });
  }, { rootMargin: "200px" });

  function renderThumb(tile) {
    var en = tile._page;
    if (!en) return;
    var key = en.doc + ":" + en.page + ":" + en.rot;
    if (tile._rendered === key) return;
    tile._rendered = key;
    docs[en.doc].js.getPage(en.page + 1).then(function (p) {
      var base = p.getViewport({ scale: 1 });
      var vp = p.getViewport({ scale: 280 / base.width,   // 표시폭 140px의 2배 (레티나)
                               rotation: (base.rotation + en.rot) % 360 });
      var canvas = tile.querySelector("canvas");
      canvas.width = vp.width;
      canvas.height = vp.height;
      return p.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    }).catch(function () {});
  }

  function makeTile(en) {
    var tile = document.createElement("div");
    tile.className = "pdf-tile";
    tile.draggable = true;
    tile._page = en;
    var canvas = document.createElement("canvas");
    tile.appendChild(canvas);
    var pn = document.createElement("span");
    pn.className = "pn";
    tile.appendChild(pn);
    var tag = document.createElement("span");
    tag.className = "tag";
    var dot = document.createElement("i");
    dot.style.background = docs[en.doc].color;
    tag.appendChild(dot);
    tag.appendChild(document.createTextNode(docs[en.doc].name));
    tile.appendChild(tag);
    var zb = document.createElement("button");
    zb.type = "button";
    zb.className = "zoom-btn";
    zb.title = "크게 보기 (더블클릭도 가능)";
    zb.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" '
      + 'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
      + 'stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>';
    zb.addEventListener("click", function (ev) {
      ev.stopPropagation();
      openViewer(pages.indexOf(en));
    });
    tile.appendChild(zb);
    tile.addEventListener("dblclick", function () { openViewer(pages.indexOf(en)); });
    tile.addEventListener("click", function () {
      en.sel = !en.sel;
      tile.classList.toggle("sel", en.sel);
      updateCount();
    });
    tile.addEventListener("dragstart", function (ev) {
      ev.dataTransfer.setData("text/plain", String(pages.indexOf(en)));
      ev.dataTransfer.effectAllowed = "move";
    });
    tile.addEventListener("dragover", function (ev) {
      ev.preventDefault();
      tile.classList.add("drag-over");
    });
    tile.addEventListener("dragleave", function () { tile.classList.remove("drag-over"); });
    tile.addEventListener("drop", function (ev) {
      ev.preventDefault();
      tile.classList.remove("drag-over");
      var from = +ev.dataTransfer.getData("text/plain");
      var to = pages.indexOf(en);
      if (isNaN(from) || from === to) return;
      var moved = pages.splice(from, 1)[0];
      pages.splice(to, 0, moved);
      renderGrid();
    });
    return tile;
  }

  function renderGrid() {
    grid.innerHTML = "";
    pages.forEach(function (en, i) {
      var tile = makeTile(en);
      tile.classList.toggle("sel", !!en.sel);
      tile.querySelector(".pn").textContent = i + 1;
      grid.appendChild(tile);
      observer.observe(tile);
    });
    updateCount();
  }

  // ---------- 파일 추가
  document.getElementById("edit-add").addEventListener("click", function () {
    fileEl.click();
  });
  fileEl.addEventListener("change", function () {
    var files = Array.prototype.slice.call(fileEl.files || []);
    fileEl.value = "";
    if (!files.length) return;
    setStatus("PDF 읽는 중…");
    resultEl.innerHTML = "";
    var chain = Promise.resolve();
    files.forEach(function (file) {
      chain = chain.then(function () {
        return file.arrayBuffer().then(function (buf) {
          var bytes = new Uint8Array(buf);
          return PDFLib.PDFDocument.load(bytes).then(function (lib) {
            // pdf.js는 버퍼를 가져가므로 복사본 전달
            return pdfjsLib.getDocument({ data: bytes.slice() }).promise.then(function (js) {
              var idx = docs.length;
              docs.push({ name: file.name.replace(/\.pdf$/i, ""), lib: lib, js: js,
                          color: DOC_COLORS[idx % DOC_COLORS.length] });
              for (var i = 0; i < lib.getPageCount(); i++) {
                pages.push({ doc: idx, page: i, rot: 0, sel: false });
              }
            });
          });
        }).catch(function (err) {
          setStatus(/encrypt/i.test(String(err))
            ? '"' + file.name + '" — 암호가 걸린 PDF입니다. 암호를 해제한 뒤 사용해주세요.'
            : '"' + file.name + '" 읽기 실패: ' + err, true);
          throw err;
        });
      });
    });
    chain.then(function () { setStatus(""); renderGrid(); }).catch(function () { renderGrid(); });
  });

  // ---------- 저장 (pdf-lib로 조립)
  function buildPdf(list, onProgress) {
    return PDFLib.PDFDocument.create().then(function (out) {
      var chain = Promise.resolve();
      list.forEach(function (en, i) {
        chain = chain.then(function () {
          if (onProgress) onProgress(i + 1, list.length);
          return out.copyPages(docs[en.doc].lib, [en.page]).then(function (c) {
            var p = c[0];
            if (en.rot) {
              p.setRotation(PDFLib.degrees((p.getRotation().angle + en.rot) % 360));
            }
            out.addPage(p);
          });
        });
      });
      return chain.then(function () { return out.save(); });
    });
  }
  function baseName() {
    return docs.length === 1 ? docs[0].name : docs[0].name + "_외" + (docs.length - 1) + "건";
  }
  function saveList(list, filename, label) {
    if (!list.length) { alert("페이지를 먼저 선택해주세요."); return; }
    if (busy) return;
    busy = true;
    buildPdf(list, function (k, n) { setStatus(k + " / " + n + " 페이지 처리 중…"); })
      .then(function (bytes) {
        busy = false;
        setStatus("");
        showDownload(new Blob([bytes], { type: "application/pdf" }), filename,
          "<b>" + list.length + "페이지</b> PDF 저장 완료");
      }).catch(function (err) { busy = false; setStatus("저장 실패: " + err, true); });
  }

  document.getElementById("edit-save-sel").addEventListener("click", function () {
    saveList(pages.filter(function (p) { return p.sel; }), baseName() + "_선택.pdf");
  });
  document.getElementById("edit-del-sel").addEventListener("click", function () {
    var keep = pages.filter(function (p) { return !p.sel; });
    if (keep.length === pages.length) { alert("삭제할 페이지를 먼저 선택해주세요."); return; }
    saveList(keep, baseName() + "_편집.pdf");
  });
  document.getElementById("edit-save-all").addEventListener("click", function () {
    saveList(pages.slice(), docs.length > 1 ? baseName() + "_병합.pdf" : baseName() + "_편집.pdf");
  });
  document.getElementById("edit-rotate").addEventListener("click", function () {
    var sel = pages.filter(function (p) { return p.sel; });
    if (!sel.length) { alert("회전할 페이지를 먼저 선택해주세요."); return; }
    sel.forEach(function (p) { p.rot = (p.rot + 90) % 360; });
    renderGrid();
  });
  document.getElementById("edit-split-zip").addEventListener("click", function () {
    if (!pages.length || busy) return;
    busy = true;
    var files = {};
    var pad = String(pages.length).length;
    var i = 0;
    function next() {
      if (i >= pages.length) {
        setStatus("zip 압축 중…");
        var zipped = fflate.zipSync(files, { level: 0 });
        busy = false;
        setStatus("");
        showDownload(new Blob([zipped], { type: "application/zip" }),
          baseName() + "_분할.zip", "<b>" + pages.length + "개</b> PDF로 분할 완료");
        return;
      }
      setStatus((i + 1) + " / " + pages.length + " 페이지 분할 중…");
      buildPdf([pages[i]]).then(function (bytes) {
        var num = String(i + 1);
        while (num.length < pad) num = "0" + num;
        files[baseName() + "_" + num + ".pdf"] = new Uint8Array(bytes);
        i++;
        setTimeout(next, 0);
      }).catch(function (err) { busy = false; setStatus("분할 실패: " + err, true); });
    }
    next();
  });

  // ---------- 뷰어 (크게 보기)
  var viewer = document.getElementById("viewer");
  var viewerBody = document.getElementById("viewer-body");
  var viewerCanvas = document.getElementById("viewer-canvas");
  var vIdx = 0, vZoom = 1, vToken = 0;

  function openViewer(i) {
    if (i < 0 || i >= pages.length) return;
    vIdx = i;
    vZoom = 1;
    viewer.hidden = false;
    renderView();
  }
  function closeViewer() { viewer.hidden = true; }

  function renderView() {
    var en = pages[vIdx];
    if (!en) return;
    document.getElementById("viewer-num").textContent = (vIdx + 1) + " / " + pages.length;
    document.getElementById("viewer-zoom").textContent = Math.round(vZoom * 100) + "%";
    var token = ++vToken;
    docs[en.doc].js.getPage(en.page + 1).then(function (p) {
      var base = p.getViewport({ scale: 1 });
      var rot = (base.rotation + en.rot) % 360;
      var shape = p.getViewport({ scale: 1, rotation: rot });
      var fit = Math.min((viewerBody.clientWidth - 40) / shape.width,
                         (viewerBody.clientHeight - 24) / shape.height);
      var dpr = window.devicePixelRatio || 1;
      var vp = p.getViewport({ scale: Math.max(fit, 0.1) * vZoom * dpr, rotation: rot });
      if (token !== vToken) return;
      viewerCanvas.width = vp.width;
      viewerCanvas.height = vp.height;
      viewerCanvas.style.width = (vp.width / dpr) + "px";
      viewerCanvas.style.height = (vp.height / dpr) + "px";
      return p.render({ canvasContext: viewerCanvas.getContext("2d"), viewport: vp }).promise;
    }).catch(function () {});
  }

  function viewerStep(delta) {
    if (!pages.length) return;
    vIdx = (vIdx + delta + pages.length) % pages.length;
    renderView();
  }
  function viewerZoom(delta) {
    vZoom = Math.min(3, Math.max(0.5, vZoom + delta));
    renderView();
  }
  document.getElementById("viewer-prev").addEventListener("click", function () { viewerStep(-1); });
  document.getElementById("viewer-next").addEventListener("click", function () { viewerStep(1); });
  document.getElementById("viewer-zin").addEventListener("click", function () { viewerZoom(0.25); });
  document.getElementById("viewer-zout").addEventListener("click", function () { viewerZoom(-0.25); });
  document.getElementById("viewer-close").addEventListener("click", closeViewer);
  viewerBody.addEventListener("click", function (e) {
    if (e.target === viewerBody) closeViewer();  // 배경 클릭 = 닫기
  });
  document.addEventListener("keydown", function (e) {
    if (viewer.hidden) return;
    if (e.key === "Escape") closeViewer();
    else if (e.key === "ArrowLeft") viewerStep(-1);
    else if (e.key === "ArrowRight") viewerStep(1);
    else if (e.key === "+" || e.key === "=") viewerZoom(0.25);
    else if (e.key === "-") viewerZoom(-0.25);
    else return;
    e.preventDefault();
  });
})();
</script>"""


READER_DIR = Path(__file__).resolve().parent.parent / "static" / "reader"
READER_ASSETS = {
    "app.js": "application/javascript",
    "library.js": "application/javascript",
    "styles.css": "text/css",
}


@app.get("/reader", response_class=HTMLResponse)
def reader_page(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    user = user_of(request)
    if not auth.is_owner(user):
        return RedirectResponse("/bid", status_code=302)
    content = ('<iframe src="/reader/app" title="이북 리더" '
               'style="width:100%;height:calc(100vh - 150px);min-height:620px;'
               'border:1px solid var(--line);border-radius:12px;background:#fff"></iframe>')
    return layout("이북 리더", icon("book-open", 20) + " 이북 리더", "/reader",
                  content, user=user, admin=auth.is_admin(user))


@app.get("/reader/app")
def reader_app(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    if not auth.is_owner(user_of(request)):
        return RedirectResponse("/bid", status_code=302)
    return FileResponse(READER_DIR / "index.html", media_type="text/html")


@app.get("/reader/{asset}")
def reader_asset(asset: str):
    if asset not in READER_ASSETS:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(READER_DIR / asset, media_type=READER_ASSETS[asset])


@app.post("/api/logs")
def reader_logs(body: dict = Body(...)):
    return {"ok": True}  # 리더 진단 로그는 수집하지 않음 (호환용 무동작 응답)


def _owner_user(request: Request) -> str | None:
    user = user_of(request)
    return user if (user and auth.is_owner(user)) else None


@app.post("/api/translations/jobs")
def reader_translation_job(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)
    job_id = str(body.get("jobId", "")).strip()[:100]
    paragraphs = body.get("paragraphs") or []
    if not job_id or not isinstance(paragraphs, list) or not paragraphs:
        return JSONResponse({"error": "잘못된 요청입니다."}, status_code=400)
    try:
        translated = summarize.gemini_translate_paragraphs(paragraphs[:300])
        result = {"schemaVersion": 1, "jobId": job_id, "paragraphs": translated}
        todos._request(
            "POST", "reader_jobs",
            json={"job_id": job_id, "email": user, "result": result},
            headers={"Prefer": "resolution=merge-duplicates"},
        )
    except (summarize.SummarizeError, store.StoreError) as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"jobFile": f"{job_id}.json", "resultFile": f"{job_id}.json"}


@app.get("/api/translations/results/{job_id}")
def reader_translation_result(request: Request, job_id: str):
    user = _owner_user(request)
    if not user:
        return JSONResponse({"error": "권한이 없습니다."}, status_code=403)
    try:
        rows = todos._request(
            "GET", "reader_jobs",
            params={"select": "result", "job_id": f"eq.{job_id}",
                    "email": f"eq.{user}"},
        ).json()
    except store.StoreError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not rows:
        return JSONResponse({"error": "not ready"}, status_code=404)
    return rows[0]["result"]


GENIE_PAGE = """<div class="genie-wrap">
  <aside class="card genie-side">
    <button type="button" id="genie-new" class="genie-new-btn">+ 새 대화</button>
    <div id="genie-list" class="genie-list"></div>
  </aside>
  <div class="card genie-main">
    <div id="genie-log" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:12px;padding:4px 2px">
      <div class="genie-msg ai">무엇이든 물어보세요! 🧞</div>
    </div>
    <form id="genie-form" class="row" style="margin-top:12px">
      <input type="text" id="genie-input" placeholder="질문 입력 후 Enter" autocomplete="off" maxlength="4000">
      <button type="submit" id="genie-send">보내기</button>
    </form>
    <div id="genie-usage" class="meta" style="margin:8px 2px 0">오늘 사용량 불러오는 중…</div>
  </div>
</div>
<style>
  .genie-wrap { display: flex; gap: 14px; height: calc(100vh - 170px); min-height: 520px; }
  .genie-side { width: 210px; flex-shrink: 0; display: flex; flex-direction: column;
                gap: 8px; overflow: hidden; }
  .genie-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .genie-new-btn { width: 100%; padding: 9px; border: none;
                   background: var(--accent-soft); color: var(--accent); border-radius: 10px;
                   cursor: pointer; font-size: 0.88rem; font-weight: 700; }
  .genie-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  .genie-item { display: flex; align-items: center; gap: 4px; padding: 7px 8px;
                border-radius: 8px; cursor: pointer; font-size: 0.85rem; color: var(--text); }
  .genie-item:hover { background: var(--fill); }
  .genie-item.active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .genie-item .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .genie-item .del { border: none; background: none; color: var(--muted); cursor: pointer;
                     padding: 2px 4px; border-radius: 4px; font-size: 0.85rem; line-height: 1; }
  .genie-item .del:hover { color: var(--red); background: var(--red-soft); }
  .genie-msg { max-width: 82%; padding: 10px 14px; border-radius: 14px;
               font-size: 0.93rem; white-space: pre-wrap; word-break: break-word; }
  .genie-msg.user { align-self: flex-end; background: var(--accent); color: #fff;
                    border-bottom-right-radius: 4px; }
  .genie-msg.ai { align-self: flex-start; background: var(--fill); color: var(--text);
                  border-bottom-left-radius: 4px; }
  .genie-msg.loading { color: var(--muted); }
  @media (max-width: 720px) {
    .genie-wrap { flex-direction: column; height: auto; }
    .genie-side { width: auto; max-height: 180px; }
    .genie-main { min-height: 480px; }
  }
</style>
<script>
(function () {
  var log = document.getElementById("genie-log");
  var form = document.getElementById("genie-form");
  var input = document.getElementById("genie-input");
  var sendBtn = document.getElementById("genie-send");
  var listEl = document.getElementById("genie-list");
  var newBtn = document.getElementById("genie-new");
  var chatId = null;
  var usageEl = document.getElementById("genie-usage");

  function renderUsage(u) {
    if (!u) return;
    var pct = u.limit ? Math.min(100, Math.round(100 * u.requests / u.limit)) : 0;
    usageEl.innerHTML = "오늘 Gemini 사용: <b>" + u.requests.toLocaleString() + "회</b> / "
      + u.limit.toLocaleString() + "회 한도 · 남음 <b>" + u.remaining.toLocaleString() + "회</b>"
      + " · 토큰 " + u.tokens.toLocaleString()
      + ' <span style="display:inline-block;width:90px;height:6px;background:var(--line);border-radius:3px;vertical-align:middle;margin-left:6px">'
      + '<span style="display:block;width:' + pct + '%;height:6px;border-radius:3px;background:'
      + (pct >= 90 ? "var(--red)" : pct >= 70 ? "#f5a623" : "var(--accent)") + '"></span></span>'
      + " (지니·AI요약·번역 합산, 자정 기준 근사치)";
  }
  fetch("/api/genie/usage").then(function (r) { return r.json(); })
    .then(function (d) { if (d.ok) renderUsage(d.usage); })
    .catch(function () {});

  function addMsg(text, cls) {
    var div = document.createElement("div");
    div.className = "genie-msg " + cls;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function resetLog() {
    log.innerHTML = "";
    addMsg("무엇이든 물어보세요! 🧞", "ai");
  }

  function markActive() {
    var items = listEl.querySelectorAll(".genie-item");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("active", items[i].dataset.id === String(chatId));
    }
  }

  function loadChats() {
    fetch("/api/genie/chats").then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        listEl.innerHTML = "";
        d.chats.forEach(function (c) {
          var row = document.createElement("div");
          row.className = "genie-item";
          row.dataset.id = String(c.id);
          var t = document.createElement("span");
          t.className = "t";
          t.textContent = c.title || "(제목 없음)";
          var del = document.createElement("button");
          del.type = "button";
          del.className = "del";
          del.textContent = "×";
          del.title = "대화 삭제";
          del.addEventListener("click", function (e) {
            e.stopPropagation();
            if (!confirm("이 대화를 삭제할까요?")) return;
            fetch("/api/genie/chats/" + c.id + "/delete", { method: "POST" })
              .then(function () {
                if (String(chatId) === String(c.id)) { chatId = null; resetLog(); }
                loadChats();
              });
          });
          row.appendChild(t);
          row.appendChild(del);
          row.addEventListener("click", function () { openChat(c.id); });
          listEl.appendChild(row);
        });
        markActive();
      }).catch(function () {});
  }

  function openChat(id) {
    fetch("/api/genie/chats/" + id).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { alert(d.error || "대화를 불러오지 못했습니다."); return; }
        chatId = d.chat.id;
        log.innerHTML = "";
        (d.chat.messages || []).forEach(function (m) {
          addMsg(m.text, m.role === "user" ? "user" : "ai");
        });
        markActive();
        input.focus();
      }).catch(function () {});
  }

  newBtn.addEventListener("click", function () {
    chatId = null;
    resetLog();
    markActive();
    input.focus();
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q || sendBtn.disabled) return;
    input.value = "";
    addMsg(q, "user");
    var wait = addMsg("생각 중…", "ai loading");
    sendBtn.disabled = true;
    fetch("/api/genie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: q }),
    }).then(function (r) { return r.json(); }).then(function (d) {
      sendBtn.disabled = false;
      if (!d.ok) { wait.textContent = "오류: " + (d.error || "실패"); return; }
      wait.classList.remove("loading");
      wait.textContent = d.reply;
      renderUsage(d.usage);
      var isNew = !chatId;
      if (d.chat_id) chatId = d.chat_id;
      if (isNew) loadChats(); else markActive();
      log.scrollTop = log.scrollHeight;
      input.focus();
    }).catch(function () {
      sendBtn.disabled = false;
      wait.textContent = "네트워크 오류 — 다시 시도해주세요.";
    });
  });
  loadChats();
  input.focus();
})();
</script>"""


@app.get("/genie", response_class=HTMLResponse)
def genie_page(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    user = user_of(request)
    if not auth.is_owner(user):
        return RedirectResponse("/bid", status_code=302)
    return layout("지니", icon("sparkles", 20) + " 지니", "/genie",
                  GENIE_PAGE, user=user, admin=auth.is_admin(user))


@app.post("/api/genie")
def genie_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    text = str(body.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "질문이 비어 있습니다."}
    chat_id = body.get("chat_id")
    email = user
    messages: list = []
    if chat_id:
        try:
            messages = genie.get_chat(email, int(chat_id)).get("messages") or []
        except (store.StoreError, ValueError) as e:
            return {"ok": False, "error": str(e)}
    messages.append({"role": "user", "text": text[:8000]})
    try:
        reply = summarize.gemini_chat(messages)
    except summarize.SummarizeError as e:
        return {"ok": False, "error": str(e)}
    messages.append({"role": "model", "text": reply})
    try:  # 저장에 실패해도 답변은 표시한다
        if chat_id:
            genie.save_messages(email, int(chat_id), messages)
        elif genie.enabled():
            chat_id = genie.create_chat(email, text, messages)
    except store.StoreError:
        pass
    return {"ok": True, "reply": reply, "chat_id": chat_id,
            "usage": summarize.usage_today()}


@app.get("/api/genie/chats")
def genie_chats_api(request: Request):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        return {"ok": True, "chats": genie.list_chats(user)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/genie/chats/{chat_id}")
def genie_chat_detail(request: Request, chat_id: int):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        return {"ok": True, "chat": genie.get_chat(user, chat_id)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/genie/chats/{chat_id}/delete")
def genie_chat_delete(request: Request, chat_id: int):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        genie.delete_chat(user, chat_id)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------- 스피킹 (영어 회화 학습)

@app.post("/api/english/chat")
def english_chat_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    text = str(body.get("text") or "").strip()
    session_id = body.get("session_id")
    try:
        if session_id:
            sess = english.get_session(user, int(session_id))
        else:
            sess = english.create_session(user, str(body.get("scenario") or ""))
            session_id = sess["id"]
        messages = sess.get("messages") or []
        if text:
            messages.append({"role": "user", "text": text[:2000]})
        persona = english.SCENARIOS[sess["scenario"]]["persona"]
        reply = summarize.gemini_english_chat(persona, messages)
        messages.append({"role": "model", "text": reply})
        english.save_session(user, int(session_id), messages=messages)
        return {"ok": True, "session_id": session_id, "reply": reply,
                "usage": summarize.usage_today()}
    except (store.StoreError, summarize.SummarizeError, ValueError) as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/finish")
def english_finish_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        sess = english.get_session(user, int(body.get("session_id") or 0))
        messages = sess.get("messages") or []
        if not any(m.get("role") == "user" for m in messages):
            return {"ok": False, "error": "아직 대화가 없습니다. 한 마디라도 해보세요!"}
        feedback = summarize.gemini_english_feedback(messages)
        english.save_session(user, sess["id"], feedback=feedback)
        return {"ok": True, "feedback": feedback}
    except (store.StoreError, summarize.SummarizeError, ValueError) as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/english/home")
def english_home_api(request: Request):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        rows = english.recent_sessions(user)
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}
    week_ago = datetime.now(KST) - timedelta(days=7)
    week = 0
    for r in rows:
        try:
            if datetime.fromisoformat(r["created_at"]) >= week_ago:
                week += 1
        except (ValueError, TypeError):
            continue
    reco = english.recommend_scenario([r["scenario"] for r in rows])
    sc = english.SCENARIOS[reco]
    recent = [{"id": r["id"], "scenario": r["scenario"],
               "title": english.SCENARIOS.get(r["scenario"], {}).get("title", r["scenario"]),
               "level": r["level"], "date": str(r["created_at"])[:10],
               "has_feedback": bool(r.get("feedback"))}
              for r in rows[:8]]
    return {"ok": True,
            "streak": english.compute_streak([r["created_at"] for r in rows]),
            "week": week,
            "recommend": {"key": reco, "title": sc["title"], "desc": sc["desc"],
                          "level": sc["level"]},
            "recent": recent}


@app.get("/api/english/sessions/{session_id}")
def english_session_api(request: Request, session_id: int):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        return {"ok": True, "session": english.get_session(user, session_id)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/polish")
def english_polish_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    text = str(body.get("text") or "").strip()
    if len(text) < 20:
        return {"ok": False, "error": "초안을 조금 더 길게 써주세요 (20자 이상)."}
    try:
        return {"ok": True, **summarize.gemini_polish_script(text),
                "usage": summarize.usage_today()}
    except summarize.SummarizeError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/english/scripts")
def english_scripts_api(request: Request):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        return {"ok": True, "items": english.list_scripts(user)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/scripts")
def english_script_add_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    sentences = body.get("sentences") or []
    if not isinstance(sentences, list) or not sentences:
        return {"ok": False, "error": "저장할 문장이 없습니다."}
    try:
        item = english.save_script(user, str(body.get("title") or ""),
                                   str(body.get("source") or ""), sentences)
        return {"ok": True, "item": item}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/scripts/{script_id}/delete")
def english_script_delete_api(request: Request, script_id: int):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        english.delete_script(user, script_id)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/english/cards")
def english_cards_api(request: Request, due: str = Query("")):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        return {"ok": True, "items": english.list_cards(user, due_only=due == "1")}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/cards")
def english_card_add_api(request: Request, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        item = english.add_card(user, str(body.get("front") or ""),
                                str(body.get("back") or ""),
                                str(body.get("note") or ""))
        return {"ok": True, "item": item}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/cards/{card_id}/review")
def english_card_review_api(request: Request, card_id: int, body: dict = Body(...)):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        result = english.review_card(user, card_id, bool(body.get("ok")))
        return {"ok": True, **result}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/english/cards/{card_id}/delete")
def english_card_delete_api(request: Request, card_id: int):
    user = _owner_user(request)
    if not user:
        return {"ok": False, "error": "권한이 없습니다."}
    try:
        english.delete_card(user, card_id)
        return {"ok": True}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


ENGLISH_PAGE_TMPL = """<div class="seg eng-seg" id="eng-tabs">
  <a href="#" data-tab="home" class="on">오늘의 연습</a>
  <a href="#" data-tab="talk">롤플레이</a>
  <a href="#" data-tab="pitch">내 피치</a>
  <a href="#" data-tab="cards">표현 카드</a>
</div>

<div id="pane-home" class="eng-pane">
  <div class="eng-stats">
    <div class="card eng-stat"><div class="n" id="eng-streak">-</div><div class="l">연속 학습일</div></div>
    <div class="card eng-stat"><div class="n" id="eng-week">-</div><div class="l">이번 주 연습</div></div>
  </div>
  <div class="card" id="eng-reco"><div class="meta">오늘의 추천 불러오는 중…</div></div>
  <div class="card"><h3 class="eng-h">최근 연습</h3><div id="eng-recent" class="meta">기록이 없습니다.</div></div>
</div>

<div id="pane-talk" class="eng-pane" hidden>
  <div id="talk-pick">
    <div class="meta">시나리오를 고르면 AI가 역할을 맡아 영어로 먼저 말을 겁니다. 🎤 버튼으로 말하거나 텍스트로 답하세요.</div>
    __SCENARIO_CARDS__
  </div>
  <div id="talk-chat" hidden>
    <div class="card talk-card">
      <div class="row talk-head">
        <b id="talk-title"></b>
        <span class="talk-tools">
          <label class="meta"><input type="checkbox" id="talk-tts" checked> AI 음성 읽기</label>
          <button type="button" id="talk-finish" class="chip chip-save">연습 종료 · 피드백 받기</button>
        </span>
      </div>
      <div id="talk-goals" class="meta"></div>
      <div id="talk-log" class="talk-log"></div>
      <form id="talk-form" class="row">
        <button type="button" id="talk-mic" class="mic-btn" title="누르고 영어로 말하세요">MIC</button>
        <input type="text" id="talk-input" placeholder="영어로 입력 후 Enter (또는 마이크)" autocomplete="off" maxlength="2000">
        <button type="submit" id="talk-send">보내기</button>
      </form>
      <div id="talk-hint" class="meta"></div>
    </div>
  </div>
  <div id="talk-fb" hidden></div>
</div>

<div id="pane-pitch" class="eng-pane" hidden>
  <div class="card">
    <h3 class="eng-h">새 피치 스크립트</h3>
    <div class="meta">한국어(또는 영어 초안)로 쓰면 AI가 말하기 좋은 비즈니스 영어 문장으로 다듬어줍니다.</div>
    <textarea id="pitch-src" rows="5" placeholder="예: 안녕하세요, 저희는 AI 기반 콘텐츠 제작 서비스를 만드는 회사입니다. 주요 고객은..."></textarea>
    <div class="row"><button type="button" id="pitch-polish">영어로 다듬기</button><span id="pitch-status" class="meta"></span></div>
    <div id="pitch-preview" hidden></div>
  </div>
  <div class="card"><h3 class="eng-h">내 스크립트</h3><div id="pitch-list" class="meta">불러오는 중…</div></div>
  <div id="pitch-practice"></div>
</div>

<div id="pane-cards" class="eng-pane" hidden>
  <div class="card"><h3 class="eng-h">오늘의 복습</h3><div id="card-review"><div class="meta">불러오는 중…</div></div></div>
  <div class="card">
    <h3 class="eng-h">카드 추가</h3>
    <div class="row"><input type="text" id="card-front" placeholder="앞면 — 상황·뜻 (예: 가격 문의에 답할 때)" maxlength="300"></div>
    <div class="row"><input type="text" id="card-back" placeholder="뒷면 — 영어 표현 (예: Our pricing starts at $99 a month.)" maxlength="300"></div>
    <div class="row"><input type="text" id="card-note" placeholder="메모 (선택)" maxlength="300"><button type="button" id="card-add">추가</button></div>
  </div>
  <div class="card"><h3 class="eng-h">전체 카드</h3><div id="card-list" class="meta">불러오는 중…</div></div>
</div>

<style>
  .eng-seg { margin-bottom: 14px; }
  .eng-h { margin: 0 0 8px; font-size: 1rem; }
  .eng-pane .card { margin-bottom: 14px; }
  .eng-stats { display: flex; gap: 12px; }
  .eng-stat { flex: 1; text-align: center; }
  .eng-stat .n { font-size: 1.7rem; font-weight: 800; color: var(--accent); }
  .eng-stat .l { color: var(--muted); font-size: 0.82rem; }
  .scn-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
  .scn { text-align: left; background: var(--fill); border: 1px solid transparent; border-radius: 12px;
         padding: 12px 14px; cursor: pointer; color: var(--text); font-weight: 400; }
  .scn:hover { border-color: var(--accent); background: var(--accent-soft); }
  .scn b { display: block; margin-bottom: 4px; }
  .scn span { color: var(--muted); font-size: 0.82rem; }
  .talk-card { display: flex; flex-direction: column; height: calc(100vh - 240px); min-height: 440px; }
  .talk-head { justify-content: space-between; margin-bottom: 6px; }
  .talk-tools { display: inline-flex; align-items: center; gap: 10px; }
  .talk-log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding: 4px 2px; }
  .mic-btn { background: var(--card); color: var(--accent); border: 2px solid var(--accent);
             border-radius: 999px; width: 46px; height: 42px; padding: 0; font-size: 0.7rem; font-weight: 800; }
  .mic-btn.rec { background: var(--red); border-color: var(--red); color: #fff; animation: engpulse 1s infinite; }
  @keyframes engpulse { 50% { opacity: 0.6; } }
  .genie-msg { max-width: 82%; padding: 10px 14px; border-radius: 14px;
               font-size: 0.93rem; white-space: pre-wrap; word-break: break-word; }
  .genie-msg.user { align-self: flex-end; background: var(--accent); color: #fff;
                    border-bottom-right-radius: 4px; }
  .genie-msg.ai { align-self: flex-start; background: var(--fill); color: var(--text);
                  border-bottom-left-radius: 4px; }
  .genie-msg.loading { color: var(--muted); }
  .genie-msg.said-check b { font-weight: 800; }
  .fb-sec { margin: 10px 0; }
  .fb-sec h4 { margin: 0 0 6px; font-size: 0.92rem; }
  .fb-item { background: var(--fill); border-radius: 8px; padding: 9px 12px; margin-bottom: 6px; font-size: 0.88rem; }
  .fb-item .orig { color: var(--red); text-decoration: line-through; }
  .fb-item .fixed { color: var(--green); font-weight: 700; }
  .fb-item .why { color: var(--muted); font-size: 0.82rem; }
  .sent-row { display: flex; align-items: flex-start; gap: 8px; padding: 9px 4px; border-bottom: 1px solid #f4f5f7; }
  .sent-row .txt { flex: 1; }
  .sent-row .en { font-weight: 600; }
  .sent-row .ko { color: var(--muted); font-size: 0.82rem; }
  .sent-row .res { font-size: 0.82rem; margin-top: 3px; }
  .sent-btn { background: var(--accent-soft); color: var(--accent); border: none;
              border-radius: 8px; padding: 5px 9px; font-size: 0.78rem; font-weight: 700; flex-shrink: 0; }
  .sent-btn.rec { background: var(--red); color: #fff; }
  .flash { text-align: center; padding: 18px 10px; }
  .flash .front { font-size: 1.05rem; font-weight: 700; margin-bottom: 12px; }
  .flash .back { font-size: 1.1rem; color: var(--accent); font-weight: 800; margin: 10px 0; }
  .flash .note { color: var(--muted); font-size: 0.85rem; }
  .miss { color: var(--red); font-weight: 800; }
  .hit { color: var(--green); }
  textarea { width: 100%; border: 1px solid transparent; background: var(--fill); border-radius: 10px;
             padding: 10px 12px; font: inherit; margin-bottom: 8px; box-sizing: border-box; }
  .card-row { display: flex; align-items: center; gap: 8px; padding: 7px 2px; border-bottom: 1px solid #f4f5f7; font-size: 0.88rem; }
  .card-row .box-chip { background: var(--accent-soft); color: var(--accent); border-radius: 6px; padding: 2px 7px; font-size: 0.75rem; font-weight: 700; flex-shrink: 0; }
  .card-row .fb { flex: 1; min-width: 0; }
  .link-btn { border: none; background: none; color: var(--muted); cursor: pointer; padding: 2px 6px; }
  .link-btn:hover { color: var(--red); }
  @media (max-width: 720px) { .talk-card { height: auto; } .talk-log { height: 50vh; } }
</style>
<script>
var SCN = __SCENARIO_JSON__;
(function () {
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body || {}) }).then(function (r) { return r.json(); });
  }

  // ---------- 음성 (TTS / STT)
  function speak(text) {
    if (!window.speechSynthesis) return;
    speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    u.rate = 0.95;
    var vs = speechSynthesis.getVoices().filter(function (v) { return v.lang.indexOf("en") === 0; });
    if (vs.length) u.voice = vs[0];
    speechSynthesis.speak(u);
  }
  var Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  function listen(onResult, btn) {
    if (!Rec) { alert("이 브라우저는 음성 인식을 지원하지 않습니다. 크롬을 사용해주세요."); return; }
    var r = new Rec();
    r.lang = "en-US";
    r.interimResults = false;
    r.maxAlternatives = 1;
    btn.classList.add("rec");
    r.onresult = function (e) { onResult(e.results[0][0].transcript); };
    r.onend = function () { btn.classList.remove("rec"); };
    r.onerror = function () { btn.classList.remove("rec"); };
    r.start();
  }

  // ---------- 탭 전환
  var tabs = $("eng-tabs");
  tabs.addEventListener("click", function (e) {
    var a = e.target.closest("a[data-tab]");
    if (!a) return;
    e.preventDefault();
    tabs.querySelectorAll("a").forEach(function (x) { x.classList.toggle("on", x === a); });
    ["home", "talk", "pitch", "cards"].forEach(function (t) {
      $("pane-" + t).hidden = t !== a.dataset.tab;
    });
    if (a.dataset.tab === "home") loadHome();
    if (a.dataset.tab === "pitch") loadScripts();
    if (a.dataset.tab === "cards") loadCards();
  });
  function goTab(name) {
    tabs.querySelector('a[data-tab="' + name + '"]').click();
  }

  // ---------- 홈 (오늘의 연습)
  function loadHome() {
    fetch("/api/english/home").then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { $("eng-reco").innerHTML = '<div class="meta">' + (d.error || "오류") + "</div>"; return; }
      $("eng-streak").textContent = d.streak + "일";
      $("eng-week").textContent = d.week + "회";
      var reco = $("eng-reco");
      reco.innerHTML = "";
      reco.appendChild(el("h3", "eng-h", "오늘의 추천 연습"));
      var b = el("div", "meta", "Level " + d.recommend.level + " · " + d.recommend.desc);
      var title = el("div", "", "");
      var strong = el("b", "", d.recommend.title);
      title.appendChild(strong);
      reco.appendChild(title);
      reco.appendChild(b);
      var btn = el("button", "", "바로 시작");
      btn.type = "button";
      btn.style.marginTop = "8px";
      btn.addEventListener("click", function () { goTab("talk"); startScenario(d.recommend.key); });
      reco.appendChild(btn);
      var rc = $("eng-recent");
      rc.innerHTML = "";
      if (!d.recent.length) { rc.textContent = "아직 연습 기록이 없습니다. 오늘 첫 연습을 시작해보세요!"; return; }
      d.recent.forEach(function (s) {
        var row = el("div", "card-row");
        row.appendChild(el("span", "box-chip", "L" + s.level));
        row.appendChild(el("span", "fb", s.title + " · " + s.date));
        if (s.has_feedback) {
          var v = el("button", "sent-btn", "피드백 보기");
          v.type = "button";
          v.addEventListener("click", function () {
            fetch("/api/english/sessions/" + s.id).then(function (r) { return r.json(); })
              .then(function (d2) {
                if (!d2.ok || !d2.session.feedback) return;
                goTab("talk");
                $("talk-pick").hidden = true;
                $("talk-chat").hidden = true;
                showFeedback(d2.session.feedback);
              });
          });
          row.appendChild(v);
        }
        rc.appendChild(row);
      });
    }).catch(function () {});
  }

  // ---------- 롤플레이
  var sessionId = null;
  var talkLog = $("talk-log");
  function addTalk(text, cls) {
    var div = el("div", "genie-msg " + cls, text);
    talkLog.appendChild(div);
    talkLog.scrollTop = talkLog.scrollHeight;
    return div;
  }
  window.startScenario = startScenario;
  function startScenario(key) {
    var sc = SCN[key];
    if (!sc) return;
    sessionId = null;
    $("talk-pick").hidden = true;
    $("talk-fb").hidden = true;
    $("talk-chat").hidden = false;
    $("talk-title").textContent = "L" + sc.level + " · " + sc.title;
    $("talk-goals").textContent = "이런 표현을 써보세요: " + sc.goals.join("  |  ");
    talkLog.innerHTML = "";
    var wait = addTalk("상대가 말을 준비하고 있어요…", "ai loading");
    post("/api/english/chat", { scenario: key }).then(function (d) {
      if (!d.ok) { wait.textContent = "오류: " + (d.error || "실패"); return; }
      sessionId = d.session_id;
      wait.classList.remove("loading");
      wait.textContent = d.reply;
      if ($("talk-tts").checked) speak(d.reply);
      $("talk-input").focus();
    }).catch(function () { wait.textContent = "네트워크 오류"; });
  }
  document.querySelectorAll(".scn").forEach(function (b) {
    b.addEventListener("click", function () { startScenario(b.dataset.key); });
  });
  $("talk-form").addEventListener("submit", function (e) {
    e.preventDefault();
    sendTalk($("talk-input").value.trim());
  });
  function sendTalk(q) {
    if (!q || !sessionId || $("talk-send").disabled) return;
    $("talk-input").value = "";
    addTalk(q, "user");
    var wait = addTalk("…", "ai loading");
    $("talk-send").disabled = true;
    post("/api/english/chat", { session_id: sessionId, text: q }).then(function (d) {
      $("talk-send").disabled = false;
      if (!d.ok) { wait.textContent = "오류: " + (d.error || "실패"); return; }
      wait.classList.remove("loading");
      wait.textContent = d.reply;
      if ($("talk-tts").checked) speak(d.reply);
      $("talk-input").focus();
    }).catch(function () { $("talk-send").disabled = false; wait.textContent = "네트워크 오류"; });
  }
  $("talk-mic").addEventListener("click", function () {
    listen(function (text) { $("talk-input").value = text; sendTalk(text); }, $("talk-mic"));
  });
  $("talk-finish").addEventListener("click", function () {
    if (!sessionId) return;
    if (window.speechSynthesis) speechSynthesis.cancel();
    $("talk-finish").disabled = true;
    $("talk-hint").textContent = "피드백을 만들고 있어요… (몇 초 걸립니다)";
    post("/api/english/finish", { session_id: sessionId }).then(function (d) {
      $("talk-finish").disabled = false;
      $("talk-hint").textContent = "";
      if (!d.ok) { alert(d.error || "피드백 실패"); return; }
      $("talk-chat").hidden = true;
      showFeedback(d.feedback);
    }).catch(function () { $("talk-finish").disabled = false; $("talk-hint").textContent = ""; });
  });
  function showFeedback(fb) {
    var box = $("talk-fb");
    box.hidden = false;
    box.innerHTML = "";
    var card = el("div", "card");
    card.appendChild(el("h3", "eng-h", "연습 피드백"));
    if (fb.corrections && fb.corrections.length) {
      var sec = el("div", "fb-sec");
      sec.appendChild(el("h4", "", "교정"));
      fb.corrections.forEach(function (c) {
        var it = el("div", "fb-item");
        it.appendChild(el("div", "orig", c.original || ""));
        it.appendChild(el("div", "fixed", c.fixed || ""));
        it.appendChild(el("div", "why", c.why || ""));
        sec.appendChild(it);
      });
      card.appendChild(sec);
    }
    if (fb.expressions && fb.expressions.length) {
      var sec2 = el("div", "fb-sec");
      sec2.appendChild(el("h4", "", "이럴 땐 이렇게 — 바로 쓰는 표현"));
      fb.expressions.forEach(function (x) {
        var it = el("div", "fb-item");
        it.appendChild(el("div", "why", x.ko || ""));
        it.appendChild(el("div", "fixed", x.en || ""));
        var save = el("button", "sent-btn", "카드로 저장");
        save.type = "button";
        save.style.marginTop = "5px";
        save.addEventListener("click", function () {
          post("/api/english/cards", { front: x.ko || "", back: x.en || "" })
            .then(function (d) {
              if (d.ok) { save.textContent = "저장됨 ✓"; save.disabled = true; }
              else alert(d.error || "저장 실패");
            });
        });
        it.appendChild(save);
        sec2.appendChild(it);
      });
      card.appendChild(sec2);
    }
    if (fb.good && fb.good.length) {
      var sec3 = el("div", "fb-sec");
      sec3.appendChild(el("h4", "", "잘한 점"));
      fb.good.forEach(function (g) { sec3.appendChild(el("div", "fb-item", "👍 " + g)); });
      card.appendChild(sec3);
    }
    var again = el("button", "", "다른 연습 하기");
    again.type = "button";
    again.addEventListener("click", function () {
      box.hidden = true;
      $("talk-pick").hidden = false;
    });
    card.appendChild(again);
    box.appendChild(card);
    box.scrollIntoView({ behavior: "smooth" });
  }

  // ---------- 내 피치
  var pendingScript = null;
  $("pitch-polish").addEventListener("click", function () {
    var text = $("pitch-src").value.trim();
    $("pitch-status").textContent = "다듬는 중… (몇 초 걸립니다)";
    $("pitch-polish").disabled = true;
    post("/api/english/polish", { text: text }).then(function (d) {
      $("pitch-polish").disabled = false;
      $("pitch-status").textContent = "";
      if (!d.ok) { alert(d.error || "실패"); return; }
      pendingScript = { title: d.title, source: text, sentences: d.sentences };
      var pv = $("pitch-preview");
      pv.hidden = false;
      pv.innerHTML = "";
      pv.appendChild(el("h4", "eng-h", "미리보기 — " + (d.title || "새 스크립트")));
      d.sentences.forEach(function (s) {
        var row = el("div", "sent-row");
        var t = el("div", "txt");
        t.appendChild(el("div", "en", s.en));
        t.appendChild(el("div", "ko", s.ko));
        row.appendChild(t);
        pv.appendChild(row);
      });
      var save = el("button", "", "저장하고 연습하기");
      save.type = "button";
      save.style.marginTop = "10px";
      save.addEventListener("click", function () {
        post("/api/english/scripts", pendingScript).then(function (d2) {
          if (!d2.ok) { alert(d2.error || "저장 실패"); return; }
          pv.hidden = true;
          $("pitch-src").value = "";
          loadScripts();
          openScript(d2.item);
        });
      });
      pv.appendChild(save);
    }).catch(function () { $("pitch-polish").disabled = false; $("pitch-status").textContent = "네트워크 오류"; });
  });

  function loadScripts() {
    fetch("/api/english/scripts").then(function (r) { return r.json(); }).then(function (d) {
      var list = $("pitch-list");
      list.innerHTML = "";
      if (!d.ok) { list.textContent = d.error || "오류"; return; }
      if (!d.items.length) { list.textContent = "저장된 스크립트가 없습니다."; return; }
      d.items.forEach(function (s) {
        var row = el("div", "card-row");
        var open = el("a", "fb", "");
        open.href = "#";
        open.textContent = s.title + " (" + (s.sentences || []).length + "문장)";
        open.addEventListener("click", function (e) { e.preventDefault(); openScript(s); });
        row.appendChild(open);
        var d1 = el("button", "link-btn", "삭제");
        d1.type = "button";
        d1.addEventListener("click", function () {
          if (!confirm('"' + s.title + '" 스크립트를 삭제할까요?')) return;
          post("/api/english/scripts/" + s.id + "/delete").then(function () {
            $("pitch-practice").innerHTML = "";
            loadScripts();
          });
        });
        row.appendChild(d1);
        list.appendChild(row);
      });
    }).catch(function () {});
  }

  function norm(s) {
    return s.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").split(/\\s+/).filter(Boolean);
  }
  function compare(target, said) {
    var a = norm(target), b = norm(said);
    var hit = {};
    var used = {};
    a.forEach(function (w, i) {
      for (var j = 0; j < b.length; j++) {
        if (!used[j] && b[j] === w) { used[j] = true; hit[i] = true; break; }
      }
    });
    var n = Object.keys(hit).length;
    var pct = a.length ? Math.round(100 * n / a.length) : 0;
    var html = a.map(function (w, i) {
      return '<span class="' + (hit[i] ? "hit" : "miss") + '">' + w + "</span>";
    }).join(" ");
    return { pct: pct, html: html };
  }

  function openScript(s) {
    var box = $("pitch-practice");
    box.innerHTML = "";
    var card = el("div", "card");
    card.appendChild(el("h3", "eng-h", "연습 — " + s.title));
    card.appendChild(el("div", "meta", "듣기로 원어민 발음을 듣고, 말하기로 따라 말해보세요. 초록=말한 단어, 빨강=빠뜨린 단어"));
    (s.sentences || []).forEach(function (sent) {
      var row = el("div", "sent-row");
      var t = el("div", "txt");
      t.appendChild(el("div", "en", sent.en));
      t.appendChild(el("div", "ko", sent.ko || ""));
      var res = el("div", "res");
      t.appendChild(res);
      var play = el("button", "sent-btn", "듣기");
      play.type = "button";
      play.addEventListener("click", function () { speak(sent.en); });
      var say = el("button", "sent-btn", "말하기");
      say.type = "button";
      say.addEventListener("click", function () {
        listen(function (text) {
          var c = compare(sent.en, text);
          res.innerHTML = "일치율 <b>" + c.pct + "%</b> · " + c.html +
            '<br><span class="meta">내 발화: ' + text + "</span>";
        }, say);
      });
      row.appendChild(play);
      row.appendChild(say);
      row.appendChild(t);
      card.appendChild(row);
    });
    box.appendChild(card);
    card.scrollIntoView({ behavior: "smooth" });
  }

  // ---------- 표현 카드
  var dueQueue = [];
  function loadCards() {
    fetch("/api/english/cards?due=1").then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { $("card-review").innerHTML = '<div class="meta">' + (d.error || "오류") + "</div>"; return; }
      dueQueue = d.items;
      nextCard();
    }).catch(function () {});
    fetch("/api/english/cards").then(function (r) { return r.json(); }).then(function (d) {
      var list = $("card-list");
      list.innerHTML = "";
      if (!d.ok) { list.textContent = d.error || "오류"; return; }
      if (!d.items.length) { list.textContent = "카드가 없습니다. 롤플레이 피드백에서 저장하거나 직접 추가해보세요."; return; }
      d.items.forEach(function (c) {
        var row = el("div", "card-row");
        row.appendChild(el("span", "box-chip", "상자" + c.box));
        row.appendChild(el("span", "fb", c.front + " → " + c.back));
        var d1 = el("button", "link-btn", "삭제");
        d1.type = "button";
        d1.addEventListener("click", function () {
          post("/api/english/cards/" + c.id + "/delete").then(loadCards);
        });
        row.appendChild(d1);
        list.appendChild(row);
      });
    }).catch(function () {});
  }
  function nextCard() {
    var box = $("card-review");
    box.innerHTML = "";
    if (!dueQueue.length) {
      box.innerHTML = '<div class="meta">오늘 복습할 카드를 모두 끝냈어요! 🎉</div>';
      return;
    }
    var c = dueQueue[0];
    var f = el("div", "flash");
    f.appendChild(el("div", "meta", "남은 카드 " + dueQueue.length + "장 · 상자" + c.box));
    f.appendChild(el("div", "front", c.front));
    var reveal = el("button", "", "정답 보기");
    reveal.type = "button";
    reveal.addEventListener("click", function () {
      reveal.remove();
      f.appendChild(el("div", "back", c.back));
      if (c.note) f.appendChild(el("div", "note", c.note));
      var play = el("button", "sent-btn", "듣기");
      play.type = "button";
      play.addEventListener("click", function () { speak(c.back); });
      f.appendChild(play);
      var btns = el("div", "row");
      btns.style.justifyContent = "center";
      btns.style.marginTop = "12px";
      var again = el("button", "", "다시 (오늘 또)");
      again.type = "button";
      again.style.background = "var(--red)";
      var good = el("button", "", "알겠음");
      good.type = "button";
      [[again, false], [good, true]].forEach(function (pair) {
        pair[0].addEventListener("click", function () {
          post("/api/english/cards/" + c.id + "/review", { ok: pair[1] }).then(function () {
            dueQueue.shift();
            if (!pair[1]) dueQueue.push(c);
            nextCard();
          });
        });
        btns.appendChild(pair[0]);
      });
      f.appendChild(btns);
    });
    f.appendChild(reveal);
    box.appendChild(f);
  }
  $("card-add").addEventListener("click", function () {
    post("/api/english/cards", {
      front: $("card-front").value, back: $("card-back").value, note: $("card-note").value,
    }).then(function (d) {
      if (!d.ok) { alert(d.error || "추가 실패"); return; }
      $("card-front").value = $("card-back").value = $("card-note").value = "";
      loadCards();
    });
  });

  loadHome();
  if (window.speechSynthesis) speechSynthesis.getVoices();  // 보이스 목록 예열
})();
</script>"""


def _english_page() -> str:
    groups = []
    for lv, name in english.LEVELS.items():
        cards = "".join(
            f'<button type="button" class="scn" data-key="{key}">'
            f'<b>{esc(sc["title"])}</b><span>{esc(sc["desc"])}</span></button>'
            for key, sc in english.SCENARIOS.items() if sc["level"] == lv
        )
        groups.append(
            f'<div class="card"><h3 class="eng-h">Level {lv} · {name}</h3>'
            f'<div class="scn-grid">{cards}</div></div>'
        )
    scn_json = json.dumps(
        {k: {"title": v["title"], "level": v["level"], "desc": v["desc"],
             "goals": v["goals"]} for k, v in english.SCENARIOS.items()},
        ensure_ascii=False)
    return (ENGLISH_PAGE_TMPL
            .replace("__SCENARIO_CARDS__", "".join(groups))
            .replace("__SCENARIO_JSON__", scn_json))


@app.get("/english", response_class=HTMLResponse)
def english_page(request: Request):
    redirect = gate(request)
    if redirect:
        return redirect
    user = user_of(request)
    if not auth.is_owner(user):
        return RedirectResponse("/bid", status_code=302)
    return layout("스피킹 연습", icon("mic", 20) + " 스피킹", "/english",
                  _english_page(), user=user, admin=auth.is_admin(user))


@app.get("/api/genie/usage")
def genie_usage(request: Request):
    if not _owner_user(request):
        return {"ok": False, "error": "권한이 없습니다."}
    return {"ok": True, "usage": summarize.usage_today()}


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
        content = f"""<div class="card" style="text-align:center;padding:56px 24px;max-width:420px;margin:9vh auto 0">
  <p style="margin:0 0 10px;font-weight:800;font-size:1.5rem;letter-spacing:-0.02em">One<b style="color:var(--accent)">AI</b>Gen</p>
  <p style="margin:0 0 4px;font-weight:700">회사 전용 서비스입니다</p>
  <p class="meta" style="margin:0 0 28px">구글 계정으로 로그인해주세요.</p>
  <a href="{auth.login_url()}"><button type="button" style="padding:13px 32px;font-size:1rem;border-radius:12px">Google 계정으로 로그인</button></a>
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


@app.get("/api/searches")
def list_searches_api(request: Request, page: str = Query("bid")):
    email = user_of(request)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다."}
    try:
        return {"ok": True, "items": searches.list_searches(email, page)}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/searches")
def add_search_api(request: Request, body: dict = Body(...)):
    email = user_of(request)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다."}
    try:
        item = searches.add_search(
            email, str(body.get("page") or ""), str(body.get("label") or ""),
            body.get("params") or {})
        return {"ok": True, "item": item}
    except store.StoreError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/searches/{search_id}/delete")
def delete_search_api(request: Request, search_id: int):
    email = user_of(request)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다."}
    try:
        searches.delete_search(email, search_id)
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
    <a href="/kakao/test" style="margin-left:8px"><button type="button" style="background:var(--fill);border-color:transparent;color:var(--sub)">테스트 메시지 보내기</button></a>
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
  <p style="word-break:break-all;background:var(--fill);border-radius:8px;padding:12px;font-size:0.85rem">{esc(refresh)}</p>
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
