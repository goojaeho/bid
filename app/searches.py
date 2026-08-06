"""사용자별 빠른 검색 저장소 (Supabase quick_searches 테이블).

입찰(bid)/정부과제(gov) 검색 조건을 이름 붙여 저장하고
칩 버튼으로 원클릭 재검색한다.
"""
from __future__ import annotations

from app import todos
from app.store import StoreError

MAX_LABEL = 30
MAX_PER_PAGE = 20

# 페이지별 저장 허용 검색 파라미터 (그 외 키는 버린다)
ALLOWED_PARAMS = {
    "bid": {"q", "cat", "days", "org", "min_amt", "max_amt", "sort"},
    "gov": {"q", "src", "region", "state", "sort"},
}


def _clean_params(page: str, params: dict) -> dict:
    allowed = ALLOWED_PARAMS[page]
    return {k: str(v).strip()[:200] for k, v in params.items()
            if k in allowed and str(v).strip()}


def list_searches(email: str, page: str) -> list[dict]:
    if page not in ALLOWED_PARAMS:
        raise StoreError("잘못된 페이지입니다.")
    return todos._request(
        "GET", "quick_searches",
        params={"select": "id,label,params", "email": f"eq.{email}",
                "page": f"eq.{page}", "order": "created_at.asc",
                "limit": str(MAX_PER_PAGE)},
    ).json()


def add_search(email: str, page: str, label: str, params: dict) -> dict:
    if page not in ALLOWED_PARAMS:
        raise StoreError("잘못된 페이지입니다.")
    label = (label or "").strip()[:MAX_LABEL]
    if not label:
        raise StoreError("빠른 검색 이름을 입력해주세요.")
    cleaned = _clean_params(page, params if isinstance(params, dict) else {})
    if not cleaned:
        raise StoreError("저장할 검색 조건이 없습니다.")
    if len(list_searches(email, page)) >= MAX_PER_PAGE:
        raise StoreError(f"빠른 검색은 페이지당 최대 {MAX_PER_PAGE}개까지 저장할 수 있습니다.")
    resp = todos._request(
        "POST", "quick_searches",
        json={"email": email, "page": page, "label": label, "params": cleaned},
        headers={"Prefer": "return=representation"},
    )
    rows = resp.json()
    return rows[0] if rows else {"label": label, "params": cleaned}


def delete_search(email: str, search_id: int) -> None:
    todos._request("DELETE", "quick_searches",
                   params={"email": f"eq.{email}", "id": f"eq.{search_id}"})
