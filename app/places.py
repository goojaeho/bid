"""내 맛집 저장소 (Supabase places 테이블).

status: 'wish'(가고싶은 곳) | 'visited'(가봤던 곳 — 별점·메뉴·한줄평 기록)
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import todos
from app.store import StoreError

KST = ZoneInfo("Asia/Seoul")
FIELDS = ("id,name,address,lat,lng,category,phone,place_url,status,"
          "rating,menu,note,visited_at,created_at")


def list_places(email: str) -> list[dict]:
    return todos._request(
        "GET", "places",
        params={"select": FIELDS, "email": f"eq.{email}",
                "order": "created_at.desc", "limit": "500"},
    ).json()


def add_place(email: str, data: dict) -> dict:
    name = str(data.get("name") or "").strip()[:200]
    if not name:
        raise StoreError("가게 이름이 비어 있습니다.")

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    row = {
        "email": email,
        "name": name,
        "address": str(data.get("address") or "").strip()[:300],
        "lat": num(data.get("lat")),
        "lng": num(data.get("lng")),
        "category": str(data.get("category") or "").strip()[:100],
        "phone": str(data.get("phone") or "").strip()[:50],
        "place_url": str(data.get("place_url") or "").strip()[:500],
        "status": "wish",
    }
    # 같은 이름+주소가 이미 있으면 중복 저장 방지
    dup = todos._request(
        "GET", "places",
        params={"select": "id", "email": f"eq.{email}",
                "name": f"eq.{row['name']}", "address": f"eq.{row['address']}"},
    ).json()
    if dup:
        raise StoreError("이미 저장된 맛집입니다.")
    resp = todos._request("POST", "places", json=row,
                          headers={"Prefer": "return=representation"})
    return resp.json()[0]


def update_place(email: str, place_id: int, fields: dict) -> None:
    allowed: dict = {}
    if fields.get("status") in ("wish", "visited"):
        allowed["status"] = fields["status"]
        if fields["status"] == "visited":
            allowed["visited_at"] = datetime.now(KST).date().isoformat()
        else:
            allowed["visited_at"] = None
    if "rating" in fields:
        try:
            r = int(fields["rating"])
            allowed["rating"] = r if 1 <= r <= 5 else None
        except (TypeError, ValueError):
            pass
    if "menu" in fields:
        allowed["menu"] = str(fields["menu"] or "").strip()[:500]
    if "note" in fields:
        allowed["note"] = str(fields["note"] or "").strip()[:1000]
    if not allowed:
        raise StoreError("수정할 내용이 없습니다.")
    todos._request("PATCH", "places",
                   params={"email": f"eq.{email}", "id": f"eq.{place_id}"},
                   json=allowed)


def delete_place(email: str, place_id: int) -> None:
    todos._request("DELETE", "places",
                   params={"email": f"eq.{email}", "id": f"eq.{place_id}"})
