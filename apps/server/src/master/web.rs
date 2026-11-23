use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer};
use actix_ws::Message;
use anyhow::{anyhow, ensure, Result};
use chrono::{DateTime, Utc};
use futures_util::StreamExt;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, RwLock};
use tracing::info;

use super::WorkerMessage;
use crate::core::models::normalize_market_symbol;

const FRESHNESS_LIMIT: Duration = Duration::from_secs(90);

#[derive(Clone)]
pub struct WebDashboard {
    port: u16,
    event_tx: broadcast::Sender<DashboardEvent>,
    state: Arc<RwLock<ObservationState>>,
}

#[derive(Clone, Debug)]
pub struct DashboardEvent {
    revision: u64,
    body: Value,
}

#[derive(Clone)]
struct ReceivedAt {
    tick: Instant,
    utc: DateTime<Utc>,
}

impl ReceivedAt {
    fn now() -> Self {
        Self {
            tick: Instant::now(),
            utc: Utc::now(),
        }
    }

    fn age(&self, now: Instant) -> Option<Duration> {
        now.checked_duration_since(self.tick)
    }
}

struct PriceRecord {
    price: f64,
    received: ReceivedAt,
}

struct ObservationState {
    instance_id: String,
    generation: u64,
    revision: u64,
    connected: bool,
    connection_state: &'static str,
    status: String,
    message: String,
    halted: bool,
    heartbeat: Option<ReceivedAt>,
    market: Option<ReceivedAt>,
    prices: BTreeMap<String, PriceRecord>,
    signals: VecDeque<Value>,
}

impl Default for ObservationState {
    fn default() -> Self {
        Self {
            instance_id: uuid::Uuid::new_v4().to_string(),
            generation: 0,
            revision: 0,
            connected: false,
            connection_state: "not_connected",
            status: "initializing".into(),
            message: "Waiting for a worker and public market observations".into(),
            halted: false,
            heartbeat: None,
            market: None,
            prices: BTreeMap::new(),
            signals: VecDeque::new(),
        }
    }
}

impl ObservationState {
    fn next_revision(&self) -> Result<u64> {
        self.revision
            .checked_add(1)
            .ok_or_else(|| anyhow!("Dashboard revision exhausted"))
    }

    fn snapshot(&self, now: Instant) -> Value {
        let heartbeat_age = self.heartbeat.as_ref().and_then(|value| value.age(now));
        let market_age = self.market.as_ref().and_then(|value| value.age(now));
        let reason = if !self.connected {
            "worker_disconnected"
        } else if self.halted {
            "observation_halted"
        } else if self.status != "observation_only" {
            "worker_initializing"
        } else if heartbeat_age.is_none() {
            "waiting_for_heartbeat"
        } else if heartbeat_age.is_some_and(|age| age > FRESHNESS_LIMIT) {
            "heartbeat_stale"
        } else if market_age.is_none() {
            "waiting_for_market_observation"
        } else if market_age.is_some_and(|age| age > FRESHNESS_LIMIT) {
            "market_observation_stale"
        } else {
            "observations_receiving"
        };
        let healthy = reason == "observations_receiving";
        let prices = self
            .prices
            .iter()
            .map(|(symbol, record)| {
                let age = record.received.age(now);
                (
                    symbol.clone(),
                    json!({
                        "symbol":symbol,
                        "price":record.price,
                        "received_at":record.received.utc,
                        "age_seconds":age.map(|value| value.as_secs()),
                        "stale":!healthy || age.map_or(true, |value| value > FRESHNESS_LIMIT)
                    }),
                )
            })
            .collect::<serde_json::Map<_, _>>();
        json!({
            "instance_id":self.instance_id,
            "service":"trading-server",
            "mode":"observation_only",
            "execution_enabled":false,
            "status":self.status,
            "message":self.message,
            "healthy":healthy,
            "readiness_reason":reason,
            "worker_connected":self.connected,
            "connection_state":self.connection_state,
            "connection_generation":self.generation,
            "revision":self.revision,
            "last_heartbeat_received_at":self.heartbeat.as_ref().map(|value| value.utc),
            "last_market_received_at":self.market.as_ref().map(|value| value.utc),
            "heartbeat_age_seconds":heartbeat_age.map(|value| value.as_secs()),
            "market_age_seconds":market_age.map(|value| value.as_secs()),
            "freshness_threshold_seconds":FRESHNESS_LIMIT.as_secs(),
            "freshness_basis":"latest_valid_price_and_heartbeat_received_by_master",
            "exchange_event_freshness_verified":false,
            "account_available":false,
            "orders_available":false,
            "prices":prices,
            "signals":self.signals,
            "timestamp":Utc::now()
        })
    }
}

impl WebDashboard {
    pub fn new(port: u16) -> Self {
        let (event_tx, _) = broadcast::channel(1000);
        Self {
            port,
            event_tx,
            state: Arc::new(RwLock::new(ObservationState::default())),
        }
    }

    fn publish(&self, state: &ObservationState, mut event: Value) {
        event["state"] = state.snapshot(Instant::now());
        event["timestamp"] = json!(Utc::now());
        // Stored state is authoritative even while there are no browser receivers.
        let _ = self.event_tx.send(DashboardEvent {
            revision: state.revision,
            body: event,
        });
    }

    pub async fn begin_worker_connection(&self) -> Result<u64> {
        let mut state = self.state.write().await;
        let generation = state
            .generation
            .checked_add(1)
            .ok_or_else(|| anyhow!("Worker generation exhausted"))?;
        let revision = state.next_revision()?;
        *state = ObservationState {
            instance_id: state.instance_id.clone(),
            generation,
            revision,
            connected: true,
            connection_state: "connected",
            ..ObservationState::default()
        };
        self.publish(&state, json!({"type":"status"}));
        Ok(generation)
    }

    pub async fn record_worker_message(
        &self,
        generation: u64,
        message: WorkerMessage,
    ) -> Result<()> {
        let mut state = self.state.write().await;
        if generation != state.generation || !state.connected {
            return Ok(());
        }
        let revision = state.next_revision()?;
        let event = match message {
            WorkerMessage::SystemStatus { status, message } => {
                ensure!(
                    matches!(
                        status.as_str(),
                        "initializing"
                            | "observation_only"
                            | "feed_unavailable"
                            | "feed_halted"
                            | "stopped"
                    ),
                    "Unsupported worker observation status"
                );
                ensure!(
                    message.len() <= 512 && !message.chars().any(|value| value.is_control()),
                    "Invalid worker status message"
                );
                // Terminal states are latched until a newly accepted connection.
                if state.halted {
                    return Ok(());
                }
                state.halted = matches!(
                    status.as_str(),
                    "feed_unavailable" | "feed_halted" | "stopped"
                );
                state.status = status.clone();
                state.message = message.clone();
                json!({"type":"status", "status":status, "message":message})
            }
            WorkerMessage::Heartbeat => {
                state.heartbeat = Some(ReceivedAt::now());
                json!({"type":"status"})
            }
            WorkerMessage::PriceUpdate { symbol, price } => {
                let symbol = normalize_market_symbol(&symbol)
                    .map_err(|_| anyhow!("Invalid worker price observation"))?;
                ensure!(
                    price.is_finite() && price > 0.0,
                    "Invalid worker price observation"
                );
                if state.halted {
                    return Ok(());
                }
                ensure!(
                    state.prices.contains_key(&symbol) || state.prices.len() < 256,
                    "Worker price observation capacity exceeded"
                );
                let received = ReceivedAt::now();
                state.market = Some(received.clone());
                state
                    .prices
                    .insert(symbol.clone(), PriceRecord { price, received });
                json!({"type":"price", "symbol":symbol, "price":price})
            }
            WorkerMessage::TradeSignal { symbol, signal } => {
                let symbol = normalize_market_symbol(&symbol)
                    .map_err(|_| anyhow!("Invalid worker signal observation"))?;
                ensure!(
                    !signal.is_empty()
                        && signal.len() <= 512
                        && !signal.chars().any(|value| value.is_control()),
                    "Invalid worker signal observation"
                );
                if state.halted {
                    return Ok(());
                }
                state.signals.push_front(json!({"symbol":symbol, "signal":signal, "received_at":Utc::now(), "execution_enabled":false}));
                state.signals.truncate(20);
                json!({"type":"signal", "symbol":symbol, "signal":signal})
            }
            WorkerMessage::OrderUpdate(_) | WorkerMessage::PositionUpdate(_) => {
                return Err(anyhow!(
                    "Account updates are unavailable on the public observation path"
                ));
            }
        };
        state.revision = revision;
        self.publish(&state, event);
        Ok(())
    }

    pub async fn end_worker_connection(&self, generation: u64, failed: bool) {
        let mut state = self.state.write().await;
        if generation != state.generation || !state.connected {
            return;
        }
        state.connected = false;
        state.connection_state = if failed { "error" } else { "disconnected" };
        if !state.halted {
            state.status = if failed {
                "worker_error"
            } else {
                "worker_disconnected"
            }
            .into();
            state.message = "Worker connection ended; public observations unavailable".into();
        }
        state.halted = true;
        if let Ok(revision) = state.next_revision() {
            state.revision = revision;
        }
        self.publish(&state, json!({"type":"status"}));
    }

    pub async fn snapshot(&self) -> Value {
        self.state.read().await.snapshot(Instant::now())
    }

    async fn subscribe_snapshot(&self) -> (broadcast::Receiver<DashboardEvent>, Value) {
        // Reading the snapshot and subscribing share the state lock, so a newer
        // update cannot be missed or overwritten by an older queued event.
        let state = self.state.read().await;
        let receiver = self.event_tx.subscribe();
        let snapshot = state.snapshot(Instant::now());
        (receiver, snapshot)
    }

    pub async fn run(&self) -> std::io::Result<()> {
        info!("Starting observation dashboard on port {}", self.port);
        let state = web::Data::new(self.clone());
        HttpServer::new(move || {
            App::new()
                .app_data(state.clone())
                .route("/", web::get().to(index))
                .route("/health", web::get().to(health_check))
                .route("/api/status", web::get().to(get_status))
                .route("/api/positions", web::get().to(get_positions))
                .route("/api/orders", web::get().to(get_orders))
                .route("/ws", web::get().to(websocket_handler))
        })
        .bind(format!("0.0.0.0:{}", self.port))?
        .run()
        .await
    }
}

async fn index() -> HttpResponse {
    HttpResponse::Ok()
        .content_type("text/html; charset=utf-8")
        .body(INDEX_HTML)
}

async fn health_check(state: web::Data<WebDashboard>) -> HttpResponse {
    let snapshot = state.snapshot().await;
    let mut response = if snapshot["healthy"] == true {
        HttpResponse::Ok()
    } else {
        HttpResponse::ServiceUnavailable()
    };
    response
        .insert_header(("Cache-Control", "no-store"))
        .json(snapshot)
}

async fn get_status(state: web::Data<WebDashboard>) -> HttpResponse {
    HttpResponse::Ok()
        .insert_header(("Cache-Control", "no-store"))
        .json(state.snapshot().await)
}

async fn get_positions() -> HttpResponse {
    HttpResponse::Ok().json(json!({"positions":[], "available":false, "mode":"observation_only", "execution_enabled":false, "reason":"Account adapter is not implemented", "timestamp":Utc::now()}))
}

async fn get_orders() -> HttpResponse {
    HttpResponse::Ok().json(json!({"orders":[], "available":false, "mode":"observation_only", "execution_enabled":false, "reason":"Order adapter is not implemented", "timestamp":Utc::now()}))
}

async fn websocket_handler(
    req: HttpRequest,
    stream: web::Payload,
    state: web::Data<WebDashboard>,
) -> Result<HttpResponse, actix_web::Error> {
    let (response, mut session, mut messages) = actix_ws::handle(&req, stream)?;
    let (mut receiver, snapshot) = state.subscribe_snapshot().await;
    actix_web::rt::spawn(async move {
        let mut revision = snapshot["revision"].as_u64().unwrap_or(0);
        if session
            .text(json!({"type":"snapshot", "state":snapshot}).to_string())
            .await
            .is_err()
        {
            return;
        }
        let mut timer = tokio::time::interval(Duration::from_secs(5));
        timer.tick().await;
        loop {
            tokio::select! {
                event = receiver.recv() => match event {
                    Ok(event) => {
                        if event.revision <= revision { continue; }
                        revision = event.revision;
                        if session.text(event.body.to_string()).await.is_err() { break; }
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => {
                        let (next_receiver, snapshot) = state.subscribe_snapshot().await;
                        receiver = next_receiver;
                        revision = snapshot["revision"].as_u64().unwrap_or(revision);
                        if session.text(json!({"type":"snapshot", "state":snapshot}).to_string()).await.is_err() { break; }
                    }
                    Err(broadcast::error::RecvError::Closed) => break,
                },
                _ = timer.tick() => {
                    let snapshot = state.snapshot().await;
                    revision = snapshot["revision"].as_u64().unwrap_or(revision);
                    if session.text(json!({"type":"status", "state":snapshot}).to_string()).await.is_err() { break; }
                }
                message = messages.next() => match message {
                    Some(Ok(Message::Ping(bytes))) => { if session.pong(&bytes).await.is_err() { break; } }
                    Some(Ok(Message::Close(_))) | Some(Err(_)) | None => break,
                    _ => {},
                },
            }
        }
        let _ = session.close(None).await;
    });
    Ok(response)
}

const INDEX_HTML: &str = r#"<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>시세 관측 대시보드</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0c1220;color:#edf2fa;font-family:system-ui,-apple-system,sans-serif;padding:28px;line-height:1.55}main{max-width:1060px;margin:auto}header{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:24px}h1{font-size:24px;margin:0}h2{font-size:15px;margin:0 0 14px;color:#bdc8d9}p{margin:6px 0}.muted{color:#a5b1c6;font-size:13px}.badge{display:inline-block;border:1px solid #465166;border-radius:20px;padding:5px 12px;font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}.card{background:#172135;border:1px solid #26334a;border-radius:14px;padding:20px}.good{color:#71e1c2}.unavailable{color:#ffba91}.quote,.signal{padding:12px 0;border-bottom:1px solid #2a3650}.quote strong{display:block;font-size:25px}.signal{font-size:13px}footer{margin-top:18px}.message{font-size:17px;margin-bottom:8px}@media(max-width:600px){body{padding:16px}header{align-items:flex-start;flex-direction:column}}
</style></head><body><main>
<header><div><h1>시세 관측 대시보드</h1><p class="muted">공개 시세와 전략 신호를 확인합니다.</p></div><span class="badge">관측 전용 · 주문 실행 비활성</span></header>
<div class="grid"><section class="card"><h2>관측 상태</h2><p id="system-status" class="message unavailable">관측 상태 확인 중</p><p id="state-message" class="muted">워커와 시세 수신을 기다리고 있습니다.</p><p id="freshness" class="muted"></p><p id="connection" class="muted">화면 연결 확인 중</p></section>
<section class="card"><h2>수신한 시세</h2><div id="prices" class="muted">아직 수신한 시세가 없습니다.</div></section>
<section class="card"><h2>계좌 · 주문 조회</h2><p>조회 기능 미구현</p><p class="muted">계좌 잔고, 보유 자산, 주문 내역은 제공되지 않습니다.</p></section>
<section class="card"><h2>관측한 전략 신호</h2><div id="signals" class="muted">아직 수신한 신호가 없습니다.</div></section></div>
<footer class="muted">상태 기준은 마스터가 수신한 시세와 하트비트입니다. 거래소 이벤트의 현재성이나 실제 계좌 상태를 검증한 결과는 아닙니다.</footer>
<script>
let latestRevision = -1, latestInstance = null, latestAge = -1, latestHealthy = false, pollBusy = false;
const labels = {worker_disconnected:'워커 연결 없음',observation_halted:'시세 관측 중단',worker_initializing:'워커 초기화 중',waiting_for_heartbeat:'하트비트 대기',heartbeat_stale:'하트비트 수신 지연',waiting_for_market_observation:'시세 수신 대기',market_observation_stale:'시세 수신 지연',observations_receiving:'공개 시세 수신 중'};
function displaySymbol(symbol){const raw=String(symbol).toUpperCase().replace('/','');return raw.endsWith('USDT')?raw.slice(0,-4)+'/USDT':raw;}
function renderState(state, authoritative=false){
 if(!state||state.mode!=='observation_only'||state.execution_enabled!==false||typeof state.instance_id!=='string')return;
 // Serialized HTTP polls select the active master. A delayed WebSocket frame
 // from a different process must never choose or retire a master instance.
 if(state.instance_id!==latestInstance){if(!authoritative)return;latestInstance=state.instance_id;latestRevision=-1;latestAge=-1;latestHealthy=false;}
 const revision=Number(state.revision), age=Math.max(Number(state.market_age_seconds??-1),Number(state.heartbeat_age_seconds??-1));
 if(revision<latestRevision||(revision===latestRevision&&(age<latestAge||(!latestHealthy&&state.healthy))))return;
 latestRevision=revision;latestAge=age;latestHealthy=state.healthy===true;
 const status=document.getElementById('system-status');status.textContent=labels[state.readiness_reason]||'관측 상태 확인 필요';status.className='message '+(state.healthy?'good':'unavailable');
 document.getElementById('state-message').textContent=state.message||'';
 document.getElementById('freshness').textContent='최근 시세: '+(state.market_age_seconds===null?'없음':state.market_age_seconds+'초 전')+' · 하트비트: '+(state.heartbeat_age_seconds===null?'없음':state.heartbeat_age_seconds+'초 전');
 const prices=document.getElementById('prices');prices.replaceChildren();
 Object.values(state.prices||{}).forEach(quote=>{if(!Number.isFinite(quote.price))return;const row=document.createElement('div');row.className='quote';const symbol=document.createElement('span');symbol.textContent=displaySymbol(quote.symbol);const value=document.createElement('strong');value.textContent=quote.price.toLocaleString(undefined,{maximumFractionDigits:8});const age=document.createElement('span');age.className='muted';age.textContent=(quote.stale?'마지막 관측값 · ':'')+quote.age_seconds+'초 전';row.append(symbol,value,age);prices.append(row);});
 if(!prices.childNodes.length)prices.textContent='아직 수신한 시세가 없습니다.';
 const signals=document.getElementById('signals');signals.replaceChildren();
 (state.signals||[]).forEach(signal=>{const row=document.createElement('div');row.className='signal';row.textContent=displaySymbol(signal.symbol)+' · '+signal.signal+' · '+new Date(signal.received_at).toLocaleTimeString();signals.append(row);});
 if(!signals.childNodes.length)signals.textContent='아직 수신한 신호가 없습니다.';
}
function unavailable(){const status=document.getElementById('system-status');status.textContent='대시보드 상태 수신 불가';status.className='message unavailable';document.getElementById('connection').textContent='화면 연결을 다시 확인하고 있습니다.';}
async function refreshStatus(){if(pollBusy)return;pollBusy=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),4000);try{const response=await fetch('/api/status',{cache:'no-store',signal:controller.signal});if(!response.ok)throw new Error('status unavailable');renderState(await response.json(),true);document.getElementById('connection').textContent='화면 상태 갱신: '+new Date().toLocaleTimeString();}catch(error){unavailable();}finally{clearTimeout(timer);pollBusy=false;}}
const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');
socket.onmessage=event=>{try{renderState(JSON.parse(event.data).state);}catch(error){unavailable();}};
socket.onclose=()=>{unavailable();refreshStatus();};socket.onerror=unavailable;
refreshStatus();setInterval(refreshStatus,5000);
</script></main></body></html>"#;

#[cfg(test)]
#[path = "web_tests.rs"]
mod tests;
