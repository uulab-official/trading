# 전체 모의 실험실 검증 기록 — 2026-11-21

## 이번 변경의 결과

기존 코인, KRW 주식, USD 주식, 주문 생명주기와 주문 관측값 대조·복원 실험을
`python tools/paper_lab.py` 한 명령으로 연결했습니다. 각 엔진의 회계·재생 감사·
백업 기능을 재사용하며, 실제 증권사 요청과 통화 합산은 없습니다.

별도 설정 진단은 허용된 토스·KIS 환경변수의 존재만 확인합니다. 이 실행에서
8개 변수는 모두 없었고, 네 가지 기능 모두 `configuration_present: false`,
`authenticated: false`였습니다. 비밀값은 읽기 결과나 보고서에 포함하지 않았습니다.

기준 리비전은 `b4e2bad2de6e215c227ded8bb69b3c950abc1bd7`이며, 이 문서는 그 이후
변경의 로컬 검증 기록입니다. 새 커밋의 원격 상태는 해당 정확한 SHA의
[GitHub Actions 실행](https://github.com/uulab-official/trading/actions/workflows/ci.yml)을
기준으로 확인합니다. 로컬 검증 성공을 원격 CI 성공으로 간주하지 않습니다.

## 실제 실행 증거

보존한 통합 실행의 기준 시각은 **2026-11-21 21:26:29 KST**입니다.
UTC 값은 `2026-11-21T12:26:29.840828+00:00`입니다.

| 항목 | 확인된 결과 |
| --- | --- |
| 전체 상태 | `synthetic_paper_lab_verified` |
| 실제 증권사 요청 / 차단된 Python 네트워크 시도 | 0건 / 0건 |
| 코인 | 합성 BTC/USDT 90개 시간봉, 가상 체결 2건; 30개 봉 중단·재시작·동일 입력 중복 방지 |
| 국내 주식 | 합성 `005930`, KRW 55개 분봉, 가상 체결 2건 |
| 미국 주식 | 가상 종목 `DEMO`, USD 55개 분봉, 가상 체결 2건 |
| 원장 감사·백업 | 세 원장별 독립 감사와 깊은 백업 재생 완료 |
| 주문 생명주기 | 합성 이벤트 14개, 고유 체결 4건, 결과 불명·부분 체결·취소 경합·중복 전달 점검 완료 |
| 주문 대조 | 일치하는 주문 2개, 의도적으로 바꾼 체결 수량·대금 두 항목의 차이 발견 |
| 주문 복원 | 새 후보 DB의 전체 상태 동일, 전체 이벤트 재전달 후 추가 체결 없음 |
| 내보낸 예제 | JSON/HTML 19개, HTML 7개 모두 합성 표시와 CSP 확인, 내부 링크 10개 모두 유효 |

코인 합성 시나리오에서는 `daily_loss_halted: true`, `drawdown_halted: false`가
기록됐습니다. 실험 완료는 위험 제한 없이 신규 거래를 허용한다는 뜻이 아닙니다.
이 값도 [코인 감사 예제](examples/paper_lab/crypto/audit.json)에 그대로 남겼습니다.

주식 원장의 합성 현금·평가금액은 각각 `1001030.948` KRW, `10011.598165` USD,
보유 수량은 둘 다 빈 값입니다. 두 금액을 합산하거나 환전하지 않았습니다.
USD 입력의 가격 비율 `400`은 고정 테스트 스케일이며 실제 환율·시장 가격이
아닙니다. 주문 실험의 현금 순변화 `5285`는 별도 합성 주문 시나리오의 결과이며
위 주식 전략 잔고에 더하지 않았습니다.

## 테스트와 도구 검증

Python 3.12.14 로컬 환경에서 실제 실행했습니다.

| 검사 | 결과 |
| --- | --- |
| 전체 pytest + 4개 패키지 coverage.xml 생성 | **741 passed, 1501 subtests passed**, 34.93초 |
| 확장 네트워크·DNS 금지 검증 | **731 tests, OK**, 27.101초 |
| 수정 후 실험실·주문 보고서·통합 HTML 회귀 묶음 | **31 passed, 24 subtests passed** |
| 독립 설정 진단 | `python -S -m unittest` 18개 통과 |
| 필수 flake8 `E9,F63,F7,F82` | 0건 |
| compileall, `git diff --check` | 통과 |
| 실제 CLI 보존 실행 | 종료 코드 0, 출력 파일·원장·백업·복원 후보 확인 |

실행한 전체 명령:

```bash
python -m pytest packages/ apps/server/tests/ -q \
  --cov=trading_core --cov=trading_data \
  --cov=trading_strategy --cov=trading_backtest --cov-report=xml
python tools/offline_checks.py --include-backtest
python -m compileall -q packages/ apps/server/ tools/
python -m flake8 packages/ apps/ tools/ --count \
  --select=E9,F63,F7,F82 --show-source --statistics
git diff --check
```

로컬 pytest에는 기존 `datetime.utcnow()` 및 설치된 pytest 플러그인의 사용 중단
경고 6,924건이 기록됐습니다. 이번 작업은 해당 레거시 동작이나 저장소 전체
Black/isort 포맷을 바꾸지 않았습니다. 필수 lint 성공을 전체 포맷 정리 완료로
해석하지 않습니다.

## 독립 리뷰와 수정

별도 리뷰어가 구현·테스트·실제 결과를 읽고 네 가지 보완점을 재현했습니다.
모두 실패하는 회귀 사례를 먼저 확인한 뒤 수정했고, 이후 전체 검증을 재실행했습니다.

| 발견한 문제 | 수정과 검증 |
| --- | --- |
| 통합 원장 HTML이 합성 입력 표시를 누락 | 실험실 provenance가 있으면 별도 합성 가격·수익률 안내 표시; 생성한 HTML 전체 검사 |
| 역방향 DNS `getnameinfo`가 보호 대상에서 누락 | 차단 대상에 추가, 실행 거절·시도 횟수·원래 함수 복원 확인 |
| HTML 생성 중 잡아서 숨긴 통신 시도가 최종 성공으로 표시될 수 있음 | 모든 렌더링·직렬화 뒤, 성공 파일 저장 전에 시도 횟수 재확인 |
| 보고서 파일 close 오류 후 성공 파일이 남음 | close도 묶음 실패 정리 범위에 포함, 이미 닫은 FD를 재시도하지 않고 나머지 FD 정리; 첫·마지막 close 오류 모두 회귀 검증 |

마지막 수정은 기존 주문 보고서의 공용 파일 저장 함수에도 적용했습니다.
닫기 전에는 FD로 파일을 고정하고, 이미 닫은 파일의 실패 정리에서는 최종
크기·변경 시각·장치·inode를 함께 확인해 교체된 파일을 보존합니다. 강제 종료나
전원 장애에 대한 여러 파일의 원자적 저장을 보장하는 변경은 아닙니다.

추가로 임시 실험이 끝난 뒤 하위 실험 요약의 `artifacts_retained`가 true로
남던 표시를 false로 맞췄습니다. 리뷰에서 미뤄 둔 중요·경미 항목은 없습니다.
HTML 검증은 파싱, 링크 해석, CSP·합성 표시·문자 이스케이프와 실제 파일
생성 기준이며 브라우저 화면 검증을 수행했다고 주장하지 않습니다.

## CI 커버리지 산출물

기존 테스트·Docker 통과 조건을 유지하고, 테스트 후 `coverage.xml`을
`coverage-python-<version>-attempt-<attempt>` 이름으로 저장하는 필수 Actions
단계를 추가했습니다. 공식 `actions/upload-artifact@v7.0.2`의 입력과 Node 24
실행 정보를 확인했습니다.

Codecov는 `CODECOV_UPLOAD_ENABLED == 'true'`일 때만 기존 업로더를 실행합니다.
설정하지 않은 기본 상태에서는 외부 전송을 건너뜁니다. 기존 업로더의 버전·인자·
실패 경고 처리는 유지하며, 서비스 계정이나 인증 정보를 설정하지 않았습니다.
원격 실행에서는 버전별 산출물 세 개, Codecov 조건, Python 3.10/3.11/3.12,
필수 lint 및 Docker 결과를 해당 커밋에 대해 별도로 확인합니다.

공식 근거:

- [Upload Artifact v7.0.2 릴리스](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)
- [해당 버전의 action.yml](https://github.com/actions/upload-artifact/blob/v7.0.2/action.yml)
- [GitHub Actions 컨텍스트](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)

## 남아 있는 인증 의존 단계

현재 환경변수 진단만으로는 실제 토스 OAuth·IP 허용·시세 권한, KIS VTS 계좌와
실제 주문·체결·잔고를 확인할 수 없습니다. 실계좌 인증과 실제 주문 API는 이번
실험에서 사용하지 않았습니다. 연속 운영 기간, 거래소 달력·실제 수수료·세금·
환율과 운영 장애 복구 목표 또한 이 합성 검증의 완료 범위가 아닙니다.

[실행 가이드](PAPER_LAB.md), [설정 진단](BROKER_READINESS.md),
[생성한 예제 입구](examples/paper_lab/index.html)를 함께 제공합니다.
