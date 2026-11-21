# 주식 주문 대조·HTML 보고서·통합 모의 테스트

로컬 주문 저널과 이미 저장한 KIS 주문 관측값을 함께 점검합니다. 주문번호와
수량·가격·누적 대금의 차이를 찾고, 미해결 주문과 조회 자료의 시각을 JSON 및
한국어 HTML 보고서로 보여줍니다. 관측값으로 이벤트를 생성하거나 저널을 수정하지
않습니다. Python 3.10 이상과 표준 라이브러리만 필요합니다.

## API 키 없이 전체 흐름 실행

```bash
# 임시 파일을 사용하는 기본 실행: 완료 후 요약 JSON만 남음
python -B -S tools/stock_order_review_smoke.py

# 새 전용 디렉터리에 합성 데이터와 보고서·백업·복원 후보 보존
mkdir -p data
python -B -S tools/stock_order_review_smoke.py \
  --output-dir data/order-review-demo
```

통합 도구는 기존 14개 합성 이벤트의 날짜를 실행 시점의 한국 날짜로 맞춥니다.
가격·수량·비용은 기존 고정 합성 값입니다. 실제 KIS 수집기에 **가짜 HTTP 응답
한 번**을 주입하고, 연락처·IP·직원·메시지 같은 불필요한 원본 필드가 제거되는지
확인합니다. 실제 HTTP·DNS·인증·계좌·주문 요청은 없습니다.

| 단계 | 확인하는 결과 |
| --- | --- |
| 합성 주문 기록 | 이벤트 14개, 고유 체결 4건, 현금 순변화 5,285원, 주식 순변화 0주 |
| 일치하는 합성 관측값 | 같은 날짜·지점·주문번호 두 개를 연결하고 비교 항목 일치 확인 |
| 의도적으로 다른 관측값 | 매수 누적 체결을 5주→4주, 대금을 49,700원→39,700원으로 바꾼 차이 발견 |
| 원본 저널 재검증 | 두 점검 후에도 원본 이벤트·체결·경제적 상태가 동일 |
| 백업 생성·검증 | SQLite 네이티브 백업과 전체 논리 재생 결과가 원본과 동일 |
| 새 DB 복원 | 기존 경로를 대체하지 않고 `restored.sqlite` 생성·검증 |
| 복원 후 중복 재생 | 전체 이벤트를 다시 전달해도 추가 체결·비용 없음 |
| 원본 보존 | 원본 저널 상태와 백업 두 파일의 내용이 그대로 유지 |

이 값은 합성 입력의 계산 결과이며 실제 시세·세율·잔고·수익률이 아닙니다.
결과 디렉터리는 `0700`, 생성한 보고서와 DB는 `0600`입니다. 기존 출력 경로를
덮어쓰지 않으며 실패하면 일부 증거가 남을 수 있으므로 새 이름으로 다시 실행하세요.

| 파일·폴더 | 내용 |
| --- | --- |
| `journal.sqlite` | 합성 원본 저널 |
| `synthetic-observations.json` | 실제 수집기 형식을 통과한 합성 관측값 |
| `review.json`, `review.html` | 관측값이 일치하는 합성 보고서 |
| `review-difference.json`, `review-difference.html` | 의도적으로 다른 관측값의 합성 보고서 |
| `backup/` | 독립적인 DB와 엄격한 매니페스트 두 파일 |
| `restored.sqlite` | 검증한 새 복원 후보 |
| `smoke.json` | 전체 시나리오의 요약과 검증 항목 |

저장소의 [일치 예제 HTML](examples/stock_order_review_equal.html),
[차이 예제 HTML](examples/stock_order_review_difference.html),
[실행 요약](examples/stock_order_review_smoke.json)은 실제 모의 실행에서 생성했습니다.
백업·복원만 따로 실행하는 명령은 [백업 가이드](STOCK_ORDER_BACKUP.md)에 있습니다.

## 기존 저널 점검

```bash
# 로컬 저널만 읽기 전용으로 확인
python -B -S tools/stock_order_review.py --mode paper \
  --database data/order-review-demo/journal.sqlite \
  --account-ref demo-stock-orders

# 저장된 관측값과 비교해 새 JSON/HTML 생성
python -B -S tools/stock_order_review.py --mode paper \
  --database data/order-review-demo/journal.sqlite \
  --account-ref demo-stock-orders \
  --observations data/order-review-demo/synthetic-observations.json \
  --max-age-seconds 300 \
  --output-json data/order-review-demo/review-latest.json \
  --output-html data/order-review-demo/review-latest.html
```

`--mode paper`, `--database`, `--account-ref`는 필수입니다. 관측값과 출력 파일은
선택 사항입니다. 출력 파일을 지정하지 않아도 전체 점검 JSON을 stdout으로
출력합니다. 계좌 별칭·주문번호·합성 또는 사용자 입력 체결 내역을 포함하므로
실제 계좌 데이터를 사용한 출력과 보고서는 비공개로 보관하세요.

`--max-age-seconds`는 1–86,400초의 정수이며 기본 300초입니다. 수집 시작 시각이
현재 UTC 시각보다 미래이거나 설정한 기간을 넘으면 확인 항목으로 표시합니다.
현재 상태를 보여주는 실시간 화면은 아니므로 나중에 열어 본 HTML의 시각도
확인해야 합니다. HTML의 표시 시각은 한국 시간(KST)이며 JSON은 UTC입니다.

| 종료 코드 | 의미 |
| --- | --- |
| `0` | 유효한 보고서를 만들었고 현재 점검 범위에서 확인 항목이 없음 |
| `3` | 유효한 보고서를 만들었지만 미해결 주문·차이·미연결 내역·오래되거나 미래인 시각 등 확인 항목이 있음 |
| `2` | 입력·저널 감사·경로 충돌·출력에 문제가 있어 완료하지 못함 |

`3`은 JSON/HTML 생성 실패가 아닙니다. 점검 보고서를 읽고 확인해야 한다는
의미입니다. `0`도 계좌 인증, 개별 체결 증명, 잔고 일치나 주문 허용을 뜻하지
않습니다. 관측값을 제공하지 않은 닫힌 저널은 `not_supplied`로 표시하며 미해결
주문이 없으면 `0`일 수 있습니다.

## 대조 기준

저널은 기존 전체 재생 감사에 성공해야 합니다. 관측 문서는 기존
[KIS 읽기 전용 수집기](KIS_VTS_ORDER_SNAPSHOT.md)의 정규화된 정확한 형식을
요구합니다. 원시 KIS 응답이나 임의 CSV·과거 원장 JSON은 이 입력 형식이 아닙니다.

- 조회일과 같은 로컬 주문만 비교합니다. 다른 날짜의 주문도 표에 남기고
  `out_of_scope`로 표시합니다. 다른 날짜의 미해결 주문도 차단 상태를 유지합니다.
- 주문 날짜·숫자로 해석한 지점번호·주문번호가 모두 같아야 연결합니다. 표시용
  앞자리 0은 보존합니다. 종목·수량·방향이 비슷해도 접수 번호가 없는 주문을
  임의로 연결하지 않습니다.
- 종목, 매수·매도, 지정가 주문 구분 `00`, 원수량, 지정가, 누적 체결 수량,
  누적 체결 대금, 로컬 유효 수량과 조회 잔량을 비교합니다.
- 원주문 참조가 있는 내역과 참조되는 주문은 관련 이력 확인 대상으로 남깁니다.
  정정·취소 관련 행을 합산하거나 별도 체결로 만들지 않습니다.
- 조회에서 찾지 못한 주문과 로컬 기록에 없는 관측값은 확인 대상으로 표시합니다.
  조회 부재를 주문 거절이나 재전송 가능 근거로 간주하지 않습니다.

`comparison_status`는 `not_supplied`, `observations_equal`, `needs_review` 중
하나입니다. `observations_equal`은 조회 범위의 **비교 항목에서 차이를 찾지
못했다**는 뜻입니다. 조회일 범위에 로컬 주문이 전혀 없을 때도 이 값일 수
있으므로 `summary.scoped_order_count`와 `matched_order_count`를 함께 확인하세요.
HTML은 이 경우 **비교 대상 없음**으로 표시하고 범위 내 주문·연결 건수를 보여줍니다.

미해결 주문의 숫자가 일치해도 `journal_blocked=true`, `needs_attention=true`를
유지합니다. 체결이 진행 중이거나 취소를 기다리는 로컬 상태가 관측값 일치만으로
완료 상태가 되지 않습니다.

## 판정할 수 없는 내용

다음 값은 모든 보고서에서 항상 `false`입니다.

| 필드 | 미검증 내용 |
| --- | --- |
| `account_binding_verified` | 로컬 별칭이 실제 증권 계좌와 연결되는지 |
| `economic_reconciliation_verified` | 개별 체결·수수료·세금·현금·보유 수량의 실제 일치 |
| `lifecycle_confirmation_verified` | 실제 접수·거절·취소·완료 증거의 진위 |
| `safe_to_submit` | 새 주문 또는 결과 불명 주문의 재전송 허용 |
| `live_orders_enabled` | 실거래 실행 활성화 |

누적 관측값이 같아도 개별 체결 ID·체결 시각·수수료·세금의 증거가 생기지
않습니다. 잔량 0이나 취소·거절 플래그로 상태를 추정하지 않습니다. 페이지를
모두 받았다는 사실도 수집 중 계좌가 변하지 않았다는 보장은 아닙니다.

## 입력·출력 보존

관측 JSON은 2,000,000바이트 이하의 일반 파일이어야 합니다. 최종 심볼릭 링크,
중복 키, 비유한 숫자, 실수 메타데이터, 과도한 숫자 리터럴, 알 수 없는 필드,
잘못된 안전 플래그·별칭·날짜·숫자·중복 주문 식별자는 거부합니다. 시각은 수집기가
출력하는 정규화된 UTC ISO 형식을 사용합니다. 저장된 과거 자료에는 수집기의
최근 30일 요청 제한을 다시 적용하지 않고 자료 신선도를 별도로 판단합니다.

보고서는 기존 부모 폴더 아래 새 이름이어야 합니다. 입력 경로와 그 SQLite 보조
경로 `-wal`, `-shm`, `-journal`, 다른 출력의 동일·보조 경로를 대상으로 쓸 수
없습니다. 부모 링크의 별칭도 해석해 검사합니다. 기존 파일·디렉터리·링크를
대체하지 않습니다.

JSON과 HTML을 함께 요청하면 모두 배타적으로 예약한 다음 씁니다. 처리 가능한
쓰기 실패에서는 소유한 출력만 정리하고, 다른 파일로 교체된 경로는 보존합니다.
여러 파일을 한 번에 공개하는 원자적 프로토콜은 아니므로 성공 종료 후 읽어야
합니다. 프로세스 강제 종료·일부 시스템 오류로 부분 파일이 남을 수 있습니다.
신뢰할 수 있는 개인 전용 부모 폴더에서 실행하세요.

저널은 SQL 읽기 전용으로 감사하고 이벤트를 추가하지 않습니다. SQLite 자체의
WAL/SHM 보조 파일 관리는 수반될 수 있으므로 원본 디렉터리 모든 바이트·메타데이터가
불변이라는 뜻은 아닙니다. HTML은 동적 텍스트를 이스케이프하고 JavaScript·외부
이미지·폰트·네트워크 리소스 없이 동작하며 CSP로 외부 자원과 폼 전송을 차단합니다.

## 개발자 API와 회귀 검증

```python
from trading_core.stock_order_review import review_orders

# journal은 실제 StockOrderJournal 인스턴스, observations는 정규화된 dict 또는 None
report = review_orders(journal, observations, now=aware_datetime, max_age_seconds=300)
```

직접 JSON을 디코딩하는 호출자는 중복 키·비유한 숫자를 거부해야 합니다.
`now`는 시간대가 있는 datetime 또는 생략 값이며, API는 완전한 보고서 또는
입력값을 노출하지 않는 고정 문구의 `ValueError`만 반환합니다.

```bash
python -m pytest packages/core/tests/test_stock_order_review.py \
  apps/server/tests/test_stock_order_backup.py \
  apps/server/tests/test_stock_order_review_cli.py -q
python tools/offline_checks.py --include-backtest
```

최종 실행 수치와 독립 리뷰 결과는
[검증 기록](STOCK_ORDER_REVIEW_VERIFICATION_2026_11_21.md)에 정리합니다.
