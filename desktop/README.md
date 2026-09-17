# 자비스 Windows 0.2

실행: dist/v0.2.0/win-unpacked/Jarvis.exe → Google로 로그인 → 브라우저에서 본인 계정 선택. 로그인 완료 후 브라우저를 닫아도 됩니다. 확장 기능과 연결 코드는 필요 없습니다.

로그인 정보는 Windows DPAPI로 암호화해 앱 데이터 폴더에 저장하며 30일 후 재로그인합니다. 로그아웃은 이 기기의 저장된 인증을 삭제합니다. 원격 기기별 인증 폐기는 아직 없습니다.

창을 닫으면 트레이에서 유지합니다. 종료는 트레이 메뉴를 사용하세요. 고정 폴더에 두고 설정에서 Windows 로그인 시 시작을 켤 수 있습니다. 입력칸 선택 후 Win+H로 받아쓰고 Enter로 보냅니다.

현재 기능: 기존 사이트 할 일 등록과 브리핑, 안내 중지/재개. 상시 호출, 메일 수신, 통화 분석, Calendar 동기화는 후속 단계입니다. 자연스러운 여성 음성은 Mac 서버 준비 후 연결합니다.

Mac 음성 규격 초안: POST {text, language: ko} → audio 응답. 30초, 최대 20MB. 실제 규격을 전달받으면 맞춥니다.

인증: 기본 브라우저 Google OAuth, 임시 loopback 포트, 난수 state, PKCE. 기존 Google callback을 사용하므로 새 리디렉션 URI 등록은 필요 없습니다. 목적별 서명으로 브라우저 쿠키와 앱 토큰을 분리하고 매 요청 소유자 권한을 검사합니다. state 5분, grant 60초, access 30일입니다. Stateless grant의 동일 verifier 재교환을 TTL 안에서 별도 DB로 차단하지는 않습니다. Access token은 URL과 렌더러에 노출하지 않습니다.

검증: Python 전체 테스트, npm test, node smoke.cjs --packaged. Google 및 저장소 교환은 모의 테스트하며 실제 소유자 로그인은 사용자가 마지막으로 확인해야 합니다.
