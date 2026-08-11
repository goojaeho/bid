"""공고 상세 페이지/첨부파일(PDF·HWP·HWPX) 텍스트 추출 + Gemini 요약."""
from __future__ import annotations

import io
import json
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


def daily_limit() -> int:
    try:
        return int(os.environ.get("GEMINI_DAILY_LIMIT", "1000"))
    except ValueError:
        return 1000


def _record_usage(kind: str, data: dict) -> None:
    """Gemini 응답의 usageMetadata를 일별로 집계 (실패해도 무시)."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app import todos

        meta = data.get("usageMetadata") or {}
        todos._request("POST", "rpc/bump_gemini_usage", json={
            "d": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
            "k": kind,
            "t": int(meta.get("totalTokenCount") or 0),
        })
    except Exception:
        pass


def usage_today() -> dict:
    """오늘 Gemini 사용량 합계 + 한도/잔여."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import todos

    day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    requests_n = tokens_n = 0
    try:
        rows = todos._request(
            "GET", "gemini_usage",
            params={"select": "requests,tokens", "day": f"eq.{day}"},
        ).json()
        requests_n = sum(r.get("requests", 0) for r in rows)
        tokens_n = sum(r.get("tokens", 0) for r in rows)
    except Exception:
        pass
    limit = daily_limit()
    return {"requests": requests_n, "tokens": tokens_n,
            "limit": limit, "remaining": max(0, limit - requests_n)}


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
        summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        err = data.get("error", {}).get("message", str(data)[:200])
        raise SummarizeError(f"요약 생성 실패: {err}")
    _record_usage("summary", data)
    return summary


def gemini_chat(messages: list[dict]) -> str:
    """멀티턴 채팅. messages: [{role: 'user'|'model', text: str}, ...] → 답변 텍스트."""
    key = gemini_key()
    if not key:
        raise SummarizeError("GEMINI_API_KEY가 설정되지 않았습니다.")
    contents = [
        {"role": m["role"] if m.get("role") in ("user", "model") else "user",
         "parts": [{"text": str(m.get("text", ""))[:8000]}]}
        for m in messages[-20:]  # 최근 20턴만 유지
    ]
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model()}:generateContent",
        params={"key": key},
        json={"contents": contents},
        timeout=60,
    )
    data = resp.json()
    try:
        reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        err = data.get("error", {}).get("message", str(data)[:200])
        raise SummarizeError(f"응답 실패: {err}")
    _record_usage("genie", data)
    return reply


def _gemini_call(payload: dict, kind: str, timeout: int = 60) -> dict:
    """generateContent 호출 + 사용량 기록. candidates JSON을 그대로 반환."""
    key = gemini_key()
    if not key:
        raise SummarizeError("GEMINI_API_KEY가 설정되지 않았습니다.")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model()}:generateContent",
        params={"key": key}, json=payload, timeout=timeout,
    )
    data = resp.json()
    try:
        data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        err = data.get("error", {}).get("message", str(data)[:200])
        raise SummarizeError(f"응답 실패: {err}")
    _record_usage(kind, data)
    return data


def _first_text(data: dict) -> str:
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def gemini_english_chat(persona: str, messages: list[dict]) -> str:
    """영어 롤플레이 대화. persona = 시나리오 시스템 프롬프트."""
    contents = [
        {"role": m["role"] if m.get("role") in ("user", "model") else "user",
         "parts": [{"text": str(m.get("text", ""))[:4000]}]}
        for m in messages[-30:]
    ]
    if not contents:  # 첫 턴: AI가 먼저 말을 건다
        contents = [{"role": "user", "parts": [{"text": "(Start the conversation.)"}]}]
    data = _gemini_call({
        "systemInstruction": {"parts": [{"text": persona}]},
        "contents": contents,
    }, kind="english")
    return _first_text(data)


FEEDBACK_PROMPT = """다음은 한국인 학습자(user)와 AI(model)의 영어 회화 연습 대화입니다.
학습자의 영어에 대해 아래 JSON 형식으로만 피드백하세요. 설명은 한국어로 씁니다.

{"corrections": [{"original": "학습자가 말한 문장", "fixed": "고친 문장", "why": "이유(한국어 한 줄)"}],
 "expressions": [{"ko": "상황(한국어)", "en": "이럴 때 원어민이 쓰는 표현"}],
 "good": ["잘한 점(한국어)"]}

corrections는 중요한 것 최대 5개, expressions는 이 대화에서 바로 써먹을 수 있는 표현 최대 5개,
good은 최대 3개. 학습자 발화가 거의 없으면 각 배열을 비워도 됩니다.

대화:
"""


def gemini_english_feedback(messages: list[dict]) -> dict:
    """연습 대화 전체에 대한 교정/표현/칭찬 피드백 (JSON)."""
    transcript = "\n".join(
        f"{'학습자' if m.get('role') == 'user' else 'AI'}: {str(m.get('text', ''))[:1000]}"
        for m in messages[-40:]
    )
    data = _gemini_call({
        "contents": [{"parts": [{"text": FEEDBACK_PROMPT + transcript}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }, kind="english", timeout=90)
    try:
        fb = json.loads(_first_text(data))
    except json.JSONDecodeError:
        raise SummarizeError("피드백 생성 실패 — 다시 시도해주세요.")
    return {"corrections": fb.get("corrections") or [],
            "expressions": fb.get("expressions") or [],
            "good": fb.get("good") or []}


POLISH_PROMPT = """다음은 한국 스타트업 대표가 쓴 피치/소개 스크립트 초안입니다 (한국어 또는 영어).
이를 국제 비즈니스 자리(IR, 전시회)에서 말하기 좋은 자연스러운 영어로 다듬고,
말하기 연습을 위해 문장 단위로 나눠 아래 JSON 형식으로만 반환하세요.

{"title": "스크립트 제목(한국어, 10자 내외)",
 "sentences": [{"en": "영어 문장", "ko": "한국어 뜻"}]}

문장은 한 번에 말하기 좋은 길이(20단어 이하)로 나누고, 구어체로 자연스럽게 쓰세요.

초안:
"""


def gemini_polish_script(text: str) -> dict:
    """피치 초안 → 비즈니스 영어 문장 목록 (JSON)."""
    data = _gemini_call({
        "contents": [{"parts": [{"text": POLISH_PROMPT + text[:6000]}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }, kind="english", timeout=90)
    try:
        out = json.loads(_first_text(data))
        sentences = [
            {"en": str(s.get("en", "")).strip(), "ko": str(s.get("ko", "")).strip()}
            for s in out.get("sentences", []) if isinstance(s, dict) and s.get("en")
        ]
    except (json.JSONDecodeError, AttributeError):
        raise SummarizeError("스크립트 다듬기 실패 — 다시 시도해주세요.")
    if not sentences:
        raise SummarizeError("문장을 만들지 못했습니다. 초안을 조금 더 써주세요.")
    return {"title": str(out.get("title", "")).strip()[:100], "sentences": sentences}


TRANSCRIBE_PROMPT = (
    "다음 오디오는 회의 녹음의 일부입니다. 들리는 내용을 한국어로 정확히 전사하세요. "
    "화자가 바뀌는 것 같으면 새 줄에 '- '로 시작해 구분하고, 영어 등 외국어 발화는 "
    "그대로 적으세요. 추임새(음, 어 등)는 생략하고, 전사 텍스트만 출력하세요. "
    "알아들을 수 없는 부분은 (불명확)으로 표시하세요."
)


def gemini_transcribe_audio(data: bytes, mime: str = "audio/webm") -> str:
    """회의 녹음 조각(≤20MB)을 한국어로 전사."""
    import base64

    payload = {
        "contents": [{"parts": [
            {"text": TRANSCRIBE_PROMPT},
            {"inline_data": {"mime_type": mime.split(";")[0] or "audio/webm",
                             "data": base64.b64encode(data).decode()}},
        ]}],
    }
    result = _gemini_call(payload, kind="meeting", timeout=180)
    return _first_text(result)


MINUTES_PROMPT = """다음은 회의 녹음을 전사한 텍스트입니다. 아래 JSON 형식으로만 회의록을 작성하세요.
내용은 모두 한국어로, 해당 내용이 없으면 빈 배열/빈 문자열로 두세요.

{"title": "회의 제목 (내용 기반, 15자 내외)",
 "attendees": ["파악된 참석자/화자"],
 "summary": ["주요 논의 내용 (핵심만 5~10개)"],
 "decisions": ["결정된 사항"],
 "action_items": [{"task": "할 일", "owner": "담당(불명확하면 빈 문자열)", "due": "기한(언급됐으면)"}]}

전사 텍스트:
"""


def gemini_minutes(transcript: str) -> dict:
    """전사 전체 → 구조화된 회의록 (JSON)."""
    data = _gemini_call({
        "contents": [{"parts": [{"text": MINUTES_PROMPT + transcript[:100000]}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }, kind="meeting", timeout=120)
    try:
        out = json.loads(_first_text(data))
    except json.JSONDecodeError:
        raise SummarizeError("회의록 생성 실패 — 다시 시도해주세요.")
    return {
        "title": str(out.get("title", "")).strip()[:200],
        "attendees": [str(a) for a in (out.get("attendees") or [])][:20],
        "summary": [str(s) for s in (out.get("summary") or [])][:20],
        "decisions": [str(d) for d in (out.get("decisions") or [])][:20],
        "action_items": [
            {"task": str(i.get("task", "")), "owner": str(i.get("owner", "")),
             "due": str(i.get("due", ""))}
            for i in (out.get("action_items") or []) if isinstance(i, dict)
        ][:20],
    }


def gemini_translate_paragraphs(paragraphs: list[dict]) -> list[dict]:
    """[{id, text}] 목록을 한국어로 번역해 같은 형식으로 반환 (Gemini JSON 모드)."""
    key = gemini_key()
    if not key:
        raise SummarizeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    results: list[dict] = []
    chunk: list[dict] = []
    size = 0
    chunks: list[list[dict]] = []
    for p in paragraphs:
        text = str(p.get("text", ""))
        if size + len(text) > 20000 and chunk:
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append({"id": p.get("id"), "text": text})
        size += len(text)
    if chunk:
        chunks.append(chunk)

    for part in chunks:
        prompt = (
            "다음 JSON 배열의 각 문단 text를 자연스러운 한국어로 번역하세요. "
            "id는 그대로 유지하고, 결과를 [{\"id\":..., \"text\":\"번역문\"}] 형식의 "
            "JSON 배열로만 반환하세요.\n\n" + json.dumps(part, ensure_ascii=False)
        )
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model()}:generateContent",
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=120,
        )
        data = resp.json()
        try:
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            translated = json.loads(raw)
        except (KeyError, IndexError, json.JSONDecodeError):
            err = data.get("error", {}).get("message", str(data)[:200])
            raise SummarizeError(f"번역 실패: {err}")
        _record_usage("translate", data)
        by_id = {t.get("id"): str(t.get("text", "")) for t in translated
                 if isinstance(t, dict)}
        for p in part:
            results.append({"id": p["id"], "text": by_id.get(p["id"], p["text"])})
    return results


_CACHE: dict[str, tuple[float, str]] = {}
CACHE_TTL = 3600


def summarize_url(detail_url: str) -> str:
    cached = _CACHE.get(detail_url)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    summary = gemini_summarize(collect_text(detail_url))
    _CACHE[detail_url] = (time.time(), summary)
    return summary
