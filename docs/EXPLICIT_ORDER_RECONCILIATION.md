# 명시적 주문 조회·대조 (v21)

## 범위와 사용
원격 v20의 `SQLiteOrderStateStore`와 `OrderManager`를 재사용합니다.
응답 유실로 거래소 주문번호를 저장하지 못한 주문을 고객 주문 ID로 명시적으로
조회합니다. 주문 생성·재전송·취소·금액 보정·라이브 활성화는 수행하지 않습니다.
별도 상태 머신 대신 기존 관리자의 주문별 I/O 배타권·응답 검사·저장을 공유합니다.
기존 자동 폴링은 변경하지 않으며 ID 없는 주문을 자동 조회하지 않습니다.

```python
from trading_core.order_reconciliation import reconcile_order
# 알려진 거래소 ID가 있으면 기존 fetch_order를 사용합니다.
resolved = await reconcile_order(manager, 'client-id')
# 거래소 ID가 없을 때만 명시적 허용이 필요합니다.
resolved = await reconcile_order(manager, 'client-id',
    allow_client_lookup=True, timeout_seconds=10.0)
```

원래 계좌/거래소와 동일한 어댑터 및 상태 저장 파일을 사용해야 합니다.
로컬 scope 별칭이 계좌 동일성을 증명하지 않습니다. 같은 주문 I/O가 진행 중이면
`ReconciliationRequired`로 거부합니다. 완료 상태는 사본으로 반환하고 재조회하지 않습니다.

## 어댑터 계약
`async fetch_order_by_client_id(client_order_id, symbol)`이 필요합니다.
이번에는 **PaperExchange만 구현**했습니다. KIS/CCXT의 실제 주문 조회는
추가하거나 활성화하지 않았습니다. 실제 공급자는 공식 API와 계좌별 검증이 필요합니다.

응답은 정확히 한 주문을 식별하는 dict이며 `id, clientOrderId, symbol, side,
type, amount, filled, remaining, status`가 모두 필요합니다. 지정가는 `price`도
원래 지정가와 같아야 합니다. 시장가 응답의 가격은 체결가격일 수 있으므로
주문가격과 비교하지 않습니다. 기존 관리자가 단조 증가 체결량·초과 체결·상태와
거래소 ID 중복을 다시 검사합니다.

체결/취소된 주문도 조회해야 합니다. 미체결 목록만 검색하거나 고객 ID로 거래소
ID를 예측하거나 금액/시각이 비슷한 주문을 고르면 안 됩니다. None·빈 결과·여러
후보·unsupported·식별자/가격/수량 불일치·통신 실패는 **미해결**입니다.
미검색을 거절의 증거로 보거나 기록을 지워서 신규 주문 차단을 해제하지 않습니다.

## 취소·저장 경계
기본 10초, 허용 범위 `0 < timeout_seconds <= 30`입니다. 재시도는 없습니다.
호출자 취소/제한시간 경과 시 read task 취소를 요청하고 늦은 응답은 버립니다.
어댑터가 취소를 삼켜도 호출자 취소가 성공 응답으로 바뀌지 않습니다. 실제 read
task 종료까지 해당 주문의 배타권을 유지합니다. 늦은 예외는 원문 로그 없이 소비하고
주문 상태에 적용하지 않습니다. 블로킹 코드는 이벤트 루프 자체를 멈출 수 있으므로
하드 실시간 보장은 아닙니다.

정상 반환은 기존 `_apply_response`의 검증·영속 저장 이후입니다. 저장 실패 시
`OrderPersistenceError`를 유지하고 같은 관리자의 다음 I/O를 차단합니다.
실패 후 객체는 진단 자료이지 저장 완료 증거가 아닙니다. 파일을 다시 열고 대조해야 합니다.
이 기능은 주문 상태만 다루며 별도 현금·수수료·포지션 원장에 체결을 추가 반영하지 않습니다.
증권사 접수와 로컬 DB의 분산 트랜잭션이나 exactly-once 보장이 아닙니다.

## 키 없는 데모
원격 저장소의 기존 설치 방식으로 패키지를 설치한 환경에서 실행합니다.
```bash
mkdir -p .private
chmod 700 .private
python tools/order_reconcile_demo.py --database .private/reconcile-demo-001.sqlite
python -m unittest discover -s tests -p 'test_order_reconciliation.py' -v
```
새 DB 경로만 허용하며 기존 파일을 덮어쓰지 않습니다. 합성 주문 체결 후 응답을
의도적으로 유실시키고 주문 DB를 다시 엽니다. 별도로 보존한 PaperExchange
스냅샷을 복원한 뒤 대조합니다. 실제 계좌나 복수 파일의 원자 복구가 아닙니다.
최종 FILLED, 체결 1건, 현금 899.9 USDT, 수수료 0.1 USDT, 조회 전후 계좌
스냅샷 동일 여부와 재시작 후 상태 유지를 확인합니다.

## 증거 경계
원격 e9bb39a의 manager/store 및 exchange/paper_exchange Git blob 해시를
확인했습니다. 전체 clone은 DNS 실패로 불가능하여 이전 누적 소스와 해시 확인한
원격 파일을 재구성했습니다. 로컬 전체 검사와 원격 CI는 별도로 기록합니다.
이번 패치는 이전 누적 UI/KIS 기능을 원격에 올리지 않습니다. 실제 계좌·전원 장애·
네트워크 파일시스템·장시간 운용 및 독립 외부 코드 리뷰는 미검증입니다.

Python 공식 취소 계약 참고: https://docs.python.org/3.10/library/asyncio-task.html
wait_for의 취소 대기는 제한시간을 넘을 수 있고 태스크는 취소를 억제할 수 있습니다.
이번 구현은 shield/read task를 사용하여 호출자 취소와 늦은 결과 적용을 분리합니다.
