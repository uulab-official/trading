# 주식 주문 이벤트 저널과 오프라인 생명주기 테스트

`StockOrderJournal`은 호출자가 제공한 주식 주문 이벤트를 SQLite에 저장하고,
매번 전체 기록을 재생해 주문 상태와 원화·수량 변화를 계산합니다. 부분 체결,
결과를 모르는 제출, 취소 요청 중 추가 체결, 중복 통지, 프로세스 재시작을
독립적으로 시험하기 위한 **오프라인 모의 기능**입니다.

계좌 인증, 주문 전송, 실제 체결 증명 검증 기능은 없습니다. 기존
`StockPaperBook`의 현금·보유 수량이나 전략 커서를 변경하지 않습니다.
`account_ref`는 로컬 구분용 별칭이며 실제 증권 계좌와의 연결을 증명하지
않습니다. 조회가 성공하거나 `blocked=false`여도 주문 실행 권한이 생기지 않습니다.

## 바로 실행하기

저장소 루트에서 Python 3.10 이상으로 실행합니다. 두 도구 모두 표준 라이브러리만
필요하며 `-S`로 외부 패키지 없이 실행할 수 있습니다.

```bash
# 임시 DB에서 전체 합성 시나리오 실행 후 요약 JSON 출력
python -B -S tools/stock_order_lifecycle_smoke.py

# 선택: 기존 상위 폴더 아래 새 전용 디렉터리에 DB와 보고서 보존
mkdir -p data
python -B -S tools/stock_order_lifecycle_smoke.py \
  --output-dir data/order-lifecycle-demo

# 보존된 DB를 읽기 전용으로 재검증
python -B -S tools/stock_order_journal.py --mode paper status \
  --database data/order-lifecycle-demo/journal.sqlite \
  --account-ref demo-stock-orders
```

`--output-dir`은 새 경로여야 합니다. 이미 있는 파일·디렉터리·최종 심볼릭 링크를
대체하지 않습니다. 새 디렉터리는 권한 `0700`, DB와 보고서는 `0600`으로 만듭니다.
실패 후에는 부분적으로 생성된 디렉터리나 DB가 남을 수 있으므로 성공 종료와
보고서 내용을 확인하세요. 같은 디렉터리로 재실행할 때는 기존 결과를 덮어쓰지
말고 새로운 이름을 사용합니다.

고정 입력은
[`synthetic_stock_order_events.json`](../config/examples/synthetic_stock_order_events.json),
실제로 생성한 상세 결과는
[`stock_order_lifecycle_report.json`](examples/stock_order_lifecycle_report.json)에 있습니다.
입력의 날짜·가격·비용은 테스트용 합성 값입니다.

## 합성 시나리오의 독립 기대값

| 단계 | 검증 내용 |
| --- | --- |
| 매수 예약 → 결과 불명 | 새 client order ID를 통한 다른 주문 예약 차단 |
| 접수 → 3주 부분 체결 | 9,900원 × 3주 + 수수료 30원 = 현금 변화 −29,730원 |
| 같은 체결 재전달 | 이벤트 통지는 보존하되 체결 수량·비용 중복 반영 방지 |
| 취소 요청 → 2주 추가 체결 | 취소 대기 중 10,000원 × 2주와 수수료 20원 반영 |
| 취소 확인 | 이미 체결한 5주 유지, 미체결 5주 유지, 현재 유효 주문 수량 0주 |
| 취소 후 과거 체결 재전달 | 동일 체결은 허용하되 새로운 체결은 거부 |
| 5주 매도 → 2주 체결 → 취소 요청 | 11,100원 × 2주, 수수료 22원·세금 44원 반영 |
| 취소 대기 중 잔여 3주 체결 | 11,000원 × 3주, 수수료 33원·세금 66원 반영 후 `filled` |
| 전량 체결 후 취소 확인 | 상태 변경 거부, 경제적 기록 유지 |
| 각 단계 재열기와 전체 입력 재전달 | 커밋된 상태 재현, 이벤트·체결 중복 방지 |

최종 이벤트 ID는 **14개**, 고유 체결은 **4건**입니다. 매수 대금 49,700원과
수수료 50원, 매도 대금 55,200원과 수수료 55원·세금 110원을 합치면
`cash_delta_krw="5285"`입니다. 총 수량 변화는 0이므로 `position_deltas={}`이며,
미해결 주문이 없어 `blocked=false`입니다. 이는 실제 잔고·평가액·투자 수익률이
아닌 합성 이벤트의 순변화입니다.

## 수동 이벤트 기록 명령

직접 쓸 때는 다른 프로세스가 파일을 교체할 수 없는 전용 디렉터리를 준비하고,
이전에 사용하지 않은 DB 이름을 선택합니다. 같은 이름의 SQLite 보조 파일
`-wal`, `-shm`, `-journal`도 기존 기록일 수 있으므로 삭제하거나 재사용하지 마세요.
생성기는 세 보조 경로를 메인 파일 예약 전과 SQLite 초기화 전에 검사합니다.
기존 항목이 있으면 거부하며, 예약 이후 새 항목을 발견하면 새 빈 메인 파일도
보존합니다. 마지막 검사 이후 다른 작성자가 경로를 바꾸는 경쟁까지 막는
원자적 파일시스템 프로토콜은 아니므로 전용 디렉터리 조건이 필요합니다.

```bash
mkdir -m 700 data/manual-stock-orders
python -B -S tools/stock_order_journal.py --mode paper init \
  --database data/manual-stock-orders/orders.sqlite --account-ref local-stock-demo

python -B -S tools/stock_order_journal.py --mode paper record \
  --database data/manual-stock-orders/orders.sqlite --account-ref local-stock-demo \
  --event /path/to/reserve-event.json

python -B -S tools/stock_order_journal.py --mode paper status \
  --database data/manual-stock-orders/orders.sqlite --account-ref local-stock-demo
```

`init`은 새 DB를 배타적으로 생성합니다. `record`와 `status`는 기존 DB만 열며
없는 파일이나 부모 폴더를 만들지 않습니다. `status`는 읽기 전용 연결로 전체
논리 기록을 검증합니다. SQLite WAL 조회에는 SQLite 자체의 보조 파일 관리가
수반될 수 있으므로 파일시스템 전체가 불변이라는 뜻은 아닙니다.

| 종료 코드 | 의미 |
| --- | --- |
| `0` | `init`/`record` 성공 또는 미해결 주문 없는 `status` |
| `3` | `status` 검증은 성공했지만 미해결 주문이 있어 새 예약 차단 |
| `2` | 잘못된 입력, 경로 충돌, 계정 별칭 불일치, 손상 또는 사용할 수 없는 DB |

`record`가 성공해도 미해결 주문이 있을 수 있습니다. 자동화는 JSON의 `blocked`와
`pending_client_order_ids`를 확인하거나 별도 `status` 종료 코드를 사용해야 합니다.
실패 메시지는 고정 문자열이며 원래 예외·파일 경로·입력값을 출력하지 않습니다.
명령은 인증 환경변수를 읽지 않습니다. 별칭과 이벤트 ID에 계좌번호나 비밀값을
넣으면 사용자가 입력한 값 자체는 정상 JSON에 나타날 수 있으므로 비밀정보를
사용하지 마세요.

## 이벤트 형식과 상태

각 입력 파일은 최대 65,536바이트의 단일 JSON 객체입니다. 중복 키와 비유한
숫자는 거부합니다. 허용되는 최상위 키는 정확히 다음 네 개입니다.

```json
{
  "event_id": "buy.reserve.001",
  "kind": "reserve",
  "client_order_id": "local-buy-001",
  "data": {
    "order_date": "2026-11-20",
    "symbol": "005930",
    "side": "buy",
    "quantity": 10,
    "limit_price": "10000",
    "max_notional": "100000"
  }
}
```

이벤트·client·execution·evidence ID는 1–64자의 ASCII 영숫자로 시작하고 뒤에는
영숫자, `.`, `_`, `-`만 허용합니다. `account_ref`는 같은 범위에서 영문자로
시작합니다. 주문 날짜는 유효한 `YYYY-MM-DD`입니다. 모의 과거 재생을 허용하므로
저널의 날짜에는 조회 도구의 최근 30일 제한이 없습니다.

| `kind` | `data`의 정확한 키 | 새 이벤트의 조건·결과 |
| --- | --- | --- |
| `reserve` | `order_date`, `symbol`, `side`, `quantity`, `limit_price`, `max_notional` | 미해결 주문이 없을 때 `reserved` |
| `submission_uncertain` | 없음 (`{}`) | `reserved` → `uncertain` |
| `acknowledge` | `broker_order_id`, `broker_org_no` | `reserved`/`uncertain` → `accepted` |
| `fill` | `execution_id`, `quantity`, `price`, `fee_krw`, `tax_krw` | 접수된 미해결 주문에 부분·전량 체결 반영 |
| `cancel_requested` | 없음 (`{}`) | `accepted`/`partially_filled` → `cancel_pending` |
| `canceled` | `evidence_id` | `cancel_pending` → `canceled`, 이전 체결 유지 |
| `rejected` | `evidence_id` | 체결이 없는 미해결 주문 → `rejected` |

한 저널은 미해결 주문을 한 개만 허용합니다. `reserved`, `uncertain`, `accepted`,
`partially_filled`, `cancel_pending`은 모두 다른 client ID의 예약을 차단합니다.
종료 상태는 `filled`, `canceled`, `rejected`입니다. 취소 대기 중 부분 체결이면
`cancel_pending`을 유지하고, 전량 체결이면 `filled`가 됩니다. 취소 후 새 체결이나
전량 체결 후 새 취소 확인은 거부하므로, 순서가 확인되지 않은 실제 통지를
자동 투입할 수 있는 완성된 증권사 어댑터가 아닙니다.

### 수량·금액·중복 규칙

- 종목은 KRX/KRW 범위의 6자리 숫자 코드이며 정수 수량만 지원합니다. 수량은
  1–9,999,999,999입니다. 실제 종목 존재 여부나 거래 가능 여부는 검증하지 않습니다.
- 가격은 최대 19자리, `max_notional`·수수료·세금은 최대 29자리의 정규화된
  ASCII 정수 문자열입니다. 수수료·세금은 `"0"`을 허용합니다. 소수·부동소수점·
  지수 표기·음수·앞자리 0은 허용하지 않습니다.
- 예약 수량 × 지정가는 `max_notional` 이하여야 합니다. 매수 체결가는 지정가
  이하, 매도 체결가는 지정가 이상이어야 합니다. 매도 가격 개선으로 체결 대금이
  예약 시 한도보다 커지는 것은 허용합니다. 이 한도는 계좌 잔고 검사나 비용을
  포함한 실거래 리스크 한도가 아닙니다.
- 같은 `event_id`와 같은 내용은 저장 건수를 늘리지 않고 현재 상태를 반환합니다.
  같은 ID에 다른 내용은 거부합니다. 새 이벤트 ID로 동일한 의미의 통지가 오면
  이벤트는 남지만 주문 상태나 경제적 값은 다시 변경하지 않습니다.
- 증권사 주문 식별자는 주문 날짜·숫자로 해석한 지점번호·주문번호의 조합으로
  중복을 검사합니다. 표시용 앞자리 0은 보존하지만 다른 주문의 별칭으로 쓰지
  못합니다. 이미 접수된 주문의 식별자 표현은 변경할 수 없습니다.
- 체결 식별자는 주문 날짜와 `execution_id`의 조합입니다. 같은 식별자를 다른
  주문·수량·가격·비용으로 재사용하면 거부합니다. 종료 후에도 정확히 같은
  과거 체결의 재전달은 수량·비용을 바꾸지 않습니다.

`filled_quantity`는 누적 체결, `unfilled_quantity`는 원수량에서 체결을 뺀 값,
`working_quantity`는 현재 유효 수량입니다. 취소 시 미체결 값은 보존되고 유효
수량만 0이 됩니다. 원화 계산은 Python 정수로 수행해 Decimal 전역 정밀도나
이진 부동소수점 반올림에 의존하지 않습니다.

## 저장·감사와 복구의 범위

모든 기록은 SQLite WAL과 `synchronous=FULL`을 사용합니다. `BEGIN IMMEDIATE`
트랜잭션 안에서 기존 기록을 검증하고, 새 이벤트와 마지막 해시·순번을 함께
커밋합니다. 읽기는 하나의 일관된 트랜잭션에서 스키마·설정·SQLite 무결성·
이벤트 순서·해시 체인·마지막 순번·상태 전이를 모두 검사합니다.

테스트에는 독립 연결의 동시 기록, 스냅샷 중 동시 커밋, 마지막 순번 갱신 뒤
예외, 같은 지점에서 자식 프로세스의 실제 `os._exit(73)`, 재열기 후 재기록이
포함됩니다. 프로세스 중단 전 커밋되지 않은 이벤트가 유효한 기록으로 나타나지
않는지 확인합니다. 이는 임의 파일시스템·하드웨어의 전원 장애까지 검증한 것은
아닙니다.

해시 체인과 마지막 순번은 부분 삭제·수정·불일치를 감지합니다. 서명이 없으므로
설정·전체 기록·해시를 일관되게 다시 작성하는 공격의 진위를 판별하지 못합니다.
새 파일 생성 실패 시 생성한 일부 DB를 보존할 수 있습니다. 손상된 DB를 자동
복구하거나 삭제하지 않으며, 읽기 전용으로도 검증에 실패하면 별도 보존 후
원인을 조사해야 합니다. 사용 중인 WAL DB는 메인 파일만 복사하지 마세요.

## KIS 관측값과의 관계

[`kis_vts_order_snapshot.py`](KIS_VTS_ORDER_SNAPSHOT.md)는 VTS 일별 주문 내역을
별도 JSON으로 수집합니다. 누적 체결 수량과 남은 수량만으로 개별 체결 ID·체결
가격·비용을 확정할 수 없으므로 이 저널에 자동 반영하지 않습니다. 모든 페이지를
받았다는 사실도 수집 중 계좌가 변하지 않았다는 보장은 아닙니다.

실제 연동에는 인증된 주문·체결·취소 증거, 원주문·정정주문 관계, 계좌별 ID 범위,
통지 지연·역전, 거래일·가격 단위·수수료·세금, 실제 잔고 대조를 추가 검증해야 합니다.
현재 주문 준비 도구, 읽기 전용 관측 도구, 오프라인 이벤트 저널의 결과를 연결하는
자동 실행 경로는 없습니다.

## 개발자 API와 검증 명령

```python
from trading_core.stock_order_journal import StockOrderJournal

journal = StockOrderJournal.create(path, account_ref="local-stock-demo")
state = journal.record(event)
reader = StockOrderJournal.open_existing(path, account_ref="local-stock-demo", read_only=True)
assert reader.snapshot() == state
```

읽기 전용 인스턴스의 `record()`는 DB 연결 전에 `PermissionError`를 냅니다.
각 작업은 짧은 연결을 사용하며 인스턴스에 잔고나 주문 상태를 캐시하지 않습니다.

```bash
python -m pytest packages/core/tests/test_stock_order_journal.py \
  apps/server/tests/test_stock_order_lifecycle.py \
  apps/server/tests/test_kis_vts_order_snapshot.py -q
python tools/offline_checks.py --include-backtest
```

실행 일자별 결과와 실제 인증 검증의 남은 범위는
[검증 기록](STOCK_ORDER_LIFECYCLE_VERIFICATION_2026_11_21.md)에 정리합니다.
