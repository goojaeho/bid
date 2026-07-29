"""정부과제/지원사업 소스 수집기.

API 소스: 기업마당(BIZINFO_API_KEY), K-Startup(KSTARTUP_API_KEY)
크롤링 소스: NIPA, KOCCA, DIP, 대구TP, 경북TP, 부산TP (모두 서버사이드 HTML)

공통 아이템 형식(dict):
  source, title, org, region, begin(YYYY-MM-DD|None), end(YYYY-MM-DD|None),
  status, url, reg_date(YYYY-MM-DD|None)
"""
from __future__ import annotations

import html as html_lib
import logging
import os
import re
import time
import urllib3
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 12

# 대구TP는 중간 인증서 누락으로 verify=False 필요 → 경고 억제
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_DATE_RE = re.compile(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def _norm_date(text: str | None) -> str | None:
    """'26.07.22', '2026-07-28', '2026.07.29' 등 → 'YYYY-MM-DD'."""
    if not text:
        return None
    m = _DATE_RE.search(str(text))
    if not m:
        # K-Startup: YYYYMMDD
        m8 = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", str(text))
        if m8:
            return f"{m8.group(1)}-{m8.group(2)}-{m8.group(3)}"
        return None
    y, mo, d = m.groups()
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _period(text: str | None) -> tuple[str | None, str | None]:
    """'A ~ B' 형태의 기간 문자열 → (begin, end)."""
    if not text:
        return None, None
    parts = str(text).split("~")
    begin = _norm_date(parts[0])
    end = _norm_date(parts[1]) if len(parts) > 1 else None
    return begin, end


def _get(url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {"User-Agent": UA})
    resp = requests.get(url, **kw)
    resp.raise_for_status()
    return resp


def _soup(resp: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(resp.text, "html.parser")


def _strip_html(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", str(text))
    cleaned = html_lib.unescape(re.sub(r"\s+", " ", cleaned)).strip()
    return cleaned[:limit]


def _item(source, title, org, region, begin, end, status, url, reg_date,
          extras: dict | None = None) -> dict:
    item = {
        "source": source,
        "title": html_lib.unescape(str(title or "")).strip(),
        "org": (org or "").strip(),
        "region": region or "",
        "begin": begin,
        "end": end,
        "status": (status or "").strip(),
        "url": url,
        "reg_date": reg_date,
        "target": "",
        "method": "",
        "summary": "",
        "contact": "",
    }
    if extras:
        item.update(extras)
    return item


# ---------------------------------------------------------------- API 소스

def fetch_bizinfo(count: int = 150) -> list[dict]:
    key = os.environ.get("BIZINFO_API_KEY", "").strip()
    if not key:
        return []
    resp = _get(
        "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do",
        params={"crtfcKey": key, "dataType": "json", "searchCnt": count},
    )
    items = []
    for it in resp.json().get("jsonArray", []):
        begin, end = _period(it.get("reqstBeginEndDe"))
        hashtags = it.get("hashtags") or ""
        region = ""
        for r in ("대구", "경북", "부산"):
            if r in hashtags or r in (it.get("jrsdInsttNm") or ""):
                region = r
                break
        items.append(_item(
            "기업마당", it.get("pblancNm"),
            it.get("excInsttNm") or it.get("jrsdInsttNm"),
            region or "전국",
            begin, end, "",
            it.get("pblancUrl"),
            _norm_date(it.get("creatPnttm")),
            extras={
                "target": _strip_html(it.get("trgetNm"), 200),
                "method": _strip_html(it.get("reqstMthPapersCn"), 200),
                "summary": _strip_html(it.get("bsnsSumryCn")),
                "contact": _strip_html(it.get("refrncNm"), 200),
            },
        ))
    return items


def fetch_kstartup(count: int = 100) -> list[dict]:
    key = os.environ.get("KSTARTUP_API_KEY", "").strip()
    if not key:
        return []
    resp = _get(
        "https://nidapi.k-startup.go.kr/api/kisedKstartupService/v1/getAnnouncementInformation",
        params={"serviceKey": key, "page": 1, "perPage": count, "returnType": "json"},
    )
    items = []
    for it in resp.json().get("data", []):
        items.append(_item(
            "K-Startup", it.get("biz_pbanc_nm") or it.get("intg_pbanc_biz_nm"),
            it.get("pbanc_ntrp_nm"),
            (it.get("supt_regin") or "전국").split(",")[0].strip(),
            _norm_date(it.get("pbanc_rcpt_bgng_dt")),
            _norm_date(it.get("pbanc_rcpt_end_dt")),
            "접수중" if it.get("rcrt_prgs_yn") == "Y" else "마감",
            it.get("detl_pg_url"),
            _norm_date(it.get("pbanc_rcpt_bgng_dt")),
            extras={
                "target": _strip_html(it.get("aply_trgt_ctnt") or it.get("aply_trgt"), 300),
                "summary": _strip_html(it.get("pbanc_ctnt")),
                "contact": _strip_html(it.get("prch_cnpl_no"), 100),
            },
        ))
    return items


# ------------------------------------------------------------- 크롤링 소스

def fetch_nipa() -> list[dict]:
    base = "https://www.nipa.kr"
    soup = _soup(_get(f"{base}/home/2-2"))
    items = []
    for tr in soup.select("table tbody tr"):
        a = tr.select_one('a[href*="/home/2-2/"]')
        if not a:
            continue
        text = tr.get_text(" ", strip=True)
        m = re.search(r"신청기간\s*:\s*(.{0,25}~.{0,25})", text)
        begin, end = _period(m.group(1)) if m else (None, None)
        dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
        items.append(_item(
            "NIPA", a.get_text(" ", strip=True), "정보통신산업진흥원", "전국",
            begin, end, "", urljoin(base, a["href"]),
            dates[-1] if dates else None,
        ))
    return items


def fetch_kocca() -> list[dict]:
    base = "https://www.kocca.kr"
    soup = _soup(_get(f"{base}/kocca/pims/list.do?menuNo=204104"))
    items = []
    for tr in soup.select("table tbody tr"):
        a = tr.select_one('a[href*="view.do?intcNo="]')
        if not a:
            continue
        period_td = tr.select_one('td[data-label="접수기간"]')
        date_td = tr.select_one('td[data-label="공고일"]')
        begin, end = _period(period_td.get_text(strip=True) if period_td else None)
        items.append(_item(
            "KOCCA", a.get_text(" ", strip=True), "한국콘텐츠진흥원", "전국",
            begin, end, "접수중", urljoin(base, a["href"]),
            _norm_date(date_td.get_text(strip=True) if date_td else None),
        ))
    return items


def fetch_dip() -> list[dict]:
    base = "https://www.dip.or.kr"
    soup = _soup(_get(f"{base}/home/notice/businessbbs/boardList.ubs?fboardcd=business"))
    items = []
    for tr in soup.select("tr[onclick]"):
        m = re.search(r"read\('dipadmin','(\d+)'\)", tr.get("onclick", ""))
        title_el = tr.select_one("td.title a") or tr.select_one("td.title")
        if not m or not title_el:
            continue
        badge = tr.select_one("td.state .badge") or tr.select_one("td.state")
        period_td = tr.select_one('td[data-heading="기간"]')
        reg_td = tr.select_one('td[data-heading="등록일"]')
        begin, end = _period(period_td.get_text(strip=True) if period_td else None)
        items.append(_item(
            "DIP", title_el.get_text(" ", strip=True), "대구디지털혁신진흥원", "대구",
            begin, end,
            badge.get_text(strip=True) if badge else "",
            f"{base}/home/notice/businessbbs/boardRead.ubs?fboardcd=business&fboardnum={m.group(1)}&sfpage=1",
            _norm_date(reg_td.get_text(strip=True) if reg_td else None),
        ))
    return items


def fetch_daegu_tp() -> list[dict]:
    base = "https://www.ttp.org"
    bbs = "BBSMSTR_000000000003"
    resp = _get(f"{base}/bbs/BoardControll.do?bbsId={bbs}", verify=False)
    soup = _soup(resp)
    items = []
    for row in soup.select("div.mtable_row"):
        m = re.search(r"fn_egov_inqire_notice\('(\d+)'", str(row))
        title_el = row.select_one("span.title")
        if not m or not title_el:
            continue
        text = row.get_text(" ", strip=True)
        pm = re.search(r"(\d{2}-\d{2}-\d{2}\s*~\s*\d{2}-\d{2}-\d{2})", text)
        begin, end = _period(pm.group(1)) if pm else (None, None)
        state = row.select_one("span.state")
        items.append(_item(
            "대구TP", title_el.get_text(" ", strip=True), "대구테크노파크", "대구",
            begin, end,
            state.get_text(strip=True) if state else "",
            f"{base}/bbs/BoardControllView.do?bbsId={bbs}&nttId={m.group(1)}",
            begin,
        ))
    return items


def fetch_gbtp() -> list[dict]:
    base = "https://www.gbtp.or.kr"
    bbs = "BBSMSTR_000000000021"
    soup = _soup(_get(f"{base}/user/board.do?bbsId={bbs}"))
    items = []
    for tr in soup.select("table.tablelist tbody tr"):
        m = re.search(r"fn_detail\('(\d+)'", str(tr))
        title_el = tr.select_one("td.title a") or tr.select_one("td.title")
        if not m or not title_el:
            continue
        term_td = tr.select_one("td.term")
        begin, end = _period(term_td.get_text(strip=True) if term_td else None)
        text = tr.get_text(" ", strip=True)
        term_text = term_td.get_text(strip=True) if term_td else ""
        reg_dates = [d for d in re.findall(r"20\d{2}-\d{2}-\d{2}", text)
                     if d not in term_text]
        reg = reg_dates[0] if reg_dates else None
        status = "접수중" if "접수중" in text else ("마감" if "마감" in text else "")
        items.append(_item(
            "경북TP", title_el.get_text(" ", strip=True), "경북테크노파크", "경북",
            begin, end, status,
            f"{base}/user/boardDetail.do?bbsId={bbs}&nttNo={m.group(1)}&pageIndex=1",
            reg,
        ))
    return items


def fetch_btp() -> list[dict]:
    base = "https://www.btp.or.kr"
    soup = _soup(_get(f"{base}/kor/CMS/Board/Board.do?mCode=MN013"))
    items = []
    for tr in soup.select("table.bdListTbl tbody tr"):
        a = tr.select_one('td.subject a[href*="mode=view"]')
        if not a:
            continue
        title_el = a.select_one("span.titleHover") or a
        period_td = tr.select_one("td.period")
        state = tr.select_one("td.state .status") or tr.select_one("td.state")
        date_td = tr.select_one("td.date")
        period_text = period_td.get_text(" ", strip=True) if period_td else None
        if period_text:
            period_text = re.sub(r"D-\d+", "", period_text)
        begin, end = _period(period_text)
        items.append(_item(
            "부산TP", title_el.get_text(" ", strip=True), "부산테크노파크", "부산",
            begin, end,
            state.get_text(strip=True) if state else "",
            urljoin(f"{base}/kor/CMS/Board/Board.do", a["href"]),
            _norm_date(date_td.get_text(strip=True) if date_td else None),
        ))
    return items


# ---------------------------------------------------------------- 통합

SOURCES: dict[str, callable] = {
    "기업마당": fetch_bizinfo,
    "K-Startup": fetch_kstartup,
    "NIPA": fetch_nipa,
    "KOCCA": fetch_kocca,
    "DIP": fetch_dip,
    "대구TP": fetch_daegu_tp,
    "경북TP": fetch_gbtp,
    "부산TP": fetch_btp,
}

_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 600  # 10분


def fetch_source(name: str) -> list[dict]:
    """단일 소스 수집 (10분 캐시)."""
    cached = _CACHE.get(name)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    items = SOURCES[name]()
    _CACHE[name] = (time.time(), items)
    return items
