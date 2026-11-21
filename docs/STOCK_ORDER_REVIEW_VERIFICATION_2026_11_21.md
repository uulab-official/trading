# 주문 대조·백업·복원 통합 검증 — 2026-11-21

기준 원격 커밋은 `2e78574fe8b6a9c9095080e5120ec754e0475618`다.
이번 변경은 기존 주식 주문 저널과 KIS 관측 수집기를 연결하는 **읽기 전용 점검**,
비공개 JSON/HTML 보고서, 독립적인 저널 백업·검증·새 경로 복원 후보를 추가한다.
Linux / Python 3.12.14에서 구현·독립 리뷰·최종 회귀 검증을 수행했다.

## 완료 범위

| 구성 요소 | 결과 |
| --- | --- |
| 코어 비교 | 실제 저널 전체 감사 후 엄격한 KIS 정규화 문서와 날짜·지점·주문번호로 대조 |
| 점검 항목 | 종목·방향·주문 구분·수량·지정가·누적 체결·대금·유효 수량의 차이, 미연결·관련 이력, 미해결 상태, 오래되거나 미래인 조회 |
| 보고서 | 한국어 정적 HTML과 전체 JSON, 조회 범위·연결 건수·차이·시각·미검증 범위 표시 |
| 백업 | 커밋된 WAL을 포함한 네이티브 SQLite 캡처, 독립적인 DELETE DB와 엄격한 매니페스트 |
| 검증 | DB SHA-256, 전체 논리 재생, 스냅샷 SHA-256, 별칭·건수·제한 플래그 대조 |
| 복원 | 기존 경로를 대체하지 않는 새 WAL 후보, 원본 백업 보존, 미해결 주문 상태 보존 |
| 통합 모의 실행 | 가짜 HTTP 수집 → 정상·차이 점검 → 백업 검증 → 새 DB 복원 → 중복 입력 재전달 |

비교 결과가 같아도 계좌 인증·개별 체결·실제 현금과 보유 수량·접수 및 취소 근거는
검증하지 않는다. 모든 보고서와 백업 결과의 `account_binding_verified`,
`economic_reconciliation_verified`, `lifecycle_confirmation_verified`,
`safe_to_submit`, `live_orders_enabled`는 `false`다.

## 최종 로컬 검증

아래 수치는 독립 리뷰에서 지적한 상위 디렉터리 동기화 수정까지 포함한 마지막
실행 결과다.

| 검증 | 실제 결과 |
| --- | --- |
| 전체 pytest 및 core/data/strategy/backtest 커버리지 XML 생성 | **700 passed; 1,471 subtests passed** |
| 네트워크·DNS 차단 오프라인 검증, 백테스트 포함 | **690 tests, OK** |
| 코어 비교 개별 회귀 | 31 tests; 323 subtests passed |
| 백업·검증·복원 개별 회귀 | 26 tests; 59 subtests passed |
| 실제 점검 CLI·HTML·통합 모의 실행 회귀 | 14 tests; 12 subtests passed |
| 독립 리뷰의 최종 좁은 재현 | 상위 동기화·교체 3개, 비교 대상 없음 표시 1개 통과 |
| compileall: packages, apps/server, tools | 종료 코드 0 |
| fatal flake8: E9,F63,F7,F82 | 오류 0개 |
| git diff --check | 오류 없음 |

테스트 집합은 서로 겹치므로 건수를 합산하지 않는다. 기존 datetime 및 플러그인
deprecation 경고 3,318건은 남아 있다. 경고 숨김이나 새로운 실패 무시 설정은
추가하지 않았다. 각 새 CLI는 별도 `python -B -S` 프로세스에서 소켓·DNS를
금지한 상태로 검증했다.

전체 오프라인 실행기는 기존 코인·주식 전략, 원장, 토스 수집기, KIS 조회·주문
준비와 새 기능을 함께 포함한다. 실제 인증 정보는 사용하지 않았고 외부 증권사
요청은 수행하지 않았다.

## 실제로 실행한 통합 합성 시나리오

`python -B -S tools/stock_order_review_smoke.py --output-dir <새 전용 경로>`를
최종 코드로 실행했다. 가짜 HTTP 응답은 실제 KIS 수집기 함수를 통과하되 소켓을
열지 않는 주입 전송을 사용했다. 응답의 연락처·IP·직원·메시지 표식은 관측값과
후속 보고서에 나타나지 않았다.

| 항목 | 관측 결과 |
| --- | --- |
| 가짜 조회 응답 / 실제 증권사 요청 | 1회 / 0회 |
| 로컬 이벤트 / 고유 체결 | 14개 / 4건 |
| 정상 관측값 연결 | 같은 조회일 주문 2개 연결, 비교 항목 일치 |
| 의도적으로 바꾼 항목 | 매수 누적 체결 5→4주, 누적 대금 49,700→39,700원 |
| 차이 보고서 | 위 두 항목을 정확히 표시하고 `needs_attention=true` |
| 백업 생성·검증·복원 결과 | 모두 원본과 같은 전체 감사 스냅샷 |
| 복원 후 전체 이벤트 재전달 | 추가 이벤트·체결·비용 없음 |
| 최종 현금·수량 순변화 | 5,285원 / 0주 |
| 검증 항목 | 8개 모두 참 |

합성 가격·수수료·세금으로 계산한 결과이며 실제 시세·세율·계좌 잔고·투자 수익이
아니다. 원래 합성 입력의 날짜만 실행 시점의 한국 날짜로 맞춰 최근 조회 규격을
시험한다. 개별 회귀에서는 원본 저널을 옮긴 후 백업만으로 복원하는 경우와,
불확실한 제출·부분 체결의 차단 상태를 복원 뒤에도 유지하는 경우도 검증했다.

다음 파일은 그 최종 실행에서 생성한 결과다. DB 파일은 저장소에 포함하지 않는다.

- [정상 대조 JSON](examples/stock_order_review_equal.json)
- [정상 대조 HTML](examples/stock_order_review_equal.html)
- [의도적인 차이 JSON](examples/stock_order_review_difference.json)
- [의도적인 차이 HTML](examples/stock_order_review_difference.html)
- [통합 실행 요약](examples/stock_order_review_smoke.json)

## 독립 리뷰와 수정 근거

독립 리뷰는 최종 소스와 운영 문서에서 미해결 Critical·Important·Minor 항목
없이 문서에 명시한 오프라인 범위를 승인했다. 다음 사항을 실제 코드·회귀에 반영했다.

### 보고서가 SQLite 보조 경로를 차지하지 않도록 제한

입력과 출력의 동일 경로만 검사하면, 아직 없는 `journal.sqlite-wal`, `-shm`,
`-journal`에 HTML이나 JSON을 만들어 다음 저널 접근을 방해할 수 있다. 이제
원본과 출력 간, 출력끼리의 메인·보조 경로 관계를 쓰기 전에 거부한다. 부모 링크
별칭도 해석하며, 각 보조 파일이 실제로 없던 경우의 거부를 테스트했다.

### 비교 대상이 없는 보고서의 표시

다른 날짜의 닫힌 주문만 있고 조회일 자료가 비어 있으면 코어의 비교 범위 내 차이
수는 0이다. 이를 관측값 일치만으로 표시하면 비교를 수행한 것으로 오해할 수
있었다. 실패하는 HTML 회귀로 먼저 재현하고, 제목을 **비교 대상 없음**으로
바꾸며 범위 내 주문·연결 건수를 항상 표시했다. JSON의 빈 범위 동작은 가이드에
명시했다. 독립 리뷰도 해당 테스트를 별도로 다시 실행했다.

### 새 백업 디렉터리의 마지막 동기화

파일과 새 백업 디렉터리를 동기화하는 데 더해, 그 디렉터리가 등록된 상위
디렉터리까지 동기화하도록 보강했다. 부모 디스크립터를 유지하고 그 상대 경로에
새 디렉터리를 만든 후, 전체 검증과 부모 `fsync` 및 파일·디렉터리 동일성 검사까지
성공해야 완료를 반환한다.

부모 동기화 실패, 그 시점의 부모 경로 교체, 그 시점의 백업 파일 교체를 각각
실패하는 회귀로 재현하고 수정했다. 부분 출력이나 교체된 증거는 지우지 않는다.
독립 리뷰에서 세 회귀를 `python -B -S`로 다시 실행해 통과를 확인했다.

### 읽기 전용 보장의 정확한 범위

기존 `StockOrderJournal.snapshot()`은 SQL 읽기 전용이지만 SQLite가 WAL/SHM
보조 파일을 관리할 수 있다. 문서는 이벤트·논리 상태 보존과 디렉터리 전체 불변을
구분한다. 저장된 백업 검증은 별도의 임시 복사본에서만 SQLite를 열어 백업 내용·
권한·항목을 보존한다. 파일시스템의 읽기 접근 시각 갱신까지 차단하는 것은 아니다.

## 재현과 원격 확인

```bash
python -B -S tools/stock_order_review_smoke.py
python -m pytest packages/ apps/server/tests/ -q \
  --cov=trading_core --cov=trading_data --cov=trading_strategy \
  --cov=trading_backtest --cov-report=xml
python tools/offline_checks.py --include-backtest
python -m compileall -q packages/ apps/server/ tools/
python -m flake8 packages/ apps/ tools/ --count \
  --select=E9,F63,F7,F82 --show-source --statistics
git diff --check
```

이 문서는 공개 전 최종 로컬 검증 기록이다. 같은 소스를 담은 정확한 main 커밋의
Python 3.10/3.11/3.12, lint, Docker 결과는
[GitHub Actions](https://github.com/uulab-official/trading/actions/workflows/ci.yml)에서
별도로 확인한다. 로컬 결과를 CI 완료 대신 사용하지 않는다.

## 남은 실제 연동 범위

이번에는 토스·KIS 인증 정보가 없는 환경에서 수행 가능한 모의 경로를 검증했다.
실제 계좌 인증, 현재 API의 실자료 대조, 모의 주문의 실제 접수·부분 체결·취소,
실제 수수료·세금·보유 수량·현금 대조는 완료하지 않았다. 결과 불명 주문의 실제
재전송 허용도 이 도구의 판정 범위가 아니다.

백업 보장은 중지한 작성자와 신뢰할 수 있는 개인 디렉터리를 전제로 한다.
감지한 교체를 보존하지만 모든 적대적 경로 경쟁을 원자적으로 차단하지는 않는다.
`fsync`의 실제 지속성은 파일시스템·장치에 의존하며 전원 장애 실험은 하지 않았다.
해시와 정상 재생만으로 원본 출처나 전체 기록을 일관되게 다시 만든 변조를 인증할
수도 없다. 사용 방법과 이 범위는 [점검 가이드](STOCK_ORDER_REVIEW.md)와
[백업 가이드](STOCK_ORDER_BACKUP.md)에 포함했다.
