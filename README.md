# bid — 나라장터 입찰공고 수집·검색

한국 공공 입찰(나라장터) 공고를 수집하고 검색하는 프로그램. 개발 계획은 [PLAN.md](PLAN.md) 참고.

## 준비

1. [공공데이터포털 나라장터 입찰공고정보서비스](https://www.data.go.kr/data/15129394/openapi.do) 활용신청 → API 키 발급
2. 설치 및 키 설정:

```bash
pip install -r requirements.txt
cp .env.example .env
# .env 파일을 열어 G2B_SERVICE_KEY에 발급받은 키 입력
# (일반 인증키 Decoding 키 권장. Encoding 키를 넣어도 자동 처리됨)
```

## 수집기 사용법

```bash
python -m app collect                      # 최근 1일치 전체(물품/용역/공사/외자) 수집
python -m app collect --days 3             # 최근 3일치
python -m app collect --category 용역 공사   # 특정 업무구분만
python -m app collect --keyword 소프트웨어   # 공고명 키워드 필터
python -m app collect --from 202607010000 --to 202607282359   # 기간 직접 지정
python -m app collect --loop --interval 30 # 30분 간격 반복 수집
python -m app stats                        # 저장된 공고 현황
```

수집 결과는 `data/bid.db`(SQLite)에 저장되며, 공고번호+차수 기준으로 중복 없이 갱신된다.

## 웹 검색 (Vercel 배포)

`api/index.py`는 Vercel 서버리스로 동작하는 실시간 검색 웹이다.
DB 없이 검색 요청마다 나라장터 API를 직접 호출한다.

1. Vercel에서 이 GitHub 리포를 Import (프레임워크: Other, 설정 변경 불필요)
2. 프로젝트 **Settings → Environment Variables**에 `G2B_SERVICE_KEY` 추가 (발급받은 키)
3. Redeploy → 접속하면 키워드/업무구분/기간으로 검색 가능

키 설정 여부는 `https://<배포주소>/health`에서 확인할 수 있다 (`key_set: true`여야 정상).

로컬에서 웹을 띄우려면:

```bash
pip install fastapi uvicorn
uvicorn api.index:app --reload
# http://127.0.0.1:8000 접속
```

## 테스트

```bash
python -m unittest discover tests -v
```

## 참고: 발급 직후 키가 안 될 때

공공데이터포털 키는 활용신청 승인 후 실제 반영까지 **최대 1시간 정도** 걸릴 수 있다.
`SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가 나오면 잠시 후 다시 시도.
