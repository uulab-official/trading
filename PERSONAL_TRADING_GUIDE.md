# 개인용 오프라인 모의거래 체험 가이드

현재 직접 실행해 확인할 수 있는 경로는 **합성 캔들 파일을 재생하는 Python CLI**입니다.
거래소 시세에 연결해 계속 작동하는 Paper Trading 서비스나 실거래 프로그램이 아닙니다.
API 키, 거래소 계정, Telegram, Docker 없이 실행합니다. 체험의 목적은
체결 기록과 저장·재시작 동작을 확인하는 것이며, 수익성을 판단하는 것이 아닙니다.

## 1. 준비

Python 3.10 이상, PyYAML 6 이상이 필요합니다. 체크포인트 저장은 로컬 POSIX
파일 잠금을 지원하는 Linux/macOS 경로에서 사용하세요. 검증 환경은 Linux입니다.
Windows와 공유·분산 파일시스템의 잠금 동작은 검증하지 않았습니다.

처음 받는 경우:

```sh
git clone https://github.com/uulab-official/trading.git
cd trading
```

이하 명령은 `README.md`, `packages/`, `apps/`가 있는 저장소 루트에서 실행합니다.
이미 받은 저장소에서는 그 루트로 이동하세요. `trading/trading-monorepo`라는
추가 하위 폴더로 들어가지 않습니다.

의존성이 없다면 별도 가상환경에 준비합니다. 설치 단계에는 패키지 다운로드가
필요할 수 있지만, 아래 재생은 공급된 파일만 사용합니다.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-offline.txt
```

## 2. 첫 재생과 결과 확인

가장 짧은 실행 경로는 저장소의 오프라인 체험 실행기입니다.

```sh
./test-trading.sh
```

결과 JSON은 표준 출력, 합성 데이터 안내는 표준 오류로 나옵니다. 기본 체크포인트
폴더는 검증된 재생이 생성합니다. 이 실행기는 Python/PyYAML을 확인하고 기존 CLI를
실행합니다. 자동 설치나 컨테이너·서비스 시작, 인증정보 설정은 하지 않습니다.

위 명령은 화면에 출력하고 `result.json`은 만들지 않습니다. 아래 재실행 비교까지
확인하려면 첫 실행이 만든 폴더에 결과를 저장하세요. 같은 체크포인트의 완료된
재생을 확인하는 것이므로 모의 체결이 추가되지 않습니다.

```sh
./test-trading.sh > data/owner-trial/result.json
```

다른 폴더에서 실행해도 기본 파일·저장 위치는 저장소 기준입니다.
`--fixture`와 `--checkpoint`로 지정한 상대 경로는 명령을 입력한 폴더 기준이고,
공백이 있는 경로는 따옴표로 감싸세요. `--fixture=PATH`, `--checkpoint=PATH` 형식도
같은 경로 기준을 사용합니다.

```sh
/path/to/trading/test-trading.sh \
  --fixture "local data.json" --checkpoint "trial output/checkpoint.json"
```

공급 파일을 바꿔도 로컬 파일 재생입니다. 외부 파일이 실제 시장의 과거 데이터인지
이 실행기가 검증하거나 시세를 다운로드하지 않습니다. 다음의 직접 CLI 명령도
계속 사용할 수 있습니다.

```sh
export PYTHONPATH=packages/core:packages/data:packages/strategy:apps/server
mkdir -p data/owner-trial
python -m trading_bot.paper_runner config/examples/offline_fixture.json \
  --checkpoint data/owner-trial/checkpoint.json \
  > data/owner-trial/result.json
python -m json.tool data/owner-trial/result.json
```

제공된 파일과 기본 옵션에서 확인할 값:

- `mode`: `paper`
- `status`: `fixture_complete`
- `bars_processed`: `90`
- `trades`: 모의 매수 1건과 모의 매도 1건, 합계 2건
- `holdings`: `{}` (이 합성 예제는 마지막에 보유 수량이 없음)
- `pending_signal`: `null`

`fixture_complete`는 파일 재생이 끝났다는 뜻입니다. 이후 시세를 기다리는
상시 프로세스가 아닙니다. `cash`, `equity`, `total_fees`, `total_return_pct`는
합성 가격과 설정된 수수료·슬리피지에서 계산한 값이며, 실제 잔고나 예상 수익률이 아닙니다.
`python-telegram-bot not installed` 안내가 보이더라도 이 경로에는 Telegram이
필요하지 않습니다. 성공 여부는 종료 코드 `0`과 결과 JSON으로 확인합니다.

## 3. 같은 체크포인트로 재실행

첫 결과를 남겨 두고 **같은 파일·옵션·체크포인트**로 다시 실행합니다.

```sh
./test-trading.sh > data/owner-trial/resumed.json
python3 - <<'PYCODE'
import json
from pathlib import Path
first = json.loads(Path("data/owner-trial/result.json").read_text())
resumed = json.loads(Path("data/owner-trial/resumed.json").read_text())
assert first == resumed, "첫 실행과 재실행 결과가 다릅니다"
assert resumed["bars_processed"] == 90
assert len(resumed["trades"]) == 2
print("재실행 일치: 90개 봉, 중복 없는 모의 체결 2건")
PYCODE
```

완료된 체결이 추가되지 않고 같은 결과가 나와야 합니다. 중단된 재생도 같은 명령으로
마지막에 저장된 봉부터 검증·재개합니다. 원본 캔들, 전략, 수수료 또는 위험 설정을
바꾼 실험에는 **새 체크포인트 경로**를 사용하세요. 기존 체크포인트를 직접 고치거나
잠금 파일을 삭제해 검증을 우회하지 마세요.

이 예제는 빠르게 끝나므로 Ctrl+C 중단을 체험자가 맞춰 누를 필요는 없습니다.
실제 신호 중단·원자적 저장·경쟁 실행은 아래 자동 검사에 포함됩니다.

## 4. 자동 검사

설치된 CLI 검사는 로컬 서버 패키지를 임시 디렉터리에 빌드하므로 pip,
setuptools 61 이상, wheel도 필요합니다. 필요하면 가상환경에서 준비합니다.

```sh
python -m pip install 'setuptools>=61' wheel
python tools/offline_checks.py
```

이 검사는 네트워크 연결과 DNS를 금지한 채 실행되며, 원장·위험 제한·체크포인트·
CLI·중단 후 재시작 등을 확인합니다. 선택적으로 pandas/numpy가 준비된 환경에서는
`python tools/offline_checks.py --include-backtest`로 별도 백테스트 회귀 검사도 실행할 수 있습니다.
통과해도 실거래나 거래소 연결이 검증되었다는 뜻은 아닙니다.

## 5. 결과를 어디서 보나요?

2절의 결과 저장과 3절의 비교를 따라간 결과는 `data/owner-trial/result.json`과
`resumed.json`입니다. 빠른 실행기만 이용했다면 결과는 터미널의 표준 출력입니다.
저장 상태는 `checkpoint.json`이며, 모두 이 CLI의 JSON 데이터입니다.

- 현재 `infra/docker/docker-compose.yml`은 Rust `trading-server`를 실행합니다.
  Python 재생 CLI를 실행하거나 JSON 체크포인트를 가져오지 않습니다
- Rust 웹의 `/api/positions`, `/api/orders`는 현재 빈 배열을 반환합니다.
  화면의 빈 목록이나 `running` 상태로 이 체험의 체결 여부를 판단하지 마세요
- 별도 Python 대시보드 `packages/monitor/web_dashboard.py`는
  `/app/data/trading.db`의 `bot:main:*` 상태를 읽습니다.
  이 JSON 체크포인트를 읽지 않으며, 현재 Compose에 별도 서비스로 연결되어 있지 않습니다
- 레거시 Python 봇의 버전 관리 SQLite 체크포인트도 별도 경로입니다.
  이 체험의 JSON이나 Rust DB와 서로 바꾸어 사용할 수 없습니다

대시보드 통합을 위해 파일 확장자만 바꾸거나 JSON을 SQLite 경로에 복사하지 마세요.
[현재 대시보드 경계](docs/WEB_DASHBOARD.md)와
[레거시 상태 복구 범위](docs/LEGACY_RISK_LIMITS.md)를 참고하세요.

## 6. 실패했을 때

- 종료 코드 `2`: 입력·옵션·체크포인트 검증 실패. 원래 설정을 확인하고 저장본을 보존하세요
- 종료 코드 `3`: 같은 체크포인트를 다른 실행이 사용 중. 해당 실행이 끝난 뒤 다시 시도하세요
- 종료 코드 `4`: 경로·권한 등 로컬 파일 오류. 경로와 저장 공간을 확인하세요
- 종료 코드 `130`: 사용자 중단. 같은 명령으로 검증·재개하세요

오류 보고에는 종료 코드와 민감정보를 제거한 오류 문구만 우선 공유하세요.
키·토큰·계정 비밀번호를 설정 파일이나 이 체험 결과에 넣지 마세요.
[상세 CLI·복구 문서](docs/OFFLINE_PAPER_RUNNER.md)에 옵션과 제한이 정리되어 있습니다.

## 다음 단계의 경계

이 체험은 기간이나 수익률에 따라 실거래로 넘어가는 절차가 아닙니다.
현재 복원된 데이터 패키지의 실시간 피드는 `LiveMarketDataUnavailable`로 차단됩니다.
시장 연결 모의운영, 대시보드 통합, 실제 거래소 호환성, 실거래 활성화는 각각 별도 구현과
검증이 필요합니다. 이 안내에는 API 키 발급, 외부 배포, 실제 주문 실행 단계가 없습니다.
