# 코인·국내 주식·미국 주식 전체 모의 실험실

한 명령으로 기존 모의 엔진을 실행하고 원장, 독립 재생 감사, 백업, 주문 생명주기,
주문 관측값 대조와 복원 후보를 확인합니다. 결과를 남기면 `index.html`에서
실험별 보고서를 열 수 있습니다. 가격·주문·관측값은 모두 저장소의 합성 입력입니다.

[실행한 예제 입구](examples/paper_lab/index.html)와
[세 원장의 예제 조회 화면](examples/paper_lab/portfolios.html)을 볼 수 있습니다.
예제 폴더에는 실행에서 복사한 정적 보고서만 보존했습니다. DB·백업·복원 후보를
포함한 전체 결과는 아래 명령으로 새 폴더에 생성하세요.

## 실행

저장소 루트에서 Python 3.10 이상을 사용하세요. 통합 실험실은 기존 코어의
PyYAML 의존성이 필요합니다. SQLite와 POSIX 파일 잠금을 사용하는 Linux/macOS
로컬 파일시스템을 전제로 합니다.

```bash
python -m pip install -r requirements-offline.txt

# 임시 폴더에서 실행한 뒤 전체 요약 JSON 출력; 임시 결과는 정리됩니다.
python tools/paper_lab.py

# 아직 존재하지 않는 새 폴더에 결과를 보존합니다.
mkdir -p data
python tools/paper_lab.py --output-dir data/paper-lab-demo
```

보존한 경우 `data/paper-lab-demo/index.html`을 열면 됩니다. 같은 출력 경로로
다시 실행하면 기존 증거를 보호하기 위해 거절합니다. 새 실험에는 다른 이름을
지정하세요. 파일이나 디렉터리를 가리키는 심볼릭 링크, 대상이 없는 심볼릭
링크도 기존 출력으로 취급합니다. 상위 디렉터리는 미리 존재해야 하며 신뢰하는
전용 로컬 경로를 사용하세요.

새 결과 디렉터리는 `0700`, 새 JSON/HTML과 DB는 `0600`입니다. SQLite의 WAL/SHM
및 체크포인트 잠금 파일이 추가로 있을 수 있습니다. 결과 폴더를 이동할 때는
상대 링크가 유지되도록 전체 폴더를 함께 이동하세요.

## 한 번 실행하면 확인되는 항목

| 실험 | 입력·상태 | 실제 확인 |
| --- | --- | --- |
| 코인 | BTC/USDT 합성 90개 시간봉 | 30개 봉에서 중단 → 재시작 → 동일 전체 입력 재실행 → 추가 거래 없음 → 독립 감사·백업 재생 |
| 국내 주식 | `005930` 합성 55개 분봉, KRW 1,000,000 | 원장 생성·재실행·독립 감사·네이티브 SQLite 백업·깊은 재생 검증 |
| 미국 주식 | 가상 종목 `DEMO` 합성 55개 분봉, USD 10,000 | KRW와 별도 DB·현금·수수료 계산으로 같은 절차 확인 |
| 주문 생명주기 | 합성 이벤트 14개, 고유 체결 4건 | 결과 불명, 부분 체결, 취소 경합, 초과 체결 거절, 재시작·중복 전달 |
| 주문 대조·복원 | 실제 수집기에 주입한 가짜 KIS 응답 | 누적 관측값 일치, 의도적 수량·대금 차이 탐지, 백업 검증·새 DB 복원·중복 재생 |
| 설정 진단 | 허용된 토스·KIS 환경변수 8개 | 필요한 변수의 유무만 표시, 값이나 계좌번호는 미출력 |

주식 전략의 고정 실험 설정은 빠른/느린 이동평균 `5`/`20`, 할당 비율 `20%`,
가상 수수료 `0.1%`입니다. USD 입력은 기존 합성 가격을 고정 비율 `400`으로
나눈 별도 테스트 값입니다. 이 비율은 환율이 아니며 미국 거래 시간·시장 달력·
실제 종목 가격을 재현하지 않습니다. 코인은 기존 `PaperRunConfig` 기본값을
사용하며 정확한 설정은 보존한 `crypto/checkpoint.json`에 기록됩니다.

KRW·USD·USDT의 현금과 수익률을 합산하지 않습니다. 통합 조회는 독립 감사된
원장 카드 3개를 표시하고 `cross_portfolio_total`은 `null`입니다. 주문 생명주기와
주문 대조는 같은 합성 이벤트를 각각 별도 DB에서 시험하므로 체결 수를 더하지
않습니다. 어느 수치도 실제 계좌 잔고나 투자 성과를 뜻하지 않습니다.

## 결과 폴더

| 경로 | 내용 |
| --- | --- |
| `index.html` | 한국어 실험 결과 입구와 설정 유무 |
| `summary.json` | 전체 성공 요약, 구성 실험별 결과와 확인 항목 |
| `portfolios.json`, `portfolios.html` | 세 개 원장의 독립 감사 결과 |
| `broker-readiness.json` | 비밀값 없는 환경변수 유무 진단 |
| `crypto/` | `candles.json`, `checkpoint.json`, `audit.json`, `report.html`, `backup/` |
| `stock-krw/`, `stock-usd/` | `candles.json`, `strategy.sqlite`, `audit.json`, `report.html`, `smoke.json`, `backup/` |
| `order-lifecycle/` | 합성 주문 DB와 이벤트별 상태 추적 `report.json` |
| `order-review/` | 합성 관측값, 일치·불일치 JSON/HTML, 백업과 `restored.sqlite`, 실행 요약 |

출력은 보고서 생성 시점의 정적 상태입니다. 실험 기준 시각은 JSON에 UTC,
입구 HTML에 한국 시간으로 표시합니다. 원장별 과거 봉의 시각은 합성 입력을
따릅니다. 같은 데이터를 같은 설정으로 다시 실행해도 모의 체결은 중복되지
않으며, 실험 전체를 재실행할 때는 항상 새 결과 폴더를 지정합니다.

### 일부만 실행하기

```bash
# 주식 실험만, 결과 보존 가능
python tools/stock_paper_smoke.py --currency KRW --output-dir data/stock-krw-demo
python tools/stock_paper_smoke.py --currency USD --output-dir data/stock-usd-demo

# 설정 진단은 표준 라이브러리만 사용합니다.
python -B -S tools/broker_readiness.py --format text

# 주문 대조·복원도 별도 실행할 수 있습니다.
python -B -S tools/stock_order_review_smoke.py --output-dir data/order-review-demo
```

통합 실행기와 주식 전략 명령에는 `-S`를 붙이지 마세요. 기존 의존성을 읽어야
합니다. 자세한 설정 유무 의미는 [설정 진단 가이드](BROKER_READINESS.md), 주문
대조 기준은 [주문 점검 가이드](STOCK_ORDER_REVIEW.md)에 있습니다.

## 성공·실패와 네트워크 경계

종료 코드 `0`은 모든 구성 실험과 보고서 생성을 완료했다는 뜻입니다. 토스·KIS
환경변수가 없어도 오프라인 실험은 정상 완료되며, 연결 준비 표에 필요한 변수
이름을 남깁니다. 설정 존재만으로 인증 성공이나 주문 가능 상태를 표시하지 않습니다.

종료 코드 `2`는 인자, 입력, 실행 또는 출력 실패입니다. 실패 메시지에는
입력 경로나 환경변수 값·원본 예외를 넣지 않습니다. 실패한 실험의 중간 파일은
남을 수 있습니다. 처리된 실패에서는 성공한 전체 결과를 뜻하는 `summary.json`과
`index.html`을 남기지 않습니다. 파일 여러 개가 하나의 원자적 작업으로 저장되는
것은 아니므로 강제 프로세스 종료·전원 장애 뒤에는 일부 파일이 남을 수 있습니다.
반드시 명령이 정상 종료한 결과만 소비하세요.

통합 실행 동안 Python 소켓 연결·UDP 전송·DNS 진입점을 거절하고, 성공과 실패
후 원래 함수를 복구합니다. 이 보호는 회귀 테스트를 위한 것으로 OS 샌드박스나
악성 네이티브 코드 차단 기능은 아닙니다. 프로세스 전체 함수를 잠시 교체하므로
독립 CLI로 실행하고 다른 네트워크 작업과 같은 Python 프로세스에서 병행하지
마세요. 서버·스케줄러·브로커 요청을 실행하지 않습니다. 주문 관측값 수집 단계는
가짜 HTTP 응답 한 번만 사용합니다.

## CI 커버리지 확인

필수 테스트와 오프라인 검증 뒤 `coverage.xml`을 Python 버전별 Actions 산출물로
보존합니다. 이름은 `coverage-python-3.10-attempt-1` 형태이고, 재실행 시 attempt가
바뀝니다. 커버리지 파일이 없거나 산출물 저장이 실패하면 해당 단계는 실패합니다.

외부 Codecov 전송은 `CODECOV_UPLOAD_ENABLED` Actions 변수 값이 `true`일 때만
시도합니다. 변수가 없으면 건너뛰며 GitHub 산출물은 계속 생성합니다. 이 변경은
Codecov 계정을 연결하거나 비밀값을 설정하지 않습니다. 명시적으로 켠 전송이
실패해도 기존과 같이 경고로 남고 필수 테스트의 실패를 숨기지는 않습니다.

공식 계약은 [Upload Artifact](https://github.com/actions/upload-artifact/releases/tag/v7.0.2)와
[GitHub Actions 컨텍스트](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)를
확인했습니다. 완료한 리비전의 결과는 [검증 기록](PAPER_LAB_VERIFICATION_2026_11_21.md)과
해당 커밋의 Actions 실행에서 확인하세요.
