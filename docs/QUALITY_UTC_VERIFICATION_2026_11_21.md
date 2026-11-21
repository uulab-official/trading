# 포맷 필수 검사와 UTC 호환성 검증 — 2026-11-21

## 결과

코드 포맷과 import 정리를 완료하고 Black·isort를 필수 CI 검사로 바꿨습니다.
실행 중인 레거시 UTC 시계 호출은 지원되는 API로 교체했으며, 기존 주문·포지션·
상태·체크포인트의 시간 표현을 유지했습니다. 과거 시세 수집 시작점이 호스트
시간대에 따라 이동하던 결함도 수정했습니다.

기준 리비전은 `8aaaf7159834f9a8b0ff23148b39336ecb538001`입니다. 이 문서는 그 이후
변경의 **로컬 실행 증거**입니다. 원격 완료 여부는 새 커밋과 일치하는
[GitHub Actions 실행](https://github.com/uulab-official/trading/actions/workflows/ci.yml)에서
Python 3.10/3.11/3.12, 필수 lint, coverage 산출물, Docker를 각각 확인해야 합니다.

## 동작 변경과 호환성

| 영역 | 변경과 확인 |
| --- | --- |
| 주문·포지션 | 생성·갱신·취소·체결 동기화·종료 시간을 명시적인 UTC에서 읽고, 기존 naïve datetime 형식 유지 |
| 일일 손실·쿨다운 | UTC 날짜 경계 및 재시작 시 비교 의미 유지; 현지 자정이 먼저 와도 UTC 회계 날짜는 바뀌지 않음 |
| 상태 저장 | SQLite `updated_at`과 일반 상태 JSON의 기존 naïve ISO 문자열 유지 |
| 엄격한 v1 체크포인트 | 저장 시 명시적인 `+00:00`, 복구 후 기존 naïve UTC 런타임 표현 유지 |
| 전략 | 직접 `analyze()`의 naïve UTC와 `on_candle()`의 캔들 기반 aware UTC 표현을 각각 유지 |
| 과거 시세 수집 | aware UTC에서 epoch 밀리초를 계산하여 호스트 시간대 의존성 제거 |
| 모니터 | 기존 WebSocket·상태 응답의 ISO 문자열과 Telegram의 UTC 문구 유지 |

Python은 naïve datetime의 `.timestamp()`를 현지 시간으로 해석합니다. 기존 코드는
UTC의 숫자 시각에서 timezone 정보가 없는 값을 만든 뒤 이 변환을 적용했습니다.
서버가 UTC+9이면 요청 시작 epoch가 9시간 빨라지고, UTC−5이면 5시간 늦어지는
실패를 고정 시계와 가짜 거래소로 재현했습니다. 수정 후 UTC·UTC+9·UTC−5에서
최초 및 후속 페이지 요청의 epoch가 모두 같습니다.

새 코드에서는 Python 3.10에도 있는 `timezone.utc`를 사용합니다. 기존 저장 자료를
새 시간 형식으로 마이그레이션하지 않습니다. 모니터 테스트는 mock route 등록과
실제 handler 직접 호출로 시간 형식을 확인하며, HTTP 프레임워크 통합 테스트나
Telegram 메시지 송신을 수행한 것은 아닙니다.

## 필수 CI 검사와 도구

| 도구 | 고정 버전 | 실행 환경 |
| --- | --- | --- |
| pytest | 9.2.0 | Python 3.10–3.12 테스트 행렬 |
| pytest-asyncio | 1.5.0 | 위와 동일 |
| pytest-cov | 7.1.0 | 위와 동일 |
| Black | 26.10.1 | Python 3.11 이상 lint 환경 |
| isort | 9.0.2 | 위와 동일 |
| flake8 | 7.5.0 | 위와 동일 |

직접 사용하는 테스트 도구는 `requirements-test.txt`, lint 도구는
`requirements-lint.txt`에 고정했습니다. 전체 런타임 전이 의존성의 lockfile을
만든 것은 아닙니다. Black 설정에도 요구 버전을 명시하고, isort의 기준 Python은
3.10으로 고정했습니다. 기존 줄 길이 100을 유지합니다.

`packages/`, `apps/`, `tools/`의 Python 파일 148개가 검사 대상입니다. isort와
Black 적용으로 147개 파일의 내용이 바뀌었으며, Black은 이 중 145개를 다시
포맷했습니다. 포맷 적용 후 Black은 **148개 모두 변경 불필요**, isort는 종료 코드
0이었습니다. 기존 fatal flake8 선택 `E9,F63,F7,F82`도 필수로 유지합니다.

워크플로의 실제 셸 명령을 실패 코드 23을 내는 stub으로 실행한 회귀 검증에서,
수정 전 Black·isort는 `|| true` 때문에 0을 반환했습니다. 수정 후 세 lint 도구
모두 23을 그대로 전달했고 세 소스 루트가 검사 인자에 포함됐습니다. Docker는
테스트·lint 성공에 계속 의존합니다.

pytest는 이 프로젝트에서 사용하는 asyncio·coverage 플러그인을 명시적으로
불러오며, 환경에 우연히 설치된 플러그인의 자동 로딩을 끕니다. asyncio fixture의
기본 이벤트 루프 범위는 function으로 명시합니다. 프로젝트의 deprecated UTC
호출 경고는 숨기지 않고 **테스트 오류로 처리**합니다. 별도의 의도적 경고 probe가
실제 종료 코드 1을 내는 것과, 설정된 플러그인만 로딩되는 것을 확인했습니다.

## 로컬 실행 결과

Python 3.12.14에서 실제 실행했습니다.

| 검사 | 결과 |
| --- | --- |
| 전체 pytest + 4개 패키지 coverage | **751 passed, 1523 subtests passed**, 경고 **0건**, 36.42초 |
| 네트워크·DNS 금지 확장 suite | **738 tests, OK**, 27.374초 |
| core·legacy 집중 회귀 | **80 passed, 39 subtests passed**, 경고 0건 |
| strategy·backtest·monitor 집중 회귀 | **90 passed, 109 subtests passed**, 경고 0건 |
| CI 실패 전달 계약 | **6 passed, 3 subtests passed**, 경고 0건 |
| Black / isort | 전체 148개 파일 검사 통과 |
| fatal flake8 | 0건 |
| compileall / `git diff --check` | 통과 |

앞선 동일 환경의 전체 실행은 741개 테스트·1501개 subtest와 6,924개 경고였습니다.
이번 실행은 테스트 10개와 subtest 22개가 늘었고, deprecated UTC 호출 및 불필요한
플러그인 자동 로딩에 따른 경고가 사라졌습니다.

```bash
python -m pip install -r requirements-test.txt
python -m pytest packages/ apps/server/tests/ -q \
  --cov=trading_core --cov=trading_data \
  --cov=trading_strategy --cov=trading_backtest --cov-report=xml
python tools/offline_checks.py --include-backtest

# Python 3.11 이상 환경
python -m pip install -r requirements-lint.txt
flake8 packages/ apps/ tools/ --count --select=E9,F63,F7,F82 --show-source --statistics
black --check packages/ apps/ tools/
isort --check-only packages/ apps/ tools/
```

## 독립 검토

별도 리뷰어가 전체 변경과 포맷 전 소스를 비교했으며, 수정을 요구하는
critical·important·minor 결함은 발견하지 않았습니다. CI 경로·설정으로 테스트
751개가 실제 수집되는 것을 확인하고, `TZ=America/New_York`에서 관련 모듈 8개를
다시 실행해 **65개 테스트·26개 subtest, 경고 0건**을 확인했습니다.

포맷 대상 148개 파일 모두 Python 3.10 문법으로 파싱됐습니다. 연속된 import와
docstring 공백을 정규화하면 포맷 전후 AST가 같았고, import 순서가 바뀐 168개
블록에는 중복 바인딩 이름이 없었습니다. 네트워크를 차단한 별도 프로세스에서도
core·data·strategy·backtest와 monitor notifier 모듈을 불러왔습니다.
이 검토에서 직접 실행한 인터프리터는 Python 3.12.14이며, Python 3.10·3.11의
실제 실행은 게시 커밋의 CI 행렬에서 별도로 확인합니다.

## 첫 원격 실행에서 확인한 추가 정리

`bd7ab91e98e246b52b99d4d1ff9b080bb2a7dee2`의
[CI #466](https://github.com/uulab-official/trading/actions/runs/44909644792)에서
Python 3.10·3.11·3.12 각각 **751개 테스트·1523개 subtest**와 기본 offline 검증
**653개**가 통과했습니다. pytest와 offline 실행의 경고는 0건이었습니다.
필수 Black·isort도 통과했고 세 coverage 산출물이 저장됐습니다.

별도로 CI 인프라와 패키지 빌드 단계에서 경고가 확인되어 다음을 보완했습니다.

| 확인된 원인 | 추가 변경 |
| --- | --- |
| 구형 액션의 Node 런타임을 runner가 강제 전환 | checkout `v5.1.0`, setup-python `v6.3.0`, cache `v5.1.0`, setup-buildx `v4.4.1`로 고정 |
| core·strategy·backtest·monitor의 metadata가 없는 README를 참조 | 네 패키지의 역할·설치·검증 범위를 설명하는 README 추가 |

네 액션의 공식 `action.yml`에서 `node24`와 사용 중인 입력 호환성을 확인했습니다.
최초 Node24 지원 major의 유지보수 버전을 선택했으며, CI에 관측된 runner
`2.338.2`는 요구 최소 버전 `2.327.1` 이상입니다. 액션 외부의 Buildx·BuildKit
버전까지 고정하는 변경은 아닙니다. 기존 테스트 조건·coverage·명시적 이미지
게시 조건은 유지합니다.

첫 실행의 `punycode`·`url.parse` 경고 제거 여부는 새 커밋의 실제 로그로
검증합니다. setuptools의 editable-install 안내는 정상적인 개발 설치 방식의
설명이며, 경고를 숨기기 위해 설치 모드를 바꾸지 않았습니다.

이후 Rust 컴파일 경고와 CLI 구조는 별도 변경에서 정리했습니다.
[Rust 품질 검증 기록](RUST_QUALITY_VERIFICATION_2026_11_21.md)을 참고합니다.

후속 변경의 로컬 검증에서는 CI 계약 **6개 테스트·3개 subtest**가 통과했습니다.
`pip wheel --no-deps --no-build-isolation --no-index`로 네 패키지 wheel을 만들고,
각 `METADATA`에 Markdown README 본문이 포함되는 것과 누락 경고가 없는 것을
확인했습니다. 독립 검토에서도 워크플로의 버전 참조 10곳 외 모든 YAML 값이
동일하고 README 내부 링크가 유효함을 확인했으며, 수정할 사항은 없었습니다.

## 전체 모의 실험 재실행

`python tools/paper_lab.py --output-dir NEW_DIRECTORY`로 보존한 실행 시각은
**2026-11-21 22:31:14 KST** (`2026-11-21T13:31:14.701271+00:00`)입니다.

7개 최상위 검증이 모두 true였습니다. 합성 BTC/USDT 90개 봉, KRW 주식 55개 봉,
USD 주식 55개 봉을 처리하고 각 포트폴리오에서 가상 체결 2건을 확인했습니다.
재시작·중복 재생 방지·원장 독립 감사·세 백업의 깊은 재생과 주문 생명주기·대조·
복원 실험도 통과했습니다. 실제 증권사 요청과 Python 네트워크 시도는 각각 0건이며
서로 다른 통화의 금액을 합산하지 않았습니다.

토스·KIS의 허용된 인증 변수 8개는 이 실행에서도 모두 없었습니다. 실제 OAuth,
시세 권한, VTS 계좌 연결이나 실제 체결 검증이 완료된 상태는 아닙니다. 기존
[모의 실험 가이드](PAPER_LAB.md)와 [연결 준비 진단](BROKER_READINESS.md)을 사용합니다.

## 공식 근거

- [Python 3.12 datetime: UTC와 timestamp 변환](https://docs.python.org/3.12/library/datetime.html)
- [Black 설정과 required-version](https://black.readthedocs.io/en/stable/usage_and_configuration/the_basics.html)
- [isort 릴리스 정책](https://pycqa.github.io/isort/docs/major_releases/release_policy.html)
- [pytest 명시적 플러그인 로딩](https://docs.pytest.org/en/stable/how-to/plugins.html)
- [pytest-asyncio 설정](https://pytest-asyncio.readthedocs.io/en/stable/reference/configuration.html)
- [Checkout v5.1.0 실행 사양](https://github.com/actions/checkout/blob/v5.1.0/action.yml)
- [Setup Python v6.3.0 실행 사양](https://github.com/actions/setup-python/blob/v6.3.0/action.yml)
- [Cache v5.1.0 실행 사양](https://github.com/actions/cache/blob/v5.1.0/action.yml)
- [Setup Buildx v4.4.1 실행 사양](https://github.com/docker/setup-buildx-action/blob/v4.4.1/action.yml)
