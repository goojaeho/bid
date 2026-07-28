"""나라장터 입찰공고정보서비스 OpenAPI 클라이언트.

공공데이터포털: https://www.data.go.kr/data/15129394/openapi.do
업무구분(물품/용역/공사/외자)별로 오퍼레이션이 나뉘어 있어 각각 호출해야 한다.
"""
from __future__ import annotations

import re
import time
from typing import Iterator

import requests

BASE_URL = "http://apis.data.go.kr/1230000/ad/BidPublicInfoService"

# 업무구분 → 오퍼레이션 (PPSSrch: 나라장터 검색조건 조회)
OPERATIONS = {
    "물품": "getBidPblancListInfoThngPPSSrch",
    "용역": "getBidPblancListInfoServcPPSSrch",
    "공사": "getBidPblancListInfoCnstwkPPSSrch",
    "외자": "getBidPblancListInfoFrgcptPPSSrch",
}

CATEGORIES = list(OPERATIONS)

# 페이지당 최대 조회 건수 (API 최대 999)
PAGE_SIZE = 500

# 호출 간 대기(초) — 트래픽 한도/서버 부하 보호
REQUEST_INTERVAL = 0.3


class G2BApiError(Exception):
    """API가 오류 응답을 반환한 경우."""


class G2BClient:
    def __init__(self, service_key: str, session: requests.Session | None = None):
        self.service_key = service_key
        self.session = session or requests.Session()

    def fetch_page(
        self,
        category: str,
        inqry_bgn_dt: str,
        inqry_end_dt: str,
        page_no: int = 1,
        num_of_rows: int = PAGE_SIZE,
        bid_ntce_nm: str | None = None,
        timeout: int = 30,
    ) -> dict:
        """한 페이지 조회. 반환: {"total_count": int, "items": [dict, ...]}

        inqry_bgn_dt/inqry_end_dt: YYYYMMDDHHMM 형식 (공고게시일시 기준, inqryDiv=1)
        """
        if category not in OPERATIONS:
            raise ValueError(f"알 수 없는 업무구분: {category} (가능: {CATEGORIES})")

        params = {
            "serviceKey": self.service_key,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "inqryDiv": "1",  # 1: 공고게시일시 기준
            "inqryBgnDt": inqry_bgn_dt,
            "inqryEndDt": inqry_end_dt,
            "type": "json",
        }
        if bid_ntce_nm:
            params["bidNtceNm"] = bid_ntce_nm

        url = f"{BASE_URL}/{OPERATIONS[category]}"
        resp = self.session.get(url, params=params, timeout=timeout)
        if resp.status_code == 401:
            raise G2BApiError(
                "인증 실패(401). G2B_SERVICE_KEY를 확인하세요. "
                "발급 직후라면 키 반영까지 최대 1시간 걸릴 수 있습니다."
            )
        resp.raise_for_status()
        return self._parse_response(resp.text)

    def iter_notices(
        self,
        category: str,
        inqry_bgn_dt: str,
        inqry_end_dt: str,
        bid_ntce_nm: str | None = None,
    ) -> Iterator[dict]:
        """기간 내 공고를 페이지네이션을 따라가며 전부 순회한다."""
        page_no = 1
        while True:
            result = self.fetch_page(
                category, inqry_bgn_dt, inqry_end_dt,
                page_no=page_no, bid_ntce_nm=bid_ntce_nm,
            )
            items = result["items"]
            yield from items
            if page_no * PAGE_SIZE >= result["total_count"] or not items:
                break
            page_no += 1
            time.sleep(REQUEST_INTERVAL)

    @staticmethod
    def _parse_response(text: str) -> dict:
        text = text.strip()
        # 인증 오류 등 게이트웨이 단 오류는 type=json이어도 XML로 내려온다.
        if text.startswith("<"):
            msg = _extract_xml_error(text)
            raise G2BApiError(f"API 오류 응답: {msg}")

        import json

        data = json.loads(text)
        response = data.get("response", {})
        header = response.get("header", {})
        code = header.get("resultCode")
        if code not in ("00", "0"):
            raise G2BApiError(
                f"API 오류 (resultCode={code}): {header.get('resultMsg')}"
            )
        body = response.get("body", {}) or {}
        items = body.get("items") or []
        # 결과 1건이면 dict로 내려오는 경우가 있어 list로 정규화
        if isinstance(items, dict):
            items = items.get("item") or items
            if isinstance(items, dict):
                items = [items]
        return {
            "total_count": int(body.get("totalCount") or 0),
            "items": items,
        }


def _extract_xml_error(text: str) -> str:
    for tag in ("returnAuthMsg", "returnReasonCode", "errMsg", "resultMsg"):
        m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
        if m:
            return f"{tag}={m.group(1)}"
    return text[:200]
