# Rust 공개 시세 관측과 전략 분리

Rust `master`/`worker`는 공개 시세와 전략 신호를 관측하는 경로입니다.
현금·수수료·원가·위험 한도의 통합 원장이 완성되기 전까지 매수·매도 실행은
명시적으로 거부합니다. 기존 Python [paper lab](PAPER_LAB.md)은 독립적인
코인·KRW·USD 모의 원장과 재생 감사 경로를 계속 제공합니다.

## 이번에 연결한 흐름

1. 활성 전략의 거래소·종목·주기·계산 파라미터를 검증합니다.
2. Binance ticker와 kline 구독을 연결 전에 중복 없이 준비합니다.
3. 수신자를 먼저 등록하고 하나의 작업에 WebSocket 소유권을 넘깁니다.
4. raw/combined 형식과 필수 필드·시각·OHLCV를 검증합니다.
5. 마감 봉을 종목·주기·거래소 시작 시각으로 구분해 SQLite에 저장합니다.
6. 그 종목과 주기를 담당하는 전략만 새 봉을 소비합니다.
7. 가격과 관측 신호·상태를 대시보드에 전달합니다.

시세 연결 실패는 임의 가격으로 대체하지 않습니다. 수신 채널 종료·이벤트
유실·잘못된 시세·저장 충돌은 관측을 중단합니다. 종료 시 소켓 작업을 취소하고
회수하며, 기존 주문·포지션을 다시 저장하거나 손익을 변경하지 않습니다.

## 데이터 계약

| 항목 | 처리 |
|---|---|
| 종목 | 설정의 `BTC/USDT`와 `btcusdt`를 `BTCUSDT`로 정규화; 지원 범위는 ASCII 영숫자 종목 |
| 주기 | `1s`, `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `12h`, `1d`, `3d`, `1w`, `1M` |
| 봉 시각 | 거래소 `k.t` 시작 시각; 로컬 수신 시각으로 대체하지 않음 |
| 마감 여부 | `k.x=true`인 봉만 전략·DB에 전달 |
| 숫자 | 유한한 양수 OHLC, 일관된 고저 범위, 유한한 비음수 거래량 |
| 봉 길이 | 고정 주기는 시작·종료 간격 검증; 월봉은 UTC 달력 월 경계 검증 |
| 중복 봉 | 저장된 값까지 같은 경우 쓰기 없이 성공; 전략은 다시 소비하지 않음 |
| 상충 봉 | 같은 종목·주기·시각의 OHLCV가 다르면 오류; 최초 기록 유지 |
| 과거 봉 | 전략의 현재 커서를 되돌리거나 계산 버퍼를 갱신하지 않음 |
| 다른 종목·주기 | 해당 전략의 계산 버퍼와 커서에 영향을 주지 않음 |

새 `closed_candles` 테이블은 `(symbol, timeframe, timestamp)` 복합 키를
사용합니다. 예전 `candles` 테이블은 보존하며 주기가 없는 옛 기록을 임의로
분류하거나 새 테이블에 복사하지 않습니다. 직렬화된 예전 Candle을 읽을 수는
있지만, 주기 누락 상태는 새 관측·저장 계약을 통과하지 못합니다.

## 설정과 실행

`apps/server/config/bot.toml`의 `mode = "paper"`를 유지합니다. 현재 Rust
관측 경로는 Binance만 지원하고 적어도 하나의 전략이 활성화되어야 합니다.
활성 전략 이름은 서로 달라야 하며, UTF-8 기준 1~256바이트이고 제어 문자를
포함하지 않아야 합니다. 관측 메시지의 길이·문자 계약을 시작 전에 검사합니다.
MA 기간은 양의 정수이며 fast가 slow보다 작아야 합니다. RSI 기간도 양의
정수이고 `0 <= oversold < overbought <= 100`을 만족해야 합니다. 계산 버퍼
상한은 기간 10,000개입니다. 잘못된 명시적 값은 기본값으로 바꾸지 않습니다.

로컬 개발 환경에서 빌드·회귀 검증:

```sh
cd apps/server
cargo test --locked
cargo build --release --locked
```

공개 시세 관측 서버를 직접 시작하려면 같은 디렉터리에서 실행합니다.
이 명령은 네트워크와 로컬 대시보드를 시작하며 오프라인 검증 명령과 구분됩니다.

```sh
./target/release/trading-server master --config-path config --web-port 8080
```

예제 설정은 `testnet=true`이며, 실제 Binance 엔드포인트 연결 여부는 사용자의
실행 환경에서 별도로 확인해야 합니다. 이번 검증은 기록한 입력과 로컬
WebSocket을 사용했습니다. API 키는 필요하지 않으며 공개 관측 클라이언트에
계좌 자격 증명을 전달하지 않습니다.

## 대시보드와 상태 API

초기 화면은 워커와 시세 수신 대기 상태입니다. `/health`는 현재 연결 세대의
워커가 `observation_only`를 보고하고, 마스터가 유효한 가격과 하트비트를 각각
최근 90초 이내에 수신한 경우에만 HTTP 200을 반환합니다. 나머지 상태는
HTTP 503입니다. `/api/status`에는 상태·이유·수신 경과 시간과
`execution_enabled: false`가 함께 표시됩니다.

`feed_unavailable`, `feed_halted`, `stopped`는 같은 워커 연결 세대 안에서
유지됩니다. 뒤늦은 하트비트가 중단을 정상으로 바꾸지 않으며, 이전 워커의
메시지·종료 이벤트가 새 연결 상태를 덮어쓰지 않습니다. 가격은 수신한 값만
표시하고, 오래된 값은 마지막 관측값으로 구분합니다. 계좌·포지션·주문 조회는
`available: false`로 반환하며 가상의 잔고나 수익률을 표시하지 않습니다.

브라우저가 없어도 마스터는 상태를 보유합니다. 새 화면은 현재 스냅샷을 받고,
WebSocket 알림과 5초 간격 상태 조회로 상태를 갱신합니다. 마스터 프로세스
식별자와 상태 revision으로 서버 재시작·지연 응답을 구분합니다.
서로 다른 프로세스의 상태는 순차 HTTP 조회가 선택하며, 이전 프로세스의
WebSocket 알림이 현재 화면을 덮어쓰지 않도록 제한합니다.
이 상태 검사는 마스터의 **수신 시점**을 기준으로 하며, 거래소 이벤트 자체의
최신성이나 경제 원장·실제 계좌의 정확성을 증명하지 않습니다.

## 연결 종료와 복구 범위

어댑터는 명시적 재연결 때 기존 구독을 보존합니다. Worker 자체는 연결 종료나
이벤트 유실 이후 자동으로 재접속하지 않고 중단합니다. 연속 실행을 추가하려면
재시도 예산·지연·누락 봉 백필·전략 재가열·중복 신호 기준을 함께 구현해야 합니다.
전략 커서는 현재 프로세스 메모리에 있으며 재시작 때 과거 봉을 자동 재생하지
않습니다.

연결·프로세스 심박은 거래소 시세의 신선도 증명이 아닙니다. 서버가 조용히 열린
소켓을 유지하는 상황의 수신 정지 감시, 거래소 시각과 로컬 시각의 차이, 미래·오래된
시세 거부와 다일 운영 검증은 남아 있습니다. 자동매매 수익이나 실제 체결·잔고를
검증한 경로로 해석하면 안 됩니다.

## 근거와 검증

프로토콜 기준은 Binance 공식
[WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)의
raw/combined 형식, UTC kline 필드와 주기, ping/pong 계약입니다.
실행 결과와 재현 기록은
[2026-11-23 검증 문서](RUST_PUBLIC_FEED_VERIFICATION_2026_11_23.md)에 정리합니다.

## Bounded startup and per-stream observation watchdog

The worker attempts its initial public WebSocket connection at most three
times (1s and 2s backoff; shutdown can interrupt). Invalid endpoint validation
fails before retrying. After a successful connection the status stays
initializing until a fresh ticker has arrived for **every** enabled symbol.
Unrelated/identical retransmitted tickers do not refresh liveness. Signals
received during initialization are not published. Every subscribed ticker
must remain fresh: source timestamps may be at most 90s old or 5s in the
future, and each subscription must receive a new ticker within 90s.
Regressing timestamps and conflicting last prices at equal source time halt.
A 90s transport-frame watchdog and 5s worker watchdog prevent a silent
connection from appearing indefinitely active.

For closed bars the in-process cursor checks the exact next opening
timestamp (including calendar months). The close must be recent, with the
same 90s age and 5s future-skew limits. Duplicate current-bar identity still
requires the immutable SQLite overlap check. A gap, regression, conflicting
payload or overdue next bar halts before advancing the strategy. The first
bar deadline uses one interval plus grace; no assumption is made about an
unqualified historical candle. All of these are observation gates, not
proof of tradable quotes or real exchange health.

Automatic mid-session resumption is deliberately not enabled: the legacy
closed-candle table does not pin testnet versus production venue identity.
Do not replay it into a new session without verified provenance and
backfill. The separate public REST archive is designed for audited history
capture, not automatic WebSocket promotion. Private operations and worker
economic execution remain unavailable. New watchdog regression tests use
injected times and loopback events; no real-feed availability is claimed.
