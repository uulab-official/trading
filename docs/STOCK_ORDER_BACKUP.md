# 주식 주문 저널 백업·검증·복원 후보

`tools/stock_order_backup.py`는 `StockOrderJournal`의 **오프라인 paper 상태**를
백업하고, 백업을 독립적으로 재생하며, 새 경로에 복원 후보를 만듭니다. 기존 주문
저널, 실행 설정, 활성 DB 경로, 주문 전송 기능을 전환하지 않습니다. 계좌 별칭과
주문·체결 데이터가 포함되므로 출력과 백업은 개인 전용 디렉터리에 보관하세요.

Python 3.10 이상과 표준 라이브러리만 사용합니다. 파일 권한, 배타적 생성,
디렉터리 파일 디스크립터를 지원하는 POSIX 환경을 전제로 합니다. 외부 패키지,
증권사 자격 증명, HTTP 요청, DNS 조회가 필요하지 않습니다.

## 실행 예시

저장소 루트에서 실행합니다. 원본을 쓰는 프로세스와 수동 이벤트 입력을 먼저
중지하세요. 아래 원본은 [합성 생명주기 도구](STOCK_ORDER_LIFECYCLE.md)로 만든
예제이며 실제 주문·체결 증거가 아닙니다. 대상 이름은 매번 새로 선택합니다.

```bash
umask 077
mkdir -p data

# 선택: 합성 예제 저널을 새 디렉터리에 생성
python -B -S tools/stock_order_lifecycle_smoke.py \
  --output-dir data/order-lifecycle-demo

# 기존 저널을 새 백업 디렉터리로 보존
python -B -S tools/stock_order_backup.py --mode paper create \
  --database data/order-lifecycle-demo/journal.sqlite \
  --destination data/order-backup-20261121 \
  --account-ref demo-stock-orders

# 원본 저널 없이도 백업의 바이트·매니페스트·전체 재생을 검증
python -B -S tools/stock_order_backup.py --mode paper verify \
  --backup data/order-backup-20261121 \
  --account-ref demo-stock-orders

# 기존 개인 전용 폴더 안의 새 이름으로 복원 후보만 생성
mkdir -m 700 data/order-recovery
python -B -S tools/stock_order_backup.py --mode paper restore \
  --backup data/order-backup-20261121 \
  --destination data/order-recovery/candidate.sqlite \
  --account-ref demo-stock-orders

# 후보를 기존 저널 도구로 다시 읽기 전용 감사
python -B -S tools/stock_order_journal.py --mode paper status \
  --database data/order-recovery/candidate.sqlite \
  --account-ref demo-stock-orders
```

`--mode paper`와 각 명령의 `--account-ref`는 필수입니다. `account_ref`는
기존 저널과 정확히 일치해야 하는 로컬 ASCII 별칭이며, 실제 계좌 인증을 뜻하지
않습니다. 백업 도구의 종료 코드는 아래와 같습니다.

| 종료 코드 | 의미 |
| --- | --- |
| `0` | 백업 생성, 검증 또는 새 복원 후보의 전체 재생 검증 성공 |
| `2` | 잘못된 입력, 크기 초과, 경로 충돌·교체, 원본 상태 변경, 별칭 불일치, 손상 또는 I/O 실패 |

백업·복원 자체가 성공하면 미해결 주문이 있어도 `0`입니다. JSON의
`snapshot.blocked`와 `snapshot.pending_client_order_ids`를 함께 확인하세요.
위 예제의 마지막 `stock_order_journal.py status` 명령은 미해결 주문이 있으면
기존 동작대로 `3`을 반환합니다.

실패 진단은 고정된 문구만 출력하며 파일 경로, 임의 입력, 환경 변수의 비밀값을
출력하지 않습니다. 성공 JSON에는 검증된 별칭과 주문 상태가 포함됩니다.

## 백업 일관성

생성기는 원본을 읽기 전용으로 전체 감사한 다음 SQLite의 네이티브 `backup()`으로
복사합니다. 따라서 메인 `.sqlite` 파일에 아직 체크포인트되지 않은 **커밋된 WAL
데이터도 포함**됩니다. 원본 메인 파일만 파일 복사하는 방식은 사용하지 않습니다.

복사본을 독립적인 `DELETE` 저널 모드로 내보낸 후, 비공개 임시 복사본에서만
`WAL`로 전환해 기존 `StockOrderJournal`의 전체 재생 검증을 실행합니다. 원본의
사전 감사 결과, 복사본의 재생 결과, 캡처 이후 원본의 재감사 결과가 달라지면
생성을 거부합니다. 원본 파일의 교체도 감지한 경우 거부합니다. 원본에 이벤트를
추가하거나 손상된 상태를 고치는 기능은 없습니다.

원본 전체 작업 구간을 독점 잠그지는 않으므로 **모든 작성자를 중지한 상태에서
실행**해야 합니다. 실행 중 감지된 변경은 거부하지만 다른 프로세스의 이후 쓰기까지
막지는 않습니다. 원본을 읽는 SQLite WAL 연결은 SQLite 자체의 WAL/SHM 보조
파일 관리를 수반할 수 있습니다. 원본 주문 기록을 수정하지 않는다는 의미이며,
원본 디렉터리의 모든 파일·메타데이터가 불변이라는 의미는 아닙니다.

## 백업 형식과 검증 범위

새 백업 디렉터리는 `0700`이며 아래 **두 파일만** 포함합니다. 두 파일은 생성 시
각각 `0600`입니다.

| 파일 | 내용 |
| --- | --- |
| `journal.sqlite` | WAL/SHM/rollback 파일 없이 읽을 수 있는 독립적인 DELETE 모드 SQLite 스냅샷 |
| `manifest.json` | 버전·별칭·paper 제한·UTC 생성 시각·바이트 수·SHA-256·재생 스냅샷 요약 |

검증은 실제 DB 바이트의 SHA-256, 전체 논리 재생, canonical JSON으로 직렬화한
전체 스냅샷의 SHA-256, 이벤트·체결·주문·미해결 주문 개수를 모두 확인합니다.
매니페스트에 파일 해시만 새로 적어 넣어도 잘못된 이벤트 체인, 스키마, 상태 전이,
계좌 별칭 또는 committed tail을 통과할 수 없습니다. 상태 해시와 개수도 실제
재생 결과와 일치해야 합니다.

매니페스트는 정확한 필드 집합을 요구합니다. 알 수 없는 필드, 중복 JSON 키,
NaN·Infinity, 실수·과도하게 긴 숫자, 정수 대신 bool, 비정규 해시, 잘못된 시각,
안전 제한 플래그의 변경은 거부합니다. DB는 **100,000,000바이트**, 매니페스트는
**65,536바이트(64 KiB)** 이하로 제한합니다. 원본의 메인 파일과 존재하는 각
SQLite 보조 파일 및 네이티브 복사의 논리 DB 크기에도 DB 상한을 적용합니다.

백업 디렉터리는 SQLite로 직접 열지 않습니다. `verify`는 두 파일을 읽어서
새 비공개 임시 디렉터리에서 재생한 뒤 임시 복사본만 정리합니다. 백업 내부의
파일 내용, 권한, 디렉터리 항목을 바꾸지 않으며 쓰기 권한이 제거된 백업도 검증할
수 있습니다. 파일 시스템의 읽기 접근 시각 갱신까지 막는다는 보장은 아닙니다.
추가 파일, 하위 디렉터리, `journal.sqlite-wal`, `-shm`, `-journal`, 최종 심볼릭
링크가 있으면 검증을 거부합니다. 파일 해시와 상태가 같아도 검증 중 감지한 경로
교체는 거부합니다.

SHA-256은 일치 여부를 확인하는 수단입니다. 전자서명, 암호화, 원본 인증 또는
백업 출처 증명 기능은 없습니다. DB와 매니페스트를 함께 일관되게 다시 만든
정상적인 논리 기록을 악의적인 변조라고 판별할 수는 없습니다. 로컬 재생 성공은
실제 증권사 주문·체결·수수료·세금·잔고 확인과 구분해서 해석해야 합니다.

## 새 후보에만 복원

복원은 먼저 백업을 위의 모든 절차로 검증하고, 그때 검증한 동일한 바이트를
새로 소유한 후보 파일에 기록합니다. 기존 `StockOrderJournal.create()`의
배타적 생성과 보조 경로 보존 검사를 사용하며, 후보를 WAL 모드로 전환한 뒤
전체 재생 결과가 검증된 백업 스냅샷과 완전히 같은지 확인합니다.

대상 부모 디렉터리는 미리 존재해야 하고 group/other 권한이 없는 개인 전용
디렉터리여야 합니다. 새 후보 파일은 `0600`으로 만듭니다. 이미 존재하는 메인
파일, 디렉터리, 최종 심볼릭 링크, dangling link 및 같은 basename의 `-wal`,
`-shm`, `-journal`이 있으면 해당 경로를 그대로 보존하고 거부합니다. SQLite
보조 파일 접미사로 끝나는 후보 이름도 허용하지 않습니다. 백업 내부나 그
하위 디렉터리에 복원할 수 없으며 부모 심볼릭 링크를 통한 우회도 거부합니다.

불확실한 제출, 부분 체결, 취소 대기, 예약 상태의 미해결 주문은 복원 이후에도
그대로 차단 상태를 유지합니다. 복원 때문에 주문이 접수·취소·완료된 것으로
추정하거나 재전송 가능한 상태로 만들지 않습니다. 검토한 후보는 기존 이벤트
기록 API나 CLI를 이용한 수동 paper 실험에서 이어 쓸 수 있습니다. 이 도구는
활성 경로 승격, 설정 교체, 자동 주문 재시도 또는 broker 플래그 변경을 하지
않습니다.

## 경로 보존과 실패 후 확인

백업 생성 대상도 기존의 신뢰할 수 있는 부모 아래 **새 디렉터리**여야 합니다.
기존 파일·디렉터리·링크를 덮어쓰지 않으며, 원본 메인 파일이나 원본의 예약된
SQLite 보조 경로를 백업 디렉터리 이름으로 사용할 수 없습니다. 처리 도중 발견한
출력 경로 교체는 거부하고 교체된 항목을 삭제하지 않습니다.

생성기는 기존 부모 디렉터리의 파일 디스크립터를 유지한 상태에서 그 안에 새
백업 디렉터리를 만듭니다. 두 파일과 백업 디렉터리를 `fsync`하고 전체 검증을
마친 뒤, 새 디렉터리 항목을 게시한 **부모 디렉터리도 `fsync`**합니다. 마지막
flush와 경로 동일성 확인까지 성공해야 생성 성공을 반환합니다. 부모 flush가
실패해 이미 검증 가능한 파일이 남더라도 해당 생성 작업은 실패로 반환합니다.

`fsync` 성공은 운영체제·파일 시스템·저장 장치가 제공하는 영속성 의미에
의존합니다. 이 회귀 테스트는 flush 요청과 오류·경로 교체 처리를 검증하며,
실제 전원 차단이나 저장 장치 고장을 재현해 무손실을 보증하는 시험은 아닙니다.

실패하면 새 백업 디렉터리 또는 새 후보 DB가 일부 남을 수 있습니다. 도구는
이러한 사용자 경로를 재귀적으로 지우거나 자동 수리하지 않습니다. 매니페스트가
보이는 것만으로 성공으로 판단하지 말고 `verify`의 성공과 상태를 확인하세요.
부분 출력이 있는 경로로 재실행하면 덮어쓰지 않고 거부하므로 다른 새 이름을
선택해야 합니다.

디렉터리 파일 디스크립터와 inode 확인은 감지한 교체 및 쓰기 실패의 영향을
제한합니다. 그러나 SQLite가 경로를 열기 직전 또는 마지막 확인 뒤에 같은
디렉터리에 쓰기 권한이 있는 다른 프로세스가 경로를 바꾸는 모든 경쟁을 원자적으로
막지는 못합니다. 원본과 후보의 부모를 다른 작성자가 바꿀 수 없는 신뢰할 수 있는
개인 공간에서 사용해야 한다는 조건은 기존 저널 생성기와 같습니다.

## Python API와 결과

```python
from tools.stock_order_backup import create_backup, verify_backup, restore_candidate

created = create_backup(database, destination, account_ref="local-stock-demo")
verified = verify_backup(destination, account_ref="local-stock-demo")
restored = restore_candidate(destination, candidate, account_ref="local-stock-demo")

assert created["snapshot"] == verified["snapshot"] == restored["snapshot"]
```

세 API 모두 실패 시 고정 메시지의 `ValueError`를 발생시키며, 성공 시 전체
검증 스냅샷을 `snapshot`에 반환합니다. 공통 결과는 다음과 같습니다.

| 필드 | 값·의미 |
| --- | --- |
| `status` | `stock_order_backup_created`, `stock_order_backup_verified`, `stock_order_restore_candidate_verified` 중 해당 작업 |
| `mode`, `live_orders_enabled` | `paper`, `false` |
| `account_ref` | 감사한 로컬 계좌 별칭 |
| `replay_verified` | `true`: 기존 저널의 전체 논리 재생 검증 성공 |
| `candidate_restore_only` | `true`: 새 후보 경로에만 복원 가능 |
| `account_binding_verified` | 항상 `false` |
| `economic_reconciliation_verified` | 항상 `false` |
| `lifecycle_confirmation_verified` | 항상 `false` |
| `safe_to_submit` | 항상 `false` |
| `snapshot` | 검증한 `StockOrderJournal.snapshot()` 전체 결과 |

## 회귀 검증

```bash
python -B -S -m unittest discover \
  -s apps/server/tests -p test_stock_order_backup.py
```

회귀 테스트는 실제 커밋된 WAL 내용, 캡처 중 원본 변경, 미해결 주문 복원과
수동 paper 후속 기록, 읽기 전용 백업의 불변성, 매니페스트 엄격성, 해시를 다시
써도 실패해야 하는 재생 손상, 파일 크기 제한, 보조 경로·링크·출력 교체 보존,
정적 오류 진단을 확인합니다. 세 CLI 명령은 별도 `python -B -S` 프로세스에서
socket 연결과 DNS 호출을 금지하고 시험합니다.
