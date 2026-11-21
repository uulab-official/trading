# 토스 토큰 재사용·KIS 모의주문 준비 검증 — 2026-11-21

기준 원격 커밋은 `fa2c7d06dada92dcf7b747939ba40be8f916941a`다.
이번 변경은 토스 읽기 전용 수집기의 인증 재사용과 KIS VTS 지정가 주문의
오프라인 준비·응답 분류를 추가한다. 실제 증권사 인증이나 주문 전송 결과를
뜻하지 않는다.

## 이번에 완료한 기능

### 토스

- `TOSS_ACCESS_TOKEN` 또는 함수의 `access_token`을 사용하면 시세 GET만
  한 번 요청한다. client ID/secret과 발급 옵션이 함께 있어도 기존 토큰이
  우선한다.
- 새 토큰 발급은 `--issue-token` / `issue_token=True`로 명시해야 한다.
  기존 client ID/secret만 설정한 실행은 이제 네트워크 요청 전에 거부된다.
- 잘못된 기존 토큰, HTTP 401/403/429/500 및 잘못된 응답은 자동 재발급이나
  재시도를 유발하지 않는다. 디스크 토큰 캐시는 만들지 않는다.
- 토큰·요청·관측 시각을 HTTP 전에 검사하며, 환경 프록시와 리다이렉트를
  차단한다. 인증 정보는 결과 JSON이나 CLI 오류에 출력하지 않는다.
- 기존 파일·심볼릭 링크·끊어진 링크와, 경로 상위에 일반 파일이 있는
  경우를 HTTP 전에 거부한다. 존재하는 상위 경로가 디렉터리인지 확인하는
  검사는 저장 권한이나 이후 파일시스템 변경을 보장하지 않는다.

공식 FAQ는 클라이언트마다 유효한 토큰이 하나이며, 새 발급 시 이전 토큰이
즉시 무효화된다고 설명한다. 이번 변경은 반복 수집 과정에서 의도하지 않은
토큰 교체를 피하기 위한 것이다.
[Toss FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)

### KIS VTS

- 국내 6자리 종목코드, KRX, KRW, 정수 주식 수량, 정수 지정가, 명시적인
  건별 주문금액 상한으로 요청 양식을 만든다. 금액은 정수 연산으로 검사한다.
- 현재 공식 예시의 모의 매수 `VTTC0012U`, 매도 `VTTC0011U`와 현금주문
  경로를 사용한다. 계좌·토큰·앱 키는 환경변수 이름이 적힌 자리표시자다.
- 생성기는 환경변수 값을 읽지 않고 HTTP 전송 기능도 갖지 않는다. 기존
  현금·보유 수량·전략 커서·주문 의도 원장을 바꾸지 않는다.
- 응답 분류기는 접수 응답, 전달받은 업무 실패, 결과 불명을 구분한다.
  모든 결과의 체결 상태는 `unknown`, `verified_fill=false`,
  `safe_to_retry=false`이며 인증된 증거라는 의미를 부여하지 않는다.
- 로컬 client ID는 검토용 연결 표식이다. 증권사 중복 방지 키나 영속적인
  주문 예약이 아니다. 주문금액 상한에는 수수료·세금·다른 주문이 포함되지
  않는다.

[KIS 공식 현금주문 예시](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/order_cash.py)
및 [운영 가이드](KIS_VTS_ORDER_PLAN.md)에 요청 규격과 범위를 기록했다.

## 실제 로컬 실행 결과

Linux / Python 3.12.14에서 필요한 패키지를 설치한 뒤 실행했다.

| 검증 | 최종 관측 결과 |
| --- | --- |
| 전체 pytest 및 core/data/strategy/backtest 커버리지 XML 생성 | 549 passed; 753 subtests passed |
| 네트워크·DNS 차단 오프라인 실행기, 백테스트 포함 | 539 tests, OK |
| 토스 수집기 개별 unittest | 30 tests, OK |
| KIS 코어·실제 CLI 개별 unittest | 25 + 10 tests, OK |
| compileall: packages, apps/server, tools | 종료 코드 0 |
| fatal flake8: E9,F63,F7,F82 | 오류 0개 |
| git diff --check | 오류 없음 |

위 테스트 집합은 서로 겹치므로 건수를 합쳐 고유 테스트 수로 해석하지 않는다.
기존 `datetime.utcnow()` 등에서 발생하는 deprecation 경고 3,318건은 남아
있다. 경고를 숨기거나 실패를 무시하는 설정은 추가하지 않았다.

KIS CLI 테스트는 실제 별도 Python 프로세스를 `-B -S`로 실행하고
소켓·DNS를 차단한다. 테스트용 인증·프록시 환경변수를 넣어도 출력에는
자리표시자만 남는다. 기존 출력·끊어진 링크·쓰기 실패와 파일 교체 시의
보존 동작도 확인했다.

독립 리뷰는 새 변경에 중대한 문제를 발견하지 않았다. 추가로 발견한 기존
토스의 비디렉터리 상위 경로 문제는 회귀 테스트의 실패를 먼저 확인하고
수정했다. 최종 전체 테스트 결과는 그 수정까지 포함한다.

## 합성 데이터 연결 실험

실제 소켓과 DNS를 차단하고, 저장소의 합성 55개 봉을 Toss 응답 형식의
가짜 HTTP 전송에 넣었다. 수집기 → 정규화 → 전략 재생 → 동일 자료 재실행
→ SQLite 원장 감사를 수행했다. 테스트용 토큰만 사용했다.

| 항목 | KRW | USD |
| --- | --- | --- |
| 예시 종목 | 005930 | AAPL |
| 합성 봉 | 55 | 55 |
| 가짜 시세 GET | 1 | 1 |
| OAuth 발급 요청 | 0 | 0 |
| 가상 체결 | 2 | 2 |
| 재실행 추가 체결 | 0 | 0 |
| 원장 감사 | 통과 | 통과 |

USD 가격은 같은 합성 OHLC를 400으로 나눈 값이다. 실제 삼성전자·애플
시세나 수익률을 의미하지 않는다. 초기 현금은 KRW 1,000,000 / USD 10,000,
전략은 fast=5, slow=20, 배분 20%, 가상 수수료 0.1%였다.

KRW 가상 체결의 종목·방향·수량·정수 가격으로 두 요청 양식을 생성했다.
이는 형식 연결 실험이며 자동 주문 서비스나 원장 연동을 추가한 것은 아니다.
가격은 합성 값이고 **실제 호가 단위에 맞는지 검증하지 않았다**.

- [합성 매수 요청 양식](examples/kis_vts_synthetic_buy_plan.json)
- [합성 매도 요청 양식](examples/kis_vts_synthetic_sell_plan.json)

KIS 국내주식 준비기에 USD를 넘기면 거부되는 동작도 확인했다.
전체 실험에서 실제 증권사 요청과 주문은 각각 0건이다.

## 재현 명령

```sh
python -m pytest packages/ apps/server/tests/ -q \
  --cov=trading_core --cov=trading_data --cov=trading_strategy \
  --cov=trading_backtest --cov-report=xml
python tools/offline_checks.py --include-backtest
python -m compileall -q packages/ apps/server/ tools/
python -m flake8 packages/ apps/ tools/ --count \
  --select=E9,F63,F7,F82 --show-source --statistics

mkdir -p data
python tools/kis_vts_order_plan.py --mode paper \
  --client-order-id demo-005930-buy-001 --symbol 005930 --side buy \
  --quantity 1 --limit-price 70000 --max-notional 100000 \
  --output data/kis-vts-buy-plan.json
```

## 원격 검증과 남은 연결 단계

이 문서는 커밋 전 로컬 검증 기록이다. 동일 소스가 반영된 정확한 커밋의
Python 3.10/3.11/3.12, lint, Docker 결과는
[GitHub Actions](https://github.com/uulab-official/trading/actions/workflows/ci.yml)에서
별도로 확인한다. 로컬 통과를 원격 CI 통과로 대신하지 않는다.

Toss 인증 시세와 실제 자료 대조, KIS VTS 계좌 연결 및 모의 주문의 실제
접수·부분 체결·취소·잔고 대조는 수행하지 않았다. 이 단계에는 별도 인증
정보와 계좌에 연결된 영속적인 주문·체결 처리 및 상태 조회가 필요하다.
KRX/NXT 통합 시세와 KRX 주문 시장의 차이, 호가 단위·장 시간·세금·매수 가능
금액 등의 검증도 해당 단계에 남아 있다.
