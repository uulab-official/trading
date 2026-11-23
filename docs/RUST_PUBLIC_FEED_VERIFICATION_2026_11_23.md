# 공개 시세·전략 분리·감독기 검증 — 2026-11-23

기준 원격 커밋은 `5105a9e9c20bc57f9bc67b7a002ac3d3436f7e0b`다.
로컬 기준 트리도 원격 트리 `e1d662d0e7c5524d61396c9ddc04040bb069c1f2`와
같음을 확인했다. 이번 작업은 Rust 공개 시세 관측 경로와 Python paper 감독기의
운영 결함을 수정한다. 실제 증권사 인증·실주문·프로덕션 배포는 검증 범위가 아니다.

## 수정 내용

| 문제 | 변경 후 |
|---|---|
| 수신자가 없는 Connected 알림이 연결 실패로 반환됨 | 상태 알림의 수신자 수와 실제 연결 성공을 분리 |
| Combined stream의 `data`를 읽지 못함 | raw/combined 파싱, stream 이름과 본문의 종목·종류·주기 일치 검사 |
| 잘못된 시세를 0으로 치환 | 필수 필드·유한수·OHLC 범위·거래량·거래소 시각 검증 후 이벤트 생성 |
| 봉 시각을 로컬 현재 시각으로 기록 | 거래소 봉 시작 시각과 주기 보존; 마감 봉만 전달 |
| 연결 상태와 실제 소켓 수명이 어긋남 | Close/EOF/오류 때 상태 정리, 명시적 재연결 때 구독 보존, ping/pong 처리 |
| Worker가 메시지 루프 동안 클라이언트 잠금을 보유 | 소켓을 별도 작업에 소유권으로 전달하고 수신자를 먼저 확보 |
| 모든 봉을 모든 전략에 전달 | 종목·주기별 전략과 독립 커서; 중복·과거 봉은 재소비하지 않음 |
| 같은 시각의 다른 주기 봉이 서로 덮어써짐 | 새 `closed_candles` 복합 키, 동일 재수신 무변경, 상충 값 거부 |
| 연결 실패를 임의 가격 화면으로 대체 | 명시적 중단 상태로 종료 |
| 체결되지 않은 요청을 보유 수량으로 기록 | Rust 실행을 거부하고 관측 신호만 전달; 기존 금융 기록 보존 |
| 종료 중 부분 전송한 master 메시지 소켓이 재사용됨 | 완전한 프레임 전송 성공에만 소켓 복귀; 취소·오류 때 연결 폐기 |
| 초기·실패 상태에서도 대시보드가 Live/healthy 표시 | 현재 워커의 관측 상태와 최근 가격·하트비트 수신을 함께 검사 |
| 브라우저 미접속·재시작·늦은 메시지로 상태 유실/역전 | 상태 보유, 연결 세대, 프로세스 식별자와 스냅샷/상태 조회 적용 |
| 설정에서 허용한 전략 이름이 관측 메시지를 깨뜨림 | 시작 전에 UTF-8 길이 1~256바이트와 제어 문자 금지 검사 |
| 감독기 즉시 종료가 이전 halted/STOP을 해제 | 루프 전에 latch 검사, 원본 중단 기록 보존 |
| 정상 종료·one-shot 전환이 실패 횟수를 초기화 | 공통 실패 예산 복구와 임계치 검사; 완료 성공 후에만 초기화 |
| heartbeat 파일이 감독기 잠금 inode를 교체 | 데이터·STOP·파생 잠금의 정규화 경로/inode 충돌을 사전 거부 |

## 회귀 재현

수정 전 Rust 기존 API 회귀 19개 중 17개가 실패했다. Binance의 14개 실패는
파싱·시각·검증·구독·연결 정리를, Worker의 3개 실패는 허위 보유 생성·삭제와
닫힌 이벤트 채널의 무한 대기를 재현했다. 기존 보장 확인 2개는 통과했다.

별도의 설정 검증 회귀 3개, 저장 회귀 6개도 수정 전에 실패했다. 독립 검토에서
발견한 전송 취소 문제는 메시지 길이 헤더를 실제로 읽은 후 본문 전송을 취소해
재현했다. 수정 후 같은 회귀가 통과하고 연결은 재사용되지 않았다.
대시보드 초기 상태·계좌/주문 미구현 표시 회귀도 수정 전에 실패했다.
실제 HTML의 JavaScript를 네트워크 없는 Node VM에서 실행해 서버 재시작과
늦은 응답 순서 문제를 재현했다. 전략 이름 회귀는 수정 전 실패를 확인하고
설정 경계 전체 15개가 통과하는 것을 확인했다.

Supervisor 회귀는 초기 중단, stopped/once/loop의 실패 예산, 임계치 축소,
잘못된 상태, 파일 별칭·hardlink·예약 잠금, 중복 감독기 진입을 포함한다.
CLI의 symlink 체크포인트가 다른 기본 STOP 경로를 찾던 추가 문제도 수정했다.

## 검증 방법

Rust는 기존 Docker와 같은 **Rust 1.99.0**을 사용한다. 애플리케이션 의존성,
`Cargo.lock`, manifest와 경고 오류화 정책은 변경하지 않는다. TCP 검증은
loopback WebSocket, master 통신 검증은 로컬 소켓쌍을 사용한다. 이 실행 환경은
이름이 있는 Unix 소켓 bind를 거부하므로, 실제 Worker 관측 실행 본체에 이미
연결된 소켓쌍을 주입했다. 전역 master 소켓이나 실제 서비스는 실행하지 않았다.

Python 전체 검증 환경에는 저장소가 선언한 monitor 의존성까지 설치한다.
최소 환경의 초기 실행에서는 미설치 Telegram 패키지 안내가 엄격한 stderr
검사를 실패시켰다. 실제 알림은 전송하지 않았으며 검증 환경에 선언된 의존성을
설치해 실행 조건을 맞췄다.

Rust 빌드 후 Python launcher 테스트가 `apps/server/target`의 대형 빌드 파일까지
복사해 임시 공간을 소진하는 문제도 발견했다. 테스트 fixture 복사에서 Rust
빌드 디렉터리를 제외했고, 기존 실행기 회귀 10개와 하위 사례 5개가 통과했다.
부하로 중단한 병렬 검증은 성공으로 계산하지 않으며, 최종 수치는 후속 완료
실행을 기준으로 기록한다.

## 최종 검증 결과

| 검증 | 결과 |
|---|---|
| Rust 1.99.0 `cargo test --locked --offline` | 107개 통과, 실패·무시·경고 0개 |
| Rust `cargo build --release --locked --offline` | 최적화 빌드 성공, 경고 0개 |
| release CLI root/master/worker/deploy/migrate `--help` | 5개 종료 코드 0; stderr·빈 작업 폴더의 생성 파일 없음 |
| Python 3.12.14 전체 pytest + coverage XML | 766개 테스트, 하위 사례 1,548개 통과 |
| 소켓/DNS 차단 offline suite, 백테스트 포함 | 753개 통과 |
| 합성 paper lab | 7개 통합 검사 통과; 실제 broker 요청 0건 |
| Black / isort / flake8 | 전체 검사 통과; Black 대상 148개 파일 |
| 변경 Rust 파일 rustfmt / `git diff --check` | 통과 |
| 실제 대시보드 JavaScript, 네트워크 없는 Node VM | 두 재시작 응답 순서, 종목 표시, stale 상태, polling 실패 확인 |

Python pytest와 offline suite에는 겹치는 테스트가 있으므로 두 수치를
독립 테스트 총수로 더하지 않는다.

선언된 Python 패키지·test/lint 의존성과 Rust 1.99.0이 설치된 환경의 재현 명령:

```sh
cargo test --locked --offline --manifest-path apps/server/Cargo.toml
cargo build --release --locked --offline --manifest-path apps/server/Cargo.toml
python -m pytest packages/ apps/server/tests/ -q \
  --cov=trading_core --cov=trading_data --cov=trading_strategy \
  --cov=trading_backtest --cov-report=xml
python tools/offline_checks.py --include-backtest
python tools/paper_lab.py
python -m black --check packages/ apps/ tools/
python -m isort --check-only packages/ apps/ tools/
python -m flake8 packages/ apps/ tools/ \
  --count --select=E9,F63,F7,F82 --show-source --statistics
```

Rust의 `--offline`은 의존성 캐시를 사용하며, 회귀의 loopback 연결 자체를
차단한다는 의미는 아니다. Python offline suite는 별도로 소켓 연결과 DNS를
금지한다. GitHub Actions는 Python 3.10·3.11·3.12, lint, Docker 내 Rust
테스트·release 빌드와 네트워크 없는 CLI help를 필수 게이트로 실행한다.

## 독립 검토

전체 변경 검토에서 전송 취소에 의한 부분 프레임 재사용과 대시보드의 고정
`Live/Running/healthy` 표시가 발견되었다. 전송 취소는 실패 재현과 수정 후
통과로 확인했으며, 화면 상태는 저장된 관측 상태와 연결 세대를 사용하는 방식으로
보완했다. 캔들 저장은 별도 검토를 거쳐 종목·주기·시각의 세 차원 독립성 회귀를
추가했다. Supervisor 경로·실패 예산과 전략 분리에서는 추가 차단 결함이
확인되지 않았다.
후속 검토에서는 처음 보는 이전 master 응답이 현재 master를 영구 배제하는
화면 문제와, 설정에서 허용한 이름이 신호 프로토콜에서 거부되는 불일치를
찾았다. 전자는 순차 HTTP 조회에 프로세스 선택권을 주어 회복할 수 있게 하고,
후자는 시작 시 메시지 길이·문자 계약에 맞춰 검증한다.
최종 수정 후 별도 검토자가 두 응답 순서를 실제 HTML 스크립트로 다시 실행하고
이름 길이·제어 문자 계약도 확인했다. 해당 검토 범위의 남은 차단 결함은 없다.

## 남은 범위

- 실제 Binance/Toss/KIS 서비스 응답과 계좌 인증의 검증.
- Rust의 현금·예약·수수료 자산·원가·부분 체결·중복 이벤트 경제 원장.
- 실제 실행 경로의 손실·낙폭·포지션 한도와 지속 상태 복구.
- 자동 재연결·백필·전략 재가열, 거래소 시각 기준 신선도, 수신 정지 감시와 다일 운영.
- Python 모의 원장과 Rust 운영 화면을 연결하는 검증된 공통 계좌 뷰.

프로토콜 근거는 Binance 공식
[WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)다.
[관측 가이드](RUST_PUBLIC_FEED.md), [모의 실험실](PAPER_LAB.md),
[운영 체크리스트](../PRODUCTION_CHECKLIST.md)를 함께 참고한다.
