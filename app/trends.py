"""쇼츠·릴스 트렌드 분석기 — 유튜브 Data API 수집 + 지표 계산 + 저장.

틱톡 Research API는 학술기관 전용이고 인스타 해시태그 검색은 사실상 막혀 있어
공식·무료로 열려 있는 유튜브 쇼츠를 트렌드 센서로 쓴다 (쇼츠·릴스는 유행이 함께 움직임).
레퍼런스 URL은 oEmbed로 공개 메타데이터만 가져와 AI가 구조를 분해한다.

필요 환경변수: YOUTUBE_API_KEY (Google Cloud → YouTube Data API v3)
할당량: 하루 10,000 단위 무료. search.list=100, videos/channels.list=1
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from app import todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")
TIMEOUT = 15
YT = "https://www.googleapis.com/youtube/v3"
SHORTS_MAX_SEC = 180  # 쇼츠 = 3분 이하
VIDEO_FIELDS = ("id,video_id,title,channel,channel_subs,views,likes,comments,"
                "duration_sec,published_at,thumb,keyword,source,collected_at")


class TrendError(Exception):
    pass


def api_key() -> str:
    return os.environ.get("YOUTUBE_API_KEY", "").strip()


def enabled() -> bool:
    return bool(api_key())


def _get(path: str, **params) -> dict:
    if not api_key():
        raise TrendError("YOUTUBE_API_KEY가 설정되지 않았습니다.")
    params["key"] = api_key()
    resp = requests.get(f"{YT}/{path}", params=params, timeout=TIMEOUT)
    if resp.status_code >= 400:
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")
        except ValueError:
            detail = resp.text[:150]
        if "quota" in detail.lower():
            raise TrendError("오늘 유튜브 API 무료 한도를 다 썼습니다. 내일 다시 시도해주세요.")
        raise TrendError(f"유튜브 API 오류({resp.status_code}): {detail[:150]}")
    return resp.json()


# ------------------------------------------------------------ 파싱·지표

_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso: str) -> int:
    m = _DUR.fullmatch((iso or "").strip())
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def views_per_hour(views: int, published_at: str, now: datetime | None = None) -> int:
    """업로드 후 시간당 조회수 — '지금 터지는 중'인지 판단하는 핵심 지표."""
    try:
        pub = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0
    now = now or datetime.now(timezone.utc)
    hours = max((now - pub).total_seconds() / 3600, 1.0)
    return int(views / hours)


def viral_score(views: int, subs: int) -> float:
    """구독자 대비 조회수 배수 — 작은 채널인데 터진 영상이 진짜 트렌드 신호."""
    return round(views / max(subs, 1), 1)


def _rows_from_items(items: list[dict], subs_map: dict, keyword: str,
                     source: str) -> list[dict]:
    rows = []
    for v in items:
        snip = v.get("snippet") or {}
        stats = v.get("statistics") or {}
        content = v.get("contentDetails") or {}
        dur = parse_duration(content.get("duration", ""))
        if not dur or dur > SHORTS_MAX_SEC:
            continue
        thumbs = snip.get("thumbnails") or {}
        thumb = ((thumbs.get("medium") or thumbs.get("default") or {})
                 .get("url", ""))
        rows.append({
            "video_id": v.get("id", ""),
            "title": (snip.get("title") or "")[:300],
            "channel": (snip.get("channelTitle") or "")[:150],
            "channel_subs": subs_map.get(snip.get("channelId", ""), 0),
            "views": _int(stats.get("viewCount")),
            "likes": _int(stats.get("likeCount")),
            "comments": _int(stats.get("commentCount")),
            "duration_sec": dur,
            "published_at": snip.get("publishedAt"),
            "thumb": thumb,
            "keyword": keyword[:100],
            "source": source,
            "tags": (snip.get("tags") or [])[:15],
            "description": (snip.get("description") or "")[:600],
        })
    return rows


def _channel_subs(channel_ids: list[str]) -> dict:
    out: dict[str, int] = {}
    ids = [c for c in dict.fromkeys(channel_ids) if c]
    for i in range(0, len(ids), 50):
        data = _get("channels", part="statistics", id=",".join(ids[i:i + 50]))
        for ch in data.get("items", []):
            out[ch.get("id", "")] = _int(
                (ch.get("statistics") or {}).get("subscriberCount"))
    return out


# ------------------------------------------------------------ 수집

def fetch_popular(region: str = "KR", limit: int = 50) -> list[dict]:
    """인기 급상승 중 쇼츠 (1 단위)."""
    data = _get("videos", part="snippet,statistics,contentDetails",
                chart="mostPopular", regionCode=region,
                maxResults=min(limit, 50))
    items = data.get("items", [])
    subs = _channel_subs([(v.get("snippet") or {}).get("channelId", "")
                          for v in items])
    return _rows_from_items(items, subs, "", "popular")


def fetch_keyword(keyword: str, days: int = 7, limit: int = 20) -> list[dict]:
    """키워드별 최근 인기 쇼츠 (search 100 + videos/channels 2 단위)."""
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    found = _get("search", part="snippet", q=keyword, type="video",
                 videoDuration="short", order="viewCount",
                 publishedAfter=after, regionCode="KR",
                 relevanceLanguage="ko", maxResults=min(limit, 50))
    ids = [(i.get("id") or {}).get("videoId", "") for i in found.get("items", [])]
    ids = [i for i in ids if i]
    if not ids:
        return []
    data = _get("videos", part="snippet,statistics,contentDetails",
                id=",".join(ids[:50]))
    items = data.get("items", [])
    subs = _channel_subs([(v.get("snippet") or {}).get("channelId", "")
                          for v in items])
    return _rows_from_items(items, subs, keyword, "search")


def collect(email: str, keywords: list[str] | None = None) -> dict:
    """인기 급상승 + 키워드별 수집 → 저장. 수집 건수 반환."""
    kws = keywords if keywords is not None else [
        k["keyword"] for k in list_keywords(email)]
    rows: list[dict] = []
    errors: list[str] = []
    try:
        rows += fetch_popular()
    except TrendError as e:
        errors.append(str(e))
    for kw in kws[:10]:
        try:
            rows += fetch_keyword(kw)
        except TrendError as e:
            errors.append(f"{kw}: {e}")
            break  # 할당량 소진 등 — 더 시도하지 않음
    saved = save_videos(email, rows)
    return {"collected": len(rows), "saved": saved, "errors": errors}


# ------------------------------------------------------------ 저장·조회

def save_videos(email: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    payload = []
    seen = set()
    for r in rows:
        vid = r.get("video_id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        payload.append({**r, "email": email,
                        "collected_at": datetime.now(KST).isoformat()})
    if not payload:
        return 0
    todos._request("POST", "trend_videos",
                   params={"on_conflict": "email,video_id"},
                   json=payload,
                   headers={"Prefer": "resolution=merge-duplicates"})
    return len(payload)


def list_videos(email: str, keyword: str = "", sort: str = "speed",
                limit: int = 60) -> list[dict]:
    params = {"select": VIDEO_FIELDS + ",tags,description", "email": f"eq.{email}",
              "order": "views.desc", "limit": str(min(limit, 200))}
    if keyword:
        params["keyword"] = f"eq.{keyword}"
    rows = todos._request("GET", "trend_videos", params=params).json()
    for r in rows:
        r["vph"] = views_per_hour(r.get("views", 0), r.get("published_at"))
        r["viral"] = viral_score(r.get("views", 0), r.get("channel_subs", 0))
    if sort == "viral":
        rows.sort(key=lambda r: r["viral"], reverse=True)
    elif sort == "views":
        rows.sort(key=lambda r: r.get("views", 0), reverse=True)
    else:
        rows.sort(key=lambda r: r["vph"], reverse=True)
    return rows


def recent_for_ai(email: str, limit: int = 40) -> list[dict]:
    """AI 분석용 — 최근 수집분 중 속도 상위."""
    rows = list_videos(email, sort="speed", limit=120)
    return rows[:limit]


AUTO_REFRESH_HOURS = 6


def needs_refresh(email: str, hours: int = AUTO_REFRESH_HOURS) -> bool:
    """마지막 수집이 N시간을 넘었으면 (또는 한 번도 없으면) 자동 수집 대상."""
    last = last_collected(email)
    if not last:
        return True
    try:
        when = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=KST)
    return (datetime.now(KST) - when) > timedelta(hours=hours)


def last_collected(email: str) -> str | None:
    rows = todos._request(
        "GET", "trend_videos",
        params={"select": "collected_at", "email": f"eq.{email}",
                "order": "collected_at.desc", "limit": "1"},
    ).json()
    return rows[0]["collected_at"] if rows else None


# ------------------------------------------------------------ 키워드

def list_keywords(email: str) -> list[dict]:
    rows = todos._request(
        "GET", "trend_keywords",
        params={"select": "id,keyword,active", "email": f"eq.{email}",
                "active": "is.true", "order": "created_at.asc", "limit": "30"},
    ).json()
    return rows


def add_keyword(email: str, keyword: str) -> dict:
    keyword = (keyword or "").strip()[:100]
    if not keyword:
        raise StoreError("키워드가 비어 있습니다.")
    resp = todos._request("POST", "trend_keywords",
                          json={"email": email, "keyword": keyword},
                          headers={"Prefer": "return=representation"})
    return resp.json()[0]


def delete_keyword(email: str, keyword_id: int) -> None:
    todos._request("DELETE", "trend_keywords",
                   params={"email": f"eq.{email}", "id": f"eq.{keyword_id}"})


# ------------------------------------------------------------ 리포트·아이디어

def save_report(email: str, kind: str, data: dict) -> dict:
    resp = todos._request("POST", "trend_reports",
                          json={"email": email, "kind": kind, "data": data},
                          headers={"Prefer": "return=representation"})
    return resp.json()[0]


def latest_report(email: str, kind: str) -> dict | None:
    rows = todos._request(
        "GET", "trend_reports",
        params={"select": "id,kind,data,created_at", "email": f"eq.{email}",
                "kind": f"eq.{kind}", "order": "created_at.desc", "limit": "1"},
    ).json()
    return rows[0] if rows else None


# ------------------------------------------------------------ 레퍼런스 URL

OEMBED = {
    "youtube": "https://www.youtube.com/oembed",
    "tiktok": "https://www.tiktok.com/oembed",
}


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    return "other"


def fetch_oembed(url: str) -> dict:
    """공개 oEmbed로 제목·작성자·썸네일만 수집 (인스타는 비공개 → 빈 값)."""
    platform = detect_platform(url)
    endpoint = OEMBED.get(platform)
    if not endpoint:
        return {"platform": platform, "title": "", "author": "", "thumb": ""}
    try:
        resp = requests.get(endpoint, params={"url": url}, timeout=TIMEOUT)
        if resp.status_code >= 400:
            return {"platform": platform, "title": "", "author": "", "thumb": ""}
        data = resp.json()
        return {"platform": platform,
                "title": (data.get("title") or "")[:300],
                "author": (data.get("author_name") or "")[:150],
                "thumb": data.get("thumbnail_url") or ""}
    except (requests.RequestException, ValueError):
        return {"platform": platform, "title": "", "author": "", "thumb": ""}


def save_ref(email: str, url: str, meta: dict, analysis: dict) -> dict:
    resp = todos._request(
        "POST", "trend_refs",
        json={"email": email, "url": url[:500],
              "platform": meta.get("platform", ""),
              "title": meta.get("title", ""), "thumb": meta.get("thumb", ""),
              "analysis": analysis},
        headers={"Prefer": "return=representation"})
    return resp.json()[0]


def list_refs(email: str, limit: int = 30) -> list[dict]:
    return todos._request(
        "GET", "trend_refs",
        params={"select": "id,url,platform,title,thumb,analysis,created_at",
                "email": f"eq.{email}", "order": "created_at.desc",
                "limit": str(limit)},
    ).json()


def delete_ref(email: str, ref_id: int) -> None:
    todos._request("DELETE", "trend_refs",
                   params={"email": f"eq.{email}", "id": f"eq.{ref_id}"})
