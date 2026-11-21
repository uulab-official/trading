# 브로커 환경변수 설정 진단

`tools/broker_readiness.py`는 Toss와 KIS VTS 기능에 필요한 **환경변수의
존재 여부**를 오프라인으로 확인합니다. 모든 설정이 있어도 인증 성공이나
거래 가능 상태를 의미하지 않습니다. 이 도구는 실제 브로커 요청을 하지
않으며 토큰을 발급·갱신하거나 주문을 제출하지 않습니다.

## 실행

저장소 루트에서 Python 3.10 이상으로 실행합니다. 외부 패키지를 사용하지
않으므로 `-S`로 사이트 패키지 로딩을 비활성화해도 동작합니다.

```bash
python -S tools/broker_readiness.py
python -S tools/broker_readiness.py --format json
python -S tools/broker_readiness.py --format text
```

기본 출력은 JSON이며, `text`는 한국어 설명과 다음 설정 단계, 기존 문서
경로를 표시합니다. 결과는 표준 출력으로만 전달하며 출력 파일 옵션은
없습니다. `--help` 또는 `-h`는 사용법을 표시합니다.

| 종료 코드 | 의미 |
| --- | --- |
| `0` | 진단 완료. 필요한 환경변수가 누락된 경우도 포함합니다. 도움말도 성공으로 종료합니다. |
| `2` | 잘못된 명령 인수 또는 진단 실패. 고정 오류 문장만 표준 오류로 출력합니다. |

종료 코드 `0`을 인증 성공이나 주문 준비 완료의 조건으로 사용하지 마세요.
진단 실패 시 부분 보고서를 성공 결과로 반환하지 않습니다.

## 확인하는 기능과 변수

조회 대상은 다음 표의 환경변수 8개로 제한됩니다. 전체 환경변수를 열거하거나
다른 이름을 조회하지 않습니다. 각 변수는 한 번 읽고, 여러 기능에서 필요한
경우 같은 존재 여부를 사용합니다.

| 기능 ID | 필요한 환경변수 | 의미 |
| --- | --- | --- |
| `toss_market_data` | `TOSS_ACCESS_TOKEN` | 기존 토큰을 사용하는 Toss 시세 수집 설정. 클라이언트 ID·시크릿은 필요하지 않습니다. |
| `toss_token_issuance` | `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` | 기존 수집기에서 명시적으로 선택하는 토큰 발급 경로의 설정. 이 진단이 발급을 허용하거나 수행하는 것은 아닙니다. |
| `kis_vts_quote` | `KIS_VTS_ACCESS_TOKEN`, `KIS_VTS_APPKEY`, `KIS_VTS_APPSECRET` | KIS VTS 현재가 조회 설정. |
| `kis_vts_order_inquiry` | `KIS_VTS_ACCESS_TOKEN`, `KIS_VTS_APPKEY`, `KIS_VTS_APPSECRET`, `KIS_VTS_CANO`, `KIS_VTS_ACNT_PRDT_CD` | KIS VTS 일별 주문내역 조회 설정. 현재가 조회용 세 변수만 있으면 계좌 관련 두 변수는 누락으로 표시합니다. |

존재 여부는 값이 문자열이고 Python의 `str.strip()` 적용 후 빈 문자열이
아닌지만 확인합니다. 누락, 빈 문자열, 공백만 있는 문자열은 `false`입니다.
Python API로 전달된 문자열 이외의 값도 `false`입니다. 문자열의 길이,
토큰 형식, 만료, 권한, 계좌번호 형식과 계좌 연결은 검증하지 않습니다.
공백이 아닌 제어문자가 있거나 실제 수집기가 거부할 문자열이어도 존재로
표시될 수 있습니다. 이러한 표시는 사용할 수 있는 인증 정보라는 뜻이 아닙니다.

Toss의 두 기능은 별도입니다. 클라이언트 ID와 시크릿만 있어도 기존 토큰
조회 기능은 누락으로 표시됩니다. 토큰 발급 항목의
`requires_explicit_opt_in: true`는 기존 수집기의 `--issue-token`이 필요하다는
설명이며, 활성화 여부를 나타내는 필드가 아닙니다. 기존 수집기에서 토큰이
제공되면 토큰 재사용 경로가 우선합니다. 실제 수집기의 동작과 토큰 취급은
[Toss·KIS 주식 페이퍼 안내](STOCK_PAPER_TOSS_KIS.md)를 확인하세요.

## JSON 계약

최상위 구조는 다음과 같습니다. `capabilities`에는 위 표 순서대로 네 항목이
포함됩니다.

```json
{
  "schema_version": 1,
  "mode": "paper",
  "live_orders_enabled": false,
  "safe_to_submit": false,
  "actual_broker_requests": 0,
  "capabilities": []
}
```

위 예시의 빈 배열은 필드 구조를 보여주기 위한 자리입니다. 실제 응답은 항상
네 기능을 포함합니다. 각 항목의 필드는 다음과 같습니다.

| 필드 | 내용 |
| --- | --- |
| `id` | 표에 정의한 고정 기능 ID |
| `required_variables` | 해당 기능에 필요한 허용된 변수 이름 목록 |
| `missing_variables` | 값이 없거나 빈 것으로 판단된 변수 이름 목록 |
| `variable_presence` | 필요한 변수 이름별 존재 여부 불리언 |
| `configuration_present` | 필요한 모든 변수의 존재 여부가 참일 때만 `true` |
| `authenticated` | 항상 `false`. 인증을 시도하거나 확인하지 않았습니다. |
| `requires_explicit_opt_in` | `toss_token_issuance`에만 포함하며 항상 `true` |

예를 들어 기존 Toss 토큰이 없는 경우 해당 항목은 다음과 같습니다.

```json
{
  "id": "toss_market_data",
  "required_variables": ["TOSS_ACCESS_TOKEN"],
  "missing_variables": ["TOSS_ACCESS_TOKEN"],
  "variable_presence": {"TOSS_ACCESS_TOKEN": false},
  "configuration_present": false,
  "authenticated": false
}
```

인증 정보와 계좌번호의 실제 값, 길이, 해시, 관련 없는 환경변수 이름,
원래 예외 메시지는 JSON이나 한국어 출력에 포함하지 않습니다. 모든 값이
설정되어도 최상위 안전 필드와 `authenticated`는 바뀌지 않습니다.

## Python API

```python
from tools.broker_readiness import inspect_environment, render_text

report = inspect_environment()
print(render_text(report), end="")
```

`inspect_environment(environ=None)`은 기본적으로 현재 프로세스 환경을
사용합니다. 테스트나 호출자가 매핑을 넘기면 그 매핑만 사용하며, 빈 매핑도
현재 프로세스 환경으로 대체하지 않습니다. 매핑은 이름을 인자로 받는
`get(name)`을 제공해야 합니다. 매핑을 조회할 수 없으면 원래 예외 문맥을
숨긴 고정 `ValueError`를 발생시킵니다.

`render_text(report)`는 이 보고서를 한국어 문자열로 변환합니다. 변수와
기능의 표시 이름은 도구의 고정 목록에서 가져오며, 불리언 존재 여부를
사용합니다. 임의의 추가 보고서 필드나 전달된 누락 이름을 그대로 출력하지
않습니다. API는 환경변수를 변경하지 않습니다.

## 다음 설정 단계

필요한 값은 운영자가 신뢰하는 로컬 세션에서 준비하고 진단을 다시
실행하세요. 실제 값을 채팅이나 진단 결과에 붙여 넣을 필요는 없습니다.
이 도구는 `.env`, 키체인, 토큰 캐시 또는 설정 파일을 읽지 않습니다.
브로커 전송 모듈도 가져오지 않습니다.

설정이 존재하는 다음 단계는 각 기능의 별도 안내를 확인하는 것입니다.
해당 수집기의 명시적 실행 옵션과 입력 검증은 이 진단과 별개입니다.

- [Toss 기존 토큰 재사용 및 명시적 토큰 발급 안내](STOCK_PAPER_TOSS_KIS.md)
- [KIS VTS 현재가 조회 안내](KIS_VTS_READONLY_QUOTE.md)
- [KIS VTS 일별 주문내역 조회 안내](KIS_VTS_ORDER_SNAPSHOT.md)

환경변수 진단은 실제 인증, 접근 권한, 계좌 연결, 실제 주문·체결 또는
시스템의 운영 준비 상태를 검증하는 절차를 대체하지 않습니다.

## 오프라인 검증

```bash
python -m pytest apps/server/tests/test_broker_readiness.py -q
```

테스트는 누락·부분 설정·전체 설정, 공백과 제어문자, 특이한 매핑, 비밀값
비노출, 고정 오류 출력을 확인합니다. 실제 `-S` CLI도 실행하며, 별도
프로세스에서 파일 열기·소켓·DNS·프로세스 시작 및 허용되지 않은 모듈
가져오기를 금지한 상태에서도 정상 진단이 완료되는지 검사합니다.
이 테스트 가드는 회귀 검증 장치이며 운영체제 수준의 격리 기능은 아닙니다.
