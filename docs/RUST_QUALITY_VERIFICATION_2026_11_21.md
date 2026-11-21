# Rust 경고 제거와 CLI 호환성 검증 — 2026-11-21

## 결과와 기준

Rust 실행 파일이 공개 라이브러리를 사용하도록 정리하고, 실제 미사용 import와
비공개 항목을 제거했습니다. Rust 1.99.0의 테스트 빌드와 최적화 릴리스 빌드에서
컴파일 경고와 오류가 각각 **0건**입니다. CLI 호환성 테스트 8개와 기존 통합
테스트 2개가 통과했습니다.

기준 커밋은 `dd8cd5dd78ccb1ac974a61cdba8fa1d8dcf9ab27`입니다. 해당 커밋의
[CI #467](https://github.com/uulab-official/trading/actions/runs/44910188207)은
성공했지만 Rust 라이브러리 27개·바이너리 61개 경고를 보고했습니다. 이는 타깃별
집계이며 테스트·릴리스 사이의 중복도 있으므로 고유 문제 88개라는 뜻은 아닙니다.

이 문서는 그 이후 변경의 로컬 검증 기록입니다. 게시된 변경의 원격 완료 상태는
그 커밋과 일치하는 [CI 실행](https://github.com/uulab-official/trading/actions/workflows/ci.yml)의
Python 행렬, lint, coverage 산출물, Docker 결과를 확인합니다.

## 원인과 수정

### 공개 라이브러리 재사용

`lib.rs`가 이미 공개한 여섯 모듈을 `main.rs`에서 다시 `mod`로 선언하고 있었습니다.
이 때문에 라이브러리와 실행 파일이 같은 소스를 서로 다른 모듈 트리로 컴파일했고,
실행 파일의 비공개 트리에서 사용하지 않는 공개 API까지 dead-code 경고가 발생했습니다.

실행 파일에서 중복 모듈 선언을 제거하고 `trading_server::{core, master, worker}`를
사용합니다. CLI enum·인자 속성·기본값·실행 분기 본문은 포맷 적용 전 기준으로
기존 코드와 동일함을 확인했습니다. 공개 타입, 메서드, 오류 재노출과 이벤트
payload는 보존했습니다.

### 실제 미사용 항목

| 항목 | 변경 및 보존한 동작 |
| --- | --- |
| 미사용 import 진단 21개 묶음 | 실제 참조가 없는 import만 정리 |
| `PortfolioManager.total_value` | 초기화 뒤 읽지도 쓰지도 않는 비공개 필드 제거; 현재 포지션으로 계산하는 공개 평가 함수 유지 |
| `WorkerProcess.exchange_manager` | 사용하지 않는 저장 필드와 메모리 할당 제거; 공개 `ExchangeManager` API와 기존 Binance 경로 유지 |
| Binance 비공개 서명 helper | 호출 경로가 없는 함수와 그 import 제거; 인증·서명·주문 전송 경로를 새로 연결하지 않음 |
| Bithumb의 저장된 config | 현재 stub이 읽지 않는 저장 필드 제거; `new(ExchangeConfig)`의 공개 인자 타입과 호출 형태 유지 |
| migration 초기화 변수 | `_db`로 바꾸어 DB 초기화와 완료 로그까지의 pool 수명을 유지 |
| 비공개 sell handler 인자 | 사용하지 않는 주문 관리자 인자와 유일한 호출부의 해당 인자만 제거 |

경고를 없애기 위해 public API를 삭제하거나 이벤트 데이터를 비우지 않았습니다.
`allow(dead_code)` 또는 `allow(warnings)` 같은 억제 속성도 추가하지 않았습니다.
수정한 Rust 파일 12개만 같은 rustfmt 버전으로 포맷했으며, 전체 Rust 저장소의
포맷 검사를 새 CI 조건으로 추가한 변경은 아닙니다.

## 경고가 다시 생기면 빌드 실패

`apps/server/Cargo.toml`에 다음 정책을 추가했습니다.

```toml
[lints.rust]
warnings = "deny"
```

Cargo의 패키지별 lint 정책은 이 패키지의 타깃에 적용되며 외부 의존성의 lint
설정을 바꾸지 않습니다. 별도 임시 패키지에서 실제 설정값을 읽어 같은 정책을
적용하고, 의도적인 미사용 변수를 넣었습니다. `cargo check --offline`은 예상대로
종료 코드 **101**과 `-D warnings`에 따른 오류를 반환했습니다.

기존 Docker의 `cargo test --locked && cargo build --release --locked`도 이 정책을
사용하므로, 프로젝트의 컴파일 경고가 다시 발생하면 필수 Docker 단계가 실패합니다.
Cargo.lock, 의존성 버전, Docker의 Rust 이미지 버전과 digest는 바꾸지 않았습니다.

## CLI 검증

새 테스트는 `Cli::try_parse_from`만 호출하며 서버나 worker를 만들지 않습니다.

| 검증 내용 | 결과 |
| --- | --- |
| master 기본 config 경로·8080/9000 포트 | 통과 |
| master 긴 옵션·짧은 옵션의 명시적 값 | 통과 |
| worker의 필수 master socket | 통과 |
| worker의 기본·명시적 config 경로 | 통과 |
| deploy의 필수 binary 경로 | 통과 |
| deploy 긴 옵션·짧은 옵션 | 통과 |
| migrate 명령 분류 | 통과 |
| root·master·worker·deploy·migrate 도움말 | 모두 도움말 종료 코드 0 |

최적화한 릴리스 실행 파일로도 빈 임시 디렉터리와 최소 환경에서 다섯 도움말을
실행했습니다. 모두 `Usage:`를 출력하고 종료 코드 0, 빈 stderr, 생성 파일 0개를
확인했습니다. `migrate --help`는 migration 실행이 아닙니다.

Docker CI의 `--network none` 도움말 검사에도 `migrate --help`를 추가했습니다.
Python CI 계약 테스트는 다섯 명령의 인자 전체를 확인합니다. 워크플로 수정 전에는
누락된 migration 도움말 때문에 **1개 실패**, 수정 후 **6개 테스트·3개 subtest**가
통과했습니다. 게시·릴리스 조건과 권한은 그대로입니다.

## 실제 로컬 실행

공식 rustup 설치 프로그램의 SHA-256을 확인하고 별도 작업 디렉터리에
Rust **1.99.0**, Cargo **1.99.0**, rustfmt **1.10.0-stable**을 설치해 실행했습니다.
기존 manifest의 `rust-version = "1.75"`를 올리거나 낮추지 않았습니다. 여기서
직접 확인한 컴파일러는 1.99.0이며 최소 지원 버전 전체를 검증했다는 뜻은 아닙니다.

| 검사 | 결과 |
| --- | --- |
| `cargo test --locked` | **8개 CLI 테스트 + 2개 통합 테스트 통과**, 경고·오류 0건, 60.94초 |
| `cargo build --release --locked` | **성공**, 경고·오류 0건, 117.41초 |
| 릴리스 도움말 실행 | 다섯 경로 모두 성공, 생성 파일 0개 |
| 의도적 실패 사례 검증 | 의도적인 경고를 오류로 처리, 예상 종료 코드 101 |
| Python CI 계약 | 6개 테스트·3개 subtest 통과 |
| Python Black / isort | 전체 148개 파일 검사 통과 |
| fatal flake8 / `git diff --check` | 통과 |
| Cargo.lock 변경 | 없음 |

## 모의 실험 재실행과 검증 범위

통합 합성 모의 실험도 **2026-11-21 23:00:42 KST**
(`2026-11-21T14:00:42.399508+00:00`)에 재실행했습니다. 세 포트폴리오의 원장
감사·백업·재시작·중복 재생 방지 및 주문 생명주기·대조·복원 등 최상위 검증
7개가 모두 true였습니다. 네트워크 시도와 실제 증권사 요청은 각각 0건입니다.

별도 리뷰어가 전체 의미 변경과 포맷 전후를 확인했고 수정할 결함을 발견하지
않았습니다. 공개 라이브러리와 실행 파일의 연결, 제거된 항목의 비사용, DB 수명,
config의 일반적인 소유값 해제, lint 범위와 Docker 명령을 검토했습니다. 리뷰어는
debug 실행 파일의 다섯 도움말도 독립적으로 실행했습니다.

Rust의 현재 거래소 어댑터에는 stub·합성 응답이 남아 있습니다. 이 변경은 주문
전송·인증·실체결·상시 운영을 검증하거나 활성화한 것이 아닙니다. 토스·KIS
인증 변수 8개도 여전히 없으며, 실제 연결은 별도 인증 환경이 필요합니다.
절차는 [브로커 준비 진단](BROKER_READINESS.md)과
[프로덕션 체크리스트](../PRODUCTION_CHECKLIST.md)에 기록돼 있습니다.

## 공식 근거

- [Cargo 패키지별 lint 설정](https://doc.rust-lang.org/cargo/reference/manifest.html#the-lints-section)
- [Rust 패키지·라이브러리·바이너리 관계](https://doc.rust-lang.org/stable/book/ch07-01-packages-and-crates.html)
- [rustup 설치 위치와 도구 설정](https://rust-lang.github.io/rustup/installation/index.html)
