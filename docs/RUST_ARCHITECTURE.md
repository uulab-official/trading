# 고성능 트레이딩 엔진 아키텍처 (Rust)

## 문제 분석

**현재 Python의 한계:**
- GIL (Global Interpreter Lock) - 진정한 병렬 처리 불가
- asyncio는 단일 스레드 이벤트 루프
- CPU 집약적 계산에 부적합
- 배포 시 프로세스 완전 중단

**필요한 것:**
1. Zero-downtime 핫 스왑 (빌드 → 실행 중 교체)
2. CPU/GPU 병렬 처리 (멀티코어 활용)
3. 사용자 로컬 리소스 활용
4. Rust 수준의 성능 + 메모리 안전

---

## 최종 아키텍처: Rust + Hot Swap

```
┌─────────────────────────────────────────────────────────────────┐
│                    Trading Engine (Rust)                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    Master Process                        │  │
│  │  - 소켓 리스닝 (TCP/WebSocket)                           │  │
│  │  - 상태 관리 (SQLite/Redis)                              │  │
│  │  - 프로세스 관리 (Worker lifecycle)                       │  │
│  │  - 웹 대시보드 서버 (Actix-web)                          │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                      │
│           ┌───────────────┴───────────────┐                     │
│           │      Socket Handoff           │                     │
│           │   (Unix Domain Socket)        │                     │
│           └───────────────┬───────────────┘                     │
│                           │                                      │
│  ┌────────────────────────▼──────────────────────────┐          │
│  │                  Worker Process                   │          │
│  │  - 실제 트레이딩 로직                               │          │
│  │  - WebSocket 연결 (거래소)                         │          │
│  │  - 전략 실행 (CPU 병렬)                            │          │
│  │  - 주문 실행                                      │          │
│  │                                                   │          │
│  │  Thread Pool:                                     │          │
│  │   - Main: 이벤트 루프 (tokio)                      │          │
│  │   - Workers: 전략 계산 (rayon)                     │          │
│  │   - GPU: 백테스팅/최적화 (Vulkan/CUDA)             │          │
│  └───────────────────────────────────────────────────┘          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Zero-downtime Deploy
                              │ (Worker만 교체, Master 유지)
                              ▼
                    ┌──────────────────┐
                    │   New Worker     │
                    │  (New Version)   │
                    └──────────────────┘
```

---

## 1. 핵심 기술 스택

### 언어: Rust

**선택 이유:**
- ⚡ C++ 수준의 성능
- 🛡️ 메모리 안전 (Segfault 없음)
- 🧬 진정한 병렬 처리 (no GIL)
- 📦 단일 바이너리 (배포 용이)
- 🚀 Tokio: async/await 네트워킹

```rust
// Cargo.toml
[dependencies]
tokio = { version = "1", features = ["full"] }
rayon = "1.8"           // 데이터 병렬처리
actix-web = "4.5"       // 웹 서버
tokio-tungstenite = "0.21"  // WebSocket
serde = { version = "1.0", features = ["derive"] }
sqlx = { version = "0.7", features = ["runtime-tokio", "sqlite"] }
tracing = "0.1"         // 로깅
config = "0.14"         // 설정 관리
```

### 아키텍처 패턴: Actor Model

```rust
// 메시지 기반 병렬 처리
use tokio::sync::mpsc;

pub struct TradingActor {
    id: String,
    strategy: Box<dyn Strategy>,
    portfolio: Portfolio,
    risk_manager: RiskManager,
}

impl TradingActor {
    async fn handle_message(&mut self, msg: TradingMessage) {
        match msg {
            TradingMessage::PriceUpdate(candle) => {
                // 병렬로 여러 전략 실행
                let signal = self.strategy.on_candle(&candle).await;
                if let Some(sig) = signal {
                    self.execute_signal(sig).await;
                }
            }
            TradingMessage::OrderFilled(order) => {
                self.portfolio.update_position(order).await;
            }
            // ...
        }
    }
}
```

---

## 2. Zero-Downtime 핫 스왑 메커니즘

### 방법: Unix Domain Socket Handoff

```rust
// master.rs - 마스터 프로세스
pub struct Master {
    socket_path: PathBuf,
    current_worker: Option<Child>,
    state_db: Arc<Mutex<SqlitePool>>,
}

impl Master {
    pub async fn deploy_new_version(&mut self, binary_path: &str) -> Result<()> {
        // 1. 새 워커 시작 (기존 소켓 상속)
        let listener = TcpListener::bind("0.0.0.0:0").await?;
        let fd = listener.as_raw_fd();
        
        let new_worker = Command::new(binary_path)
            .env("SOCKET_FD", fd.to_string())
            .env("MODE", "worker")
            .env("STATE_PATH", self.state_db.path())
            .spawn()?;
        
        // 2. 새 워커가 준비될 때까지 대기
        tokio::time::timeout(Duration::from_secs(10), async {
            self.wait_for_worker_ready().await
        }).await?;
        
        // 3. 기존 워커에 graceful shutdown 신호
        if let Some(mut old_worker) = self.current_worker.take() {
            old_worker.kill().await?;  // 또는 SIGTERM
        }
        
        // 4. 소켓 핸드오프 (TCP: SO_REUSEPORT)
        self.current_worker = Some(new_worker);
        
        info!("Hot swap completed!");
        Ok(())
    }
}
```

### 방법 2: 소켓 활성화 (systemd-style)

```rust
// systemd socket activation 패턴
use std::os::unix::io::FromRawFd;

pub fn get_listener() -> TcpListener {
    // systemd에서 전달받은 소켓 FD
    if let Ok(fd) = env::var("LISTEN_FDS") {
        let fd: i32 = fd.parse().unwrap();
        unsafe { TcpListener::from_raw_fd(fd) }
    } else {
        // 일반 모드
        TcpListener::bind("0.0.0.0:8080").unwrap()
    }
}
```

---

## 3. CPU/GPU 병렬 최적화

### CPU: Rayon (Data Parallelism)

```rust
use rayon::prelude::*;

// 여러 전략 동시 실행
pub async fn run_strategies_parallel(
    strategies: Vec<Box<dyn Strategy>>,
    candle: &Candle
) -> Vec<Option<Signal>> {
    // Rayon 스레드풀에서 병렬 실행
    let signals: Vec<_> = strategies
        .par_iter()  // 병렬 이터레이터
        .map(|strategy| {
            strategy.on_candle(candle)
        })
        .collect();
    
    signals
}

// 대량 백테스팅
pub fn backtest_parallel(
    strategy: &dyn Strategy,
    candles: &[Candle],
) -> Vec<Trade> {
    candles
        .par_chunks(1000)  // 1000개씩 청크로 병렬 처리
        .flat_map(|chunk| {
            chunk.iter().filter_map(|candle| {
                strategy.on_candle(candle).map(|signal| {
                    Trade::new(signal, candle.timestamp)
                })
            })
        })
        .collect()
}
```

### GPU: Vulkan Compute Shader

```rust
// GPU 가속 백테스팅
use vulkano::pipeline::ComputePipeline;

pub struct GpuBacktester {
    device: Arc<Device>,
    queue: Arc<Queue>,
    pipeline: Arc<ComputePipeline>,
}

impl GpuBacktester {
    pub fn run_batch(&self, params: &[StrategyParams]) -> Vec<BacktestResult> {
        // GPU에서 수천 개의 파라미터 조합 동시 계산
        let data_buffer = CpuAccessibleBuffer::from_iter(
            self.device.clone(),
            BufferUsage::all(),
            false,
            params.iter().cloned(),
        ).unwrap();
        
        let command_buffer = AutoCommandBufferBuilder::primary(
            self.device.clone(),
            self.queue.family(),
        )
        .unwrap()
        .dispatch([params.len() as u32, 1, 1], self.pipeline.clone(),
            descriptor_set, // 데이터 바인딩
            (), // 푸시 상수
        )
        .unwrap()
        .build()
        .unwrap();
        
        // 결과 반환 (GPU → CPU)
        // 수천 배 빠른 백테스팅
    }
}
```

---

## 4. 사용자 로컬 리소스 활용

### 클라이언트-서버 하이브리드

```
사용자 PC (로컬)
├── Trading Core (Rust)
│   ├── GPU: 백테스팅/최적화
│   ├── CPU: 전략 실행
│   └── 메모리: 실시간 데이터
│
└── Web Dashboard (임베디드)
    └── http://localhost:8080

클라우드 (선택)
├── Market Data Feed (가벼운)
└── Telemetry (익명화된 통계)
```

**장점:**
- ✅ 사용자 GPU/CPU 활용 (무료)
- ✅ 0원 서버 비용 (개인용)
- ✅ 지연 시간 최소화 (로컬 실행)
- ✅ 데이터 프라이버시 (API 키 로컬)

---

## 5. Tokio 기반 이벤트 루프

```rust
// main.rs
#[tokio::main]
async fn main() -> Result<()> {
    // 멀티스레드 Tokio 런타임
    // 기본적으로 CPU 코어 수만큼 스레드
    
    let (tx, mut rx) = mpsc::channel(1000);
    
    // 1. 시장 데이터 스트림
    let market_task = tokio::spawn(async move {
        let mut ws = connect_exchange().await?;
        
        while let Some(msg) = ws.next().await {
            let candle = parse_candle(msg?)?;
            tx.send(TradingMessage::PriceUpdate(candle)).await?;
        }
        
        Ok::<_, Error>(())
    });
    
    // 2. 트레이딩 로직
    let trading_task = tokio::spawn(async move {
        let actor = TradingActor::new().await?;
        
        while let Some(msg) = rx.recv().await {
            actor.handle_message(msg).await;
        }
        
        Ok::<_, Error>(())
    });
    
    // 3. 웹 대시보드
    let web_task = tokio::spawn(async {
        HttpServer::new(|| {
            App::new()
                .route("/", web::get().to(dashboard))
                .route("/ws", web::get().to(ws_handler))
        })
        .bind("0.0.0.0:8080")?
        .run()
        .await
    });
    
    // 4. 상태 저장
    let persist_task = tokio::spawn(async {
        let mut interval = tokio::time::interval(Duration::from_secs(60));
        
        loop {
            interval.tick().await;
            save_state_to_sqlite().await?;
        }
    });
    
    // 모든 태스크 동시 실행
    tokio::try_join!(
        market_task,
        trading_task,
        web_task,
        persist_task
    )?;
    
    Ok(())
}
```

---

## 6. 부드러운 실시간 UX

### WebSocket + React/Vue

```rust
// websocket.rs
use actix_web::{web, Error, HttpRequest, HttpResponse};
use actix_web_actors::ws;

pub struct DashboardWs {
    user_id: String,
    last_update: Instant,
}

impl Actor for DashboardWs {
    type Context = ws::WebsocketContext<Self>;
    
    fn started(&mut self, ctx: &mut Self::Context) {
        // 100ms마다 데이터 푸시
        ctx.run_interval(Duration::from_millis(100), |act, ctx| {
            let data = get_portfolio_snapshot(&act.user_id);
            ctx.text(json!(data).to_string());
        });
    }
}

// 프론트엔드 (React)
const Dashboard = () => {
  const [data, setData] = useState(null);
  
  useEffect(() => {
    const ws = new WebSocket('ws://localhost:8080/ws');
    
    ws.onmessage = (event) => {
      const newData = JSON.parse(event.data);
      
      // React Query로 캐싱 및 스무딩
      setData(prev => ({
        ...newData,
        // 부드러운 전환 애니메이션
        portfolio: interpolate(prev?.portfolio, newData.portfolio)
      }));
    };
    
    return () => ws.close();
  }, []);
  
  return <DashboardUI data={data} />;
};
```

### 성능 최적화

```rust
// 1. Lock-free 데이터 구조
use crossbeam::queue::ArrayQueue;

pub struct PriceCache {
    // 락프리 원형 버퍼
    buffer: ArrayQueue<Candle>,
}

// 2. 메모리 풀
pub struct ObjectPool<T> {
    pool: Vec<T>,
}

// 3. SIMD 연산 (AVX2)
#[target_feature(enable = "avx2")]
pub unsafe fn calculate_indicators_simd(data: &[f64]) -> Vec<f64> {
    // AVX2로 RSI/MACD 병렬 계산
}
```

---

## 7. 구현 로드맵

### Phase 1: Core Migration (4주)
- [ ] Rust 프로젝트 설정 (Cargo workspace)
- [ ] Tokio 기반 이벤트 루프
- [ ] CCXT Pro equivalent (Rust)
- [ ] SQLite 통합 (sqlx)
- [ ] 기본 전략 포트 (RSI, MA)

### Phase 2: Zero-Downtime (2주)
- [ ] Master-Worker 프로세스 분리
- [ ] 소켓 핸드오프 구현
- [ ] 상태 복구 메커니즘
- [ ] 핫 스왑 스크립트

### Phase 3: GPU Acceleration (2주)
- [ ] Vulkan/CUDA 통합
- [ ] GPU 백테스팅
- [ ] 파라미터 최적화

### Phase 4: Web Dashboard (2주)
- [ ] Actix-web 임베딩
- [ ] React 프론트엔드
- [ ] WebSocket 실시간 업데이트

### Phase 5: Optimization (2주)
- [ ] SIMD 최적화
- [ ] 메모리 프로파일링
- [ ] 지연 시간 측정

**총: 12주 (3개월)**

---

## 8. 현재 Python에서 점진적 마이그레이션

### 하이브리드 접근

```
Month 1-2: Python 유지 + Rust 확장 모듈
  - 백테스팅만 Rust로
  - pyo3로 Python 바인딩

Month 3-4: 핵심 로직 Rust로
  - 전략 실행
  - 주문 관리

Month 5-6: 완전 Rust 전환
  - Python 제거
  - 단일 바이너리
```

### PyO3 예시

```rust
// Rust 라이브러리
use pyo3::prelude::*;

#[pyfunction]
fn calculate_rsi(prices: Vec<f64>, period: usize) -> PyResult<Vec<f64>> {
    // Rust로 빠른 계산
    let rsi = ta::indicators::rsi(&prices, period);
    Ok(rsi)
}

#[pymodule]
fn trading_core(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calculate_rsi, m)?)?;
    Ok(())
}
```

```python
# Python에서 사용
import trading_core

# Rust로 컴파일된 고성능 함수
rsi = trading_core.calculate_rsi(prices, 14)  # 100x faster
```

---

## 9. 비용 비교

| 항목 | Python (현재) | Rust (목표) | 개선 |
|------|---------------|-------------|------|
| **CPU 사용량** | 100% (1코어) | 800% (8코어) | 8x |
| **메모리** | 500MB | 100MB | 5x |
| **지연 시간** | 10ms | 0.1ms | 100x |
| **배포 중단** | 30초 | 0초 | 핫 스왑 |
| **서버 비용** | $100/월 | $0 (로컬) | 무료 |

---

## 결론

**핵심 메시지:**
1. **Rust**로 전환하여 성능 10x 향상
2. **Master-Worker** 아키텍처로 핫 스왑 구현
3. **사용자 로컬 리소스** 활용 (0원 서버비)
4. **GPU 가속**으로 백테스팅 수천 배 빠르게

**다음 단계:**
1. Rust 프로젝트 시작?
2. Python에 Rust 모듈 통합 (단기적)?
3. 기존 Python 최적화만 (단기적)?

**권장:** Python + Rust 하이브리드로 시작 → 점진적 전환
