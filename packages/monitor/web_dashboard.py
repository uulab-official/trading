# Web Dashboard Service
# FastAPI + WebSocket으로 실시간 트레이딩 모니터링

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 전역 상태
connected_clients: List[WebSocket] = []
app_state = {
    "trading_status": "stopped",
    "portfolio": {},
    "positions": [],
    "recent_trades": [],
    "logs": []
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 관리"""
    # 시작
    asyncio.create_task(data_sync_loop())
    logger.info("Web Dashboard started")
    yield
    # 종료
    logger.info("Web Dashboard stopped")

app = FastAPI(title="Trading Bot Dashboard", lifespan=lifespan)

# 정적 파일 서빙 (HTML, JS, CSS)
app.mount("/static", StaticFiles(directory="/app/web/static"), name="static")

# 데이터베이스 경로
DB_PATH = "/app/data/trading.db"

def get_db_connection():
    """SQLite 연결"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

async def data_sync_loop():
    """1초마다 데이터 동기화"""
    while True:
        try:
            sync_data()
            await broadcast_to_clients()
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Sync error: {e}")
            await asyncio.sleep(5)

def sync_data():
    """SQLite에서 데이터 읽기"""
    
    try:
        if not Path(DB_PATH).exists():
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 포트폴리오 상태
        cursor.execute("""
            SELECT data FROM state WHERE key = 'bot:main:portfolio'
        """)
        row = cursor.fetchone()
        if row:
            app_state["portfolio"] = json.loads(row[0])
        
        # 포지션 목록
        cursor.execute("""
            SELECT data FROM state WHERE key = 'bot:main:positions'
        """)
        row = cursor.fetchone()
        if row:
            app_state["positions"] = json.loads(row[0])
        
        # 봇 상태
        cursor.execute("""
            SELECT data FROM state WHERE key = 'bot:main:status'
        """)
        row = cursor.fetchone()
        if row:
            status_data = json.loads(row[0])
            app_state["trading_status"] = status_data.get("status", "unknown")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Database read error: {e}")

async def broadcast_to_clients():
    """모든 클라이언트에 데이터 브로드캐스트"""
    if not connected_clients:
        return
    
    message = {
        "type": "update",
        "timestamp": datetime.utcnow().isoformat(),
        "data": app_state
    }
    
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_json(message)
        except:
            disconnected.append(client)
    
    # 연결 끊긴 클라이언트 제거
    for client in disconnected:
        connected_clients.remove(client)

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """메인 대시보드 페이지"""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Trading Bot Dashboard</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e27;
            color: #fff;
            min-height: 100vh;
        }
        .header {
            background: #161b33;
            padding: 20px;
            border-bottom: 1px solid #2d3561;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 24px; color: #00d4aa; }
        .status {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #ff4757;
        }
        .status-dot.running { background: #00d4aa; animation: pulse 2s infinite; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .container {
            padding: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: #161b33;
            border-radius: 12px;
            padding: 20px;
            border: 1px solid #2d3561;
        }
        .card h3 {
            color: #8b92b4;
            font-size: 14px;
            text-transform: uppercase;
            margin-bottom: 10px;
        }
        .card .value {
            font-size: 32px;
            font-weight: bold;
            color: #fff;
        }
        .card .value.positive { color: #00d4aa; }
        .card .value.negative { color: #ff4757; }
        .positions-table {
            width: 100%;
            border-collapse: collapse;
        }
        .positions-table th,
        .positions-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #2d3561;
        }
        .positions-table th {
            color: #8b92b4;
            font-weight: 500;
        }
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
        }
        .badge-long { background: rgba(0, 212, 170, 0.2); color: #00d4aa; }
        .badge-short { background: rgba(255, 71, 87, 0.2); color: #ff4757; }
        .connection-status {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 10px 20px;
            border-radius: 8px;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .connection-status.connected { background: rgba(0, 212, 170, 0.2); color: #00d4aa; }
        .connection-status.disconnected { background: rgba(255, 71, 87, 0.2); color: #ff4757; }
        .logs {
            background: #0d1117;
            border-radius: 8px;
            padding: 15px;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 12px;
            max-height: 300px;
            overflow-y: auto;
        }
        .log-entry { margin-bottom: 4px; }
        .log-entry.error { color: #ff4757; }
        .log-entry.warn { color: #ffa502; }
        .log-entry.info { color: #74b9ff; }
        #lastUpdate {
            color: #8b92b4;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🤖 Trading Bot Dashboard</h1>
        <div class="status">
            <span>Status:</span>
            <div id="statusDot" class="status-dot"></div>
            <span id="statusText">Connecting...</span>
            <span id="lastUpdate"></span>
        </div>
    </div>
    
    <div class="container">
        <div class="grid">
            <div class="card">
                <h3>Total Balance</h3>
                <div id="totalBalance" class="value">$0.00</div>
            </div>
            <div class="card">
                <h3>Total PnL</h3>
                <div id="totalPnl" class="value">$0.00</div>
            </div>
            <div class="card">
                <h3>Open Positions</h3>
                <div id="openPositions" class="value">0</div>
            </div>
            <div class="card">
                <h3>Daily Trades</h3>
                <div id="dailyTrades" class="value">0</div>
            </div>
        </div>
        
        <div class="card">
            <h3>Open Positions</h3>
            <table class="positions-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Entry Price</th>
                        <th>Current Price</th>
                        <th>PnL</th>
                    </tr>
                </thead>
                <tbody id="positionsBody">
                    <tr>
                        <td colspan="6" style="text-align: center; color: #8b92b4;">
                            No open positions
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </div>
    
    <div id="connectionStatus" class="connection-status disconnected">
        <span>●</span>
        <span>Disconnected</span>
    </div>

    <script>
        const ws = new WebSocket(`ws://${window.location.host}/ws`);
        
        ws.onopen = function() {
            console.log('Connected to dashboard');
            updateConnectionStatus(true);
        };
        
        ws.onmessage = function(event) {
            const message = JSON.parse(event.data);
            if (message.type === 'update') {
                updateDashboard(message.data);
            }
        };
        
        ws.onclose = function() {
            console.log('Disconnected from dashboard');
            updateConnectionStatus(false);
        };
        
        ws.onerror = function(error) {
            console.error('WebSocket error:', error);
            updateConnectionStatus(false);
        };
        
        function updateConnectionStatus(connected) {
            const status = document.getElementById('connectionStatus');
            if (connected) {
                status.className = 'connection-status connected';
                status.innerHTML = '<span>●</span><span>Connected</span>';
            } else {
                status.className = 'connection-status disconnected';
                status.innerHTML = '<span>●</span><span>Disconnected</span>';
            }
        }
        
        function updateDashboard(data) {
            // 상태 업데이트
            const statusDot = document.getElementById('statusDot');
            const statusText = document.getElementById('statusText');
            
            if (data.trading_status === 'running') {
                statusDot.className = 'status-dot running';
                statusText.textContent = 'Running';
            } else {
                statusDot.className = 'status-dot';
                statusText.textContent = 'Stopped';
            }
            
            // 포트폴리오 업데이트
            const portfolio = data.portfolio || {};
            const balance = portfolio.balance || 0;
            const totalPnl = portfolio.total_pnl || 0;
            
            document.getElementById('totalBalance').textContent = 
                '$' + parseFloat(balance).toFixed(2);
            
            const pnlElement = document.getElementById('totalPnl');
            pnlElement.textContent = (totalPnl >= 0 ? '+' : '') + '$' + parseFloat(totalPnl).toFixed(2);
            pnlElement.className = 'value ' + (totalPnl >= 0 ? 'positive' : 'negative');
            
            // 포지션 업데이트
            const positions = data.positions || [];
            document.getElementById('openPositions').textContent = positions.length;
            
            const tbody = document.getElementById('positionsBody');
            if (positions.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="6" style="text-align: center; color: #8b92b4;">
                            No open positions
                        </td>
                    </tr>
                `;
            } else {
                tbody.innerHTML = positions.map(pos => `
                    <tr>
                        <td>${pos.symbol}</td>
                        <td><span class="badge badge-${pos.side}">${pos.side.toUpperCase()}</span></td>
                        <td>${pos.size}</td>
                        <td>$${parseFloat(pos.entry_price).toFixed(2)}</td>
                        <td>$${parseFloat(pos.current_price || pos.entry_price).toFixed(2)}</td>
                        <td style="color: ${(pos.unrealized_pnl || 0) >= 0 ? '#00d4aa' : '#ff4757'}">
                            ${(pos.unrealized_pnl || 0) >= 0 ? '+' : ''}$${parseFloat(pos.unrealized_pnl || 0).toFixed(2)}
                        </td>
                    </tr>
                `).join('');
            }
            
            // 마지막 업데이트 시간
            document.getElementById('lastUpdate').textContent = 
                'Updated: ' + new Date().toLocaleTimeString();
        }
    </script>
</body>
</html>
    """

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 연결 - 실시간 데이터"""
    await websocket.accept()
    connected_clients.append(websocket)
    
    # 초기 데이터 전송
    await websocket.send_json({
        "type": "update",
        "timestamp": datetime.utcnow().isoformat(),
        "data": app_state
    })
    
    try:
        while True:
            # 클라이언트 메시지 대기 (ping/pong 등)
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get("action") == "ping":
                await websocket.send_json({"type": "pong"})
                
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        logger.info("Client disconnected")

@app.get("/api/status")
async def get_status():
    """REST API: 현재 상태"""
    return {
        "status": app_state["trading_status"],
        "timestamp": datetime.utcnow().isoformat(),
        "portfolio": app_state["portfolio"],
        "positions": app_state["positions"]
    }

@app.get("/api/history")
async def get_history(limit: int = 100):
    """REST API: 거래 내역"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT data FROM state 
            WHERE key LIKE 'trade:%'
            ORDER BY key DESC
            LIMIT ?
        """, (limit,))
        
        trades = [json.loads(row[0]) for row in cursor.fetchall()]
        conn.close()
        
        return {"trades": trades}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
