"""공고 상세 페이지/첨부파일(PDF·HWP·HWPX) 텍스트 추출 + Gemini 요약."""
from __future__ import annotations

import io
import os
import re
import time
import zipfile
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

TIMEOUT = 20
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 15000
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# SSRF 방지: 수집 소스 도메인만 접근 허용
ALLOWED_HOSTS = {
    "www.bizinfo.go.kr", "bizinfo.go.kr",
    "www.k-startup.go.kr", "k-startup.go.kr",
    "www.nipa.kr", "nipa.kr",
    "www.kocca.kr", "kocca.kr",
    "www.dip.or.kr", "dip.or.kr",
    "www.ttp.org", "ttp.org",
    "www.gbtp.or.kr", "gbtp.or.kr",
    "www.btp.or.kr", "btp.or.kr",
    "www.g2b.go.kr", "g2b.go.kr",
}

ATTACH_RE = re.compile(r"\.(pdf|hwpx|hwp)(\?|$)", re.I)


class SummarizeError(Exception):
    pass


def gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY", "").strip() or None


def gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest").strip()


def _get(url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("headers", {"User-Agent": UA})
    if urlparse(url).hostname in ("www.ttp.org", "ttp.org"):
        kw.setdefault("verify", False)
    resp = requests.get(url, **kw)
    resp.raise_for_status()
    return resp


def extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:30])


def extract_hwp(data: bytes) -> str:
    """HWP(OLE)의 PrvText(미리보기 텍스트) 스트림 추출."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if ole.exists("PrvText"):
            return ole.openstream("PrvText").read().decode("utf-16", errors="ignore")
        return ""
    finally:
        ole.close()


def extract_hwpx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        texts = []
        for name in zf.namelist():
            if name.startswith("Contents/section") and name.endswith(".xml"):
                xml = zf.read(name).decode("utf-8", errors="ignore")
                texts.append(re.sub(r"<[^>]+>", " ", xml))
        return "\n".join(texts)


def _extract_attachment(url: str) -> str:
    resp = _get(url, stream=True)
    data = resp.raw.read(MAX_FILE_BYTES, decode_content=True)
    lower = url.lower()
    if ".pdf" in lower:
        return extract_pdf(data)
    if ".hwpx" in lower:
        return extract_hwpx(data)
    if ".hwp" in lower:
        return extract_hwp(data)
    return ""


def collect_text(detail_url: str) -> str:
    """상세 페이지 본문 + 첫 번째 첨부파일 텍스트 수집."""
    host = urlparse(detail_url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise SummarizeError(f"허용되지 않은 도메인입니다: {host}")

    resp = _get(detail_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))

    attach_text = ""
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if ATTACH_RE.search(href):
            try:
                attach_text = _extract_attachment(urljoin(detail_url, href))
                if len(attach_text.strip()) > 300:
                    break
            except Exception:
                continue

    text = (attach_text.strip() + "\n\n" + page_text) if attach_text.strip() else page_text
    text = text[:MAX_TEXT_CHARS]
    if len(text.strip()) < 100:
        raise SummarizeError("공고 내용을 읽지 못했습니다. 원문 링크에서 직접 확인해주세요.")
    return text


PROMPT = """다음은 한국 정부/공공기관의 지원사업·입찰 공고문입니다. 아래 형식으로 간결하게 요약해주세요.
항목에 해당하는 내용이 없으면 그 항목은 생략하세요. 마크다운 없이 일반 텍스트로, 각 줄은 '· '로 시작하세요.

· 사업목적: (한 문장)
· 지원대상:
· 지원내용/규모:
· 신청기간:
· 신청방법:
· 문의처:

공고문:
"""


def gemini_summarize(text: str) -> str:
    key = gemini_key()
    if not key:
        raise SummarizeError("GEMINI_API_KEY가 설정되지 않았습니다.")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model()}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": PROMPT + text}]}]},
        timeout=40,
    )
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        err = data.get("error", {}).get("message", str(data)[:200])
        raise SummarizeError(f"요약 생성 실패: {err}")


_CACHE: dict[str, tuple[float, str]] = {}
CACHE_TTL = 3600


def summarize_url(detail_url: str) -> str:
    cached = _CACHE.get(detail_url)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    summary = gemini_summarize(collect_text(detail_url))
    _CACHE[detail_url] = (time.time(), summary)
    return summary
