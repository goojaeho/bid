"""환경설정 로딩. .env 파일 또는 환경변수에서 읽는다."""
import os
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def get_service_key() -> str:
    key = os.environ.get("G2B_SERVICE_KEY", "").strip()
    if not key or key == "여기에_발급받은_키":
        raise SystemExit(
            "G2B_SERVICE_KEY가 설정되지 않았습니다. "
            ".env.example을 .env로 복사하고 공공데이터포털에서 발급받은 키를 넣어주세요."
        )
    # 공공데이터포털의 'Encoding 키'(%가 포함됨)를 넣은 경우 디코딩해서 사용.
    # requests가 파라미터를 다시 인코딩하므로 원본(Decoding) 키가 필요하다.
    if "%" in key:
        key = unquote(key)
    return key


def get_db_path() -> Path:
    raw = os.environ.get("BID_DB_PATH", "data/bid.db")
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
