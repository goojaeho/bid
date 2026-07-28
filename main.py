"""Vercel 진입점 폴백. 실제 앱은 api/index.py에 있다."""
from api.index import app

__all__ = ["app"]
