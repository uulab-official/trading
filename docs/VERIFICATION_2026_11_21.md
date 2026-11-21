# 모의매매 검증 결과 — 2026-11-21

이 기록은 `9457f790f04ee24b802ca1a7c85f69801cab2ab5`의 소스 224개를
Git blob 해시로 확인한 뒤 수행한 로컬 수정·실행 결과다. 토스·KIS 실제
계좌 수익이나 인증된 증권사 주문 결과를 뜻하지 않는다.

## 실제 실행 결과

환경: Linux, Python 3.12.14. core/data/strategy/backtest/monitor/server의
editable 패키지와 pytest, pytest-asyncio, pytest-cov, flake8을 설치했다.

| 검증 | 관측 결과 |
| --- | --- |
| 전체 `pytest packages/ apps/server/tests/` 및 4개 모듈 커버리지 생성 | 489 passed; 450 subtests passed |
| `python tools/offline_checks.py --include-backtest` | 479 tests, OK |
| `python tools/stock_paper_smoke.py` | 55개 합성 봉, 체결 2건, 중복 0건, 감사·백업·HTML 생성 성공 |
| `python -m compileall -q packages/ apps/server/ tools/` | 종료 코드 0 |
| `python -m flake8 packages/ apps/ tools/ --count --select=E9,F63,F7,F82 --show-source --statistics` | 오류 0개 |
| `git diff --check` | 오류 없음 |

전체 pytest와 오프라인 실행기는 포함하는 테스트가 다르므로 두 건수를
더한 값이 고유 테스트 수는 아니다. pytest에는 기존 `datetime.utcnow()`
사용 및 테스트 플러그인의 deprecation 경고 3,318건이 남아 있다. 경고를
숨기거나 테스트 실패를 무시하는 설정은 추가하지 않았다.

GitHub Actions의 Python 3.10/3.11/3.12 및 Docker 결과는
[해당 커밋의 실행 기록](https://github.com/uulab-official/trading/actions/workflows/ci.yml)
에서 별도로 확인한다. 이 문서의 로컬 통과 기록이 원격 CI 통과를 대신하지 않는다.

## 재현한 문제와 수정

1. **KIS 조회 모듈의 문법 오류:** 실제 줄바꿈 대신 들어간 두 `\\n` 때문에
   pytest 수집이 중단됐다. 두 곳을 고치고 CI의 초기 문법 검사 및 fatal
   flake8 범위에 `tools/`를 포함했다.
2. **불완전한 백업 검증:** SQLite WAL 변경이 주 데이터베이스 해시에
   포함되지 않아 손상을 놓쳤다. 백업 사본은 `DELETE` journal mode로
   완결하고, 검증 시 WAL/SHM/journal 별도 파일이 있으면 거부한다.
3. **과거 체결 누락을 자동 복구하던 재생:** 삭제된 과거 체결을 신규
   체결처럼 다시 만들 수 있었다. 저장된 봉 커서에 해당하는 체결을
   쓰기 트랜잭션 안에서 재계산하고 기존 기록과 정확히 대조한다.
4. **읽기 전용 주문 의도 원장의 쓰기 허용:** 읽기 전용 인스턴스의
   세 변경 메서드를 연결 전에 차단했다. 조회 메서드도 읽기 전용
   연결을 사용한다.
5. **주문 수량의 Decimal 문맥 의존:** 전역 정밀도가 낮으면 수량이
   반올림되어 서로 다른 요청이 같은 요청으로 처리됐다. 입력의 모든
   자릿수를 보존하는 문맥 독립 직렬화로 바꿨다.
6. **토스 국내 15:30 집계봉:** 공식 FAQ상 일부 종목에서 16:00까지의
   체결이 포함될 수 있다. 고정 1분봉으로 재생하면 미래 체결을 일찍
   사용할 수 있어 KRW의 source-close 15:30 행을 항상 제외한다.
7. **오래된 테스트 기대값:** 수수료 `22`와 `22.0`을 같은 정확한 금액으로
   비교하고, CI 테스트는 명령 전체 문자열 대신 실제 인자와 실패 전파를
   검사하도록 수정했다. 기존 서버 테스트 범위를 줄이지 않았다.

재시작 검증은 실제 SQLite로 수행했다. 동시 append의 단일 체결,
커서 쓰기 오류 시 체결·커서 동시 롤백, 커서 UPDATE 직후 COMMIT 전에
자식 프로세스를 `os._exit(73)`으로 종료한 뒤의 복구를 포함한다.
파일시스템 전원 장애, 네트워크 파일시스템, 실제 운영 복구시간까지
검증한 것은 아니다.

## KRW·USD 전체 흐름 실험

두 경우 모두 네트워크 연결과 DNS 함수를 거부한 상태에서 전략 실행 →
동일 입력 재실행 → 원장 감사 → 별도 백업 → 백업 재생 검증 → 위험도
계산 → 정적 HTML 생성을 실행했다.

| 항목 | KRW 모의 원장 | USD 모의 원장 |
| --- | --- | --- |
| 예시 종목 | 005930 | AAPL |
| 초기 현금 | 1,000,000 KRW | 10,000 USD |
| 합성 1분봉 | 55 | 55 |
| 첫 실행 체결 | 2 | 2 |
| 동일 입력 재실행 추가 체결 | 0 | 0 |
| 마지막 가상 평가금액 | 1,001,030.948 KRW | 10,011.598165 USD |
| 종료 보유 수량 | 0 | 0 |
| 백업 경제 재생 검증 | 통과 | 통과 |

KRW 입력은 저장소의 `synthetic_toss_stock_paper_1m.json`이다. USD는
같은 합성 가격 경로의 OHLC를 400으로 나누고 통화와 예시 종목을 바꾼
검증용 자료다. **실제 삼성전자·애플 가격이나 투자 성과가 아니다.**
전략 옵션은 fast=5, slow=20, 배분 20%, 가상 수수료 0.1%다.

생성 예시: [KRW 보고서](examples/krw_stock_paper.html),
[USD 보고서](examples/usd_stock_paper.html).
두 HTML의 합성 데이터 표시, 스크립트/외부 리소스 부재, CSP를 검사했다.
브라우저 화면 검사는 실행 환경의 로컬 브라우저 및 파일 URL 제한으로
완료하지 못했으므로 시각적 검증 통과를 주장하지 않는다.

## 기존 자료를 보존하는 업그레이드

- 과거 기록이 누락된 전략 DB는 이제 재생이 거부된다. 기존 DB를
  보존하고 신뢰할 수 있는 별도 백업·기록과 대조한다.
- WAL/SHM 파일을 포함한 예전 백업은 별도 파일을 지워 통과시키지 않는다.
  감사가 끝난 원본에서 새 백업을 만든다.
- 이전 버전에서 이미 반올림한 주문 수량은 원장만으로 복구할 수 없다.
  저장된 수량과 원래 요청을 구분해 확인한다.
- 15:30 집계봉을 포함한 기존 Toss 정규화 자료는 덮어쓰지 않는다.
  새 자료·새 모의 DB에서 제외 정책의 영향을 비교한다.

## 인증이 있어야 진행 가능한 단계

현재 실행 환경에는 Toss client ID/secret/access token 및 KIS VTS
access token/appkey/appsecret이 설정되어 있지 않았다. 값은 출력하지
않고 존재 여부만 확인했다. OAuth 발급, 실제 시세 조회, 계좌 조회,
모의 주문 제출 및 실제 주문 제출은 수행하지 않았다.

- Toss: 공식 IP 허용 설정과 로컬 인증 정보를 준비한 뒤 읽기 전용
  시세 응답을 실제 시장 자료와 대조해야 한다. 현재 수집기는 매 호출
  토큰을 발급하므로 반복 운영 전에 토큰 공유·재사용 정책이 필요하다.
- KIS: 현재 구현은 VTS 호스트의 GET 현재가 조회다. 공식 모의 주문,
  주문 조회·잔고 대조·부분 체결 처리는 아직 구현/실계정 검증 범위 밖이다.
- 장 달력, 수정주가/분할, 실제 세금·수수료·호가 단위 및 연속 운영
  검증도 별도 단계다.

공식 근거: [Toss OpenAPI](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json),
[Toss FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md),
[KIS 공식 API](https://apiportal.koreainvestment.com/apiservice).
