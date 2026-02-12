use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer};
use actix_ws::Message;
use futures_util::StreamExt;
use tokio::sync::broadcast;
use tracing::info;

use crate::core::models::{Order, Position};

pub struct WebDashboard {
    port: u16,
    event_tx: broadcast::Sender<DashboardEvent>,
}

#[derive(Clone, Debug)]
pub enum DashboardEvent {
    PriceUpdate { symbol: String, price: f64 },
    OrderUpdate(Order),
    PositionUpdate(Position),
    TradeSignal { symbol: String, signal: String },
    SystemStatus { status: String, message: String },
}

pub struct DashboardState {
    event_tx: broadcast::Sender<DashboardEvent>,
}

impl WebDashboard {
    pub fn new(port: u16) -> Self {
        let (event_tx, _) = broadcast::channel(1000);
        Self { port, event_tx }
    }

    pub fn get_event_sender(&self) -> broadcast::Sender<DashboardEvent> {
        self.event_tx.clone()
    }

    pub async fn run(self) -> std::io::Result<()> {
        info!("Starting web dashboard on port {}", self.port);

        let state = web::Data::new(DashboardState {
            event_tx: self.event_tx.clone(),
        });

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
        .content_type("text/html")
        .body(INDEX_HTML)
}

async fn health_check() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "status": "healthy",
        "service": "trading-server"
    }))
}

async fn get_status() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "status": "running",
        "mode": "master",
        "timestamp": chrono::Utc::now().to_rfc3339()
    }))
}

async fn get_positions() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "positions": [],
        "timestamp": chrono::Utc::now().to_rfc3339()
    }))
}

async fn get_orders() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "orders": [],
        "timestamp": chrono::Utc::now().to_rfc3339()
    }))
}

async fn websocket_handler(
    req: HttpRequest,
    stream: web::Payload,
    state: web::Data<DashboardState>,
) -> Result<HttpResponse, actix_web::Error> {
    let (response, mut session, mut msg_stream) = actix_ws::handle(&req, stream)?;

    let mut event_rx = state.event_tx.subscribe();

    actix_web::rt::spawn(async move {
        loop {
            tokio::select! {
                Ok(event) = event_rx.recv() => {
                    let msg = match event {
                        DashboardEvent::PriceUpdate { symbol, price } => {
                            serde_json::json!({
                                "type": "price",
                                "symbol": symbol,
                                "price": price,
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }).to_string()
                        }
                        DashboardEvent::TradeSignal { symbol, signal } => {
                            serde_json::json!({
                                "type": "signal",
                                "symbol": symbol,
                                "signal": signal,
                                "timestamp": chrono::Utc::now().to_rfc3339()
                            }).to_string()
                        }
                        _ => continue,
                    };

                    if session.text(msg).await.is_err() {
                        break;
                    }
                }
                Some(Ok(msg)) = msg_stream.next() => {
                    match msg {
                        Message::Ping(bytes) => {
                            if session.pong(&bytes).await.is_err() {
                                break;
                            }
                        }
                        Message::Close(_) => break,
                        _ => {}
                    }
                }
                else => break,
            }
        }
    });

    Ok(response)
}

const INDEX_HTML: &str = r#"
<!DOCTYPE html>
<html>
<head>
    <title>Trading Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e27;
            color: #fff;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid #1a1f3a;
        }
        .header h1 { font-size: 28px; color: #00d4aa; }
        .status { display: flex; gap: 20px; }
        .status-item {
            background: #1a1f3a;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 14px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .card {
            background: #1a1f3a;
            border-radius: 12px;
            padding: 20px;
        }
        .card h2 {
            font-size: 16px;
            color: #8b92b4;
            margin-bottom: 15px;
            text-transform: uppercase;
        }
        .price {
            font-size: 32px;
            font-weight: bold;
            color: #00d4aa;
        }
        .price-change {
            font-size: 14px;
            margin-top: 5px;
        }
        .positive { color: #00d4aa; }
        .negative { color: #ff4757; }
        .signal {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px;
            background: #0a0e27;
            border-radius: 8px;
            margin-top: 10px;
        }
        .signal-buy { color: #00d4aa; }
        .signal-sell { color: #ff4757; }
        .connection-status {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .connected { background: #00d4aa; color: #0a0e27; }
        .disconnected { background: #ff4757; color: #fff; }
    </style>
</head>
<body>
    <div class="connection-status disconnected" id="conn-status">Disconnected</div>

    <div class="header">
        <h1>Trading Dashboard</h1>
        <div class="status">
            <div class="status-item">Mode: Live</div>
            <div class="status-item" id="timestamp">--:--:--</div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>BTC/USDT Price</h2>
            <div class="price" id="btc-price">$0.00</div>
            <div class="price-change" id="btc-change">0.00%</div>
        </div>

        <div class="card">
            <h2>Active Positions</h2>
            <div id="positions">No active positions</div>
        </div>

        <div class="card">
            <h2>Recent Signals</h2>
            <div id="signals">Waiting for signals...</div>
        </div>

        <div class="card">
            <h2>System Status</h2>
            <div id="system-status">Running</div>
        </div>
    </div>

    <script>
        const ws = new WebSocket('ws://' + window.location.host + '/ws');
        const connStatus = document.getElementById('conn-status');
        const signalsDiv = document.getElementById('signals');

        ws.onopen = () => {
            connStatus.textContent = 'Connected';
            connStatus.className = 'connection-status connected';
        };

        ws.onclose = () => {
            connStatus.textContent = 'Disconnected';
            connStatus.className = 'connection-status disconnected';
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);

            if (data.type === 'price') {
                document.getElementById('btc-price').textContent = '$' + data.price.toFixed(2);
            } else if (data.type === 'signal') {
                const signalDiv = document.createElement('div');
                signalDiv.className = 'signal signal-' + data.signal.toLowerCase();
                signalDiv.innerHTML = `
                    <span>${data.symbol}</span>
                    <span>${data.signal}</span>
                    <span>${new Date(data.timestamp).toLocaleTimeString()}</span>
                `;
                signalsDiv.prepend(signalDiv);
            }
        };

        function updateTimestamp() {
            document.getElementById('timestamp').textContent = new Date().toLocaleTimeString();
        }
        setInterval(updateTimestamp, 1000);
        updateTimestamp();
    </script>
</body>
</html>
"#;