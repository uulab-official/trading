# Monitoring and Alerting Setup

24/7 트레이딩 봇 모니터링 및 알림 설정 가이드

---

## 📊 모니터링 개요

### 핵심 모니터링 영역
1. **System Health** (시스템 건강)
2. **Trading Performance** (거래 성과)
3. **Risk Metrics** (리스크 지표)
4. **External Dependencies** (외부 의존성)

---

## 🔧 기본 모니터링 (Docker)

### Docker 내장 모니터링

```bash
# 컨테이너 상태 확인
docker-compose ps

# 실시간 리소스 모니터링
docker stats

# 로그 모니터링
docker-compose logs -f trading-bot-blue

# 마지막 100줄 로그 확인
docker-compose logs --tail=100 trading-bot-blue
```

### Health Check 설정

Docker Compose에 이미 설정됨:
```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

상태 확인:
```bash
# Health status 확인
docker inspect --format='{{.State.Health.Status}}' crypto-trading-bot-blue

# Health check 로그
docker inspect --format='{{json .State.Health}}' crypto-trading-bot-blue | jq
```

---

## 📈 고급 모니터링 (Prometheus + Grafana)

### 1. 모니터링 스택 추가

`docker-compose.monitoring.yml`:
```yaml
version: '3.8'

services:
  # Prometheus
  prometheus:
    image: prom/prometheus:latest
    container_name: trading-prometheus
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'
    networks:
      - trading-network

  # Grafana
  grafana:
    image: grafana/grafana:latest
    container_name: trading-grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana-data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources
    networks:
      - trading-network
    depends_on:
      - prometheus

  # Node Exporter (시스템 메트릭)
  node-exporter:
    image: prom/node-exporter:latest
    container_name: trading-node-exporter
    restart: unless-stopped
    volumes:
      - /proc:/host/proc:ro
      - /sys:/host/sys:ro
      - /:/rootfs:ro
    command:
      - '--path.procfs=/host/proc'
      - '--path.rootfs=/rootfs'
      - '--path.sysfs=/host/sys'
      - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
    networks:
      - trading-network

  # cAdvisor (컨테이너 메트릭)
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    container_name: trading-cadvisor
    restart: unless-stopped
    privileged: true
    devices:
      - /dev/kmsg:/dev/kmsg
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker:/var/lib/docker:ro
      - /cgroup:/cgroup:ro
    networks:
      - trading-network

volumes:
  prometheus-data:
  grafana-data:

networks:
  trading-network:
    external: true
```

### 2. Prometheus 설정

`monitoring/prometheus.yml`:
```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'node-exporter'
    static_configs:
      - targets: ['node-exporter:9100']

  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: 'trading-bot'
    static_configs:
      - targets: ['trading-bot-blue:8080']
    metrics_path: /metrics
    scrape_interval: 10s
```

### 3. 알림 규칙 설정

`monitoring/rules/trading_alerts.yml`:
```yaml
groups:
  - name: trading_bot_alerts
    rules:
      - alert: TradingBotDown
        expr: up{job="trading-bot"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Trading Bot is down"
          description: "Trading bot has been down for more than 1 minute"

      - alert: HighCPUUsage
        expr: 100 - (avg by (instance) (irate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 80
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High CPU usage detected"
          description: "CPU usage is above 80% for more than 5 minutes"

      - alert: HighMemoryUsage
        expr: (node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes) / node_memory_MemTotal_bytes * 100 > 85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High memory usage detected"
          description: "Memory usage is above 85% for more than 5 minutes"

      - alert: DailyLossLimit
        expr: trading_bot_daily_pnl_percent < -3
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Daily loss limit reached"
          description: "Daily loss has exceeded 3%"

      - alert: NoTradesFor1Hour
        expr: rate(trading_bot_trades_total[1h]) == 0
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: "No trades in the last hour"
          description: "Trading bot hasn't executed any trades in the past hour"

      - alert: WebSocketDisconnected
        expr: trading_bot_websocket_connected == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "WebSocket disconnected"
          description: "Trading bot has lost connection to exchange"
```

### 4. 모니터링 스택 시작

```bash
# 모니터링 스택 시작
docker-compose -f docker-compose.monitoring.yml up -d

# 상태 확인
docker-compose -f docker-compose.monitoring.yml ps

# 접속 정보
# Prometheus: http://localhost:9090
# Grafana: http://localhost:3000 (admin/admin)
```

---

## 🚨 알림 설정 (AlertManager)

### Slack 알림

`monitoring/alertmanager.yml`:
```yaml
global:
  slack_api_url: 'YOUR_SLACK_WEBHOOK_URL'

route:
  receiver: 'slack-notifications'
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h

receivers:
  - name: 'slack-notifications'
    slack_configs:
      - channel: '#trading-alerts'
        title: '{{ range .Alerts }}{{ .Annotations.summary }}{{ end }}'
        text: '{{ range .Alerts }}{{ .Annotations.description }}{{ end }}'
        send_resolved: true

inhibit_rules:
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'warning'
    equal: ['alertname', 'instance']
```

### Telegram 알림 (AlertManager 연동)

```yaml
receivers:
  - name: 'telegram-alerts'
    webhook_configs:
      - url: 'http://trading-bot:8080/webhook/alert'
        send_resolved: true
```

---

## 📊 커스텀 메트릭 (코드 내)

### Python 메트릭 노출

```python
# trading_bot/metrics.py
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# 메트릭 정의
trades_total = Counter('trading_bot_trades_total', 'Total number of trades', ['symbol', 'side'])
positions_open = Gauge('trading_bot_positions_open', 'Number of open positions')
daily_pnl = Gauge('trading_bot_daily_pnl', 'Daily PnL in USD')
daily_pnl_percent = Gauge('trading_bot_daily_pnl_percent', 'Daily PnL percentage')
websocket_connected = Gauge('trading_bot_websocket_connected', 'WebSocket connection status')
order_latency = Histogram('trading_bot_order_latency_seconds', 'Order execution latency')

# 메트릭 서버 시작
def start_metrics_server(port=8080):
    start_http_server(port)
    logger.info(f"Metrics server started on port {port}")

# 사용 예시
class TradingBot:
    async def _open_long_position(self, signal):
        with order_latency.time():
            order = await self.order_manager.place_order(...)
        
        trades_total.labels(symbol=signal.symbol, side='buy').inc()
        positions_open.inc()
        
    async def _close_position(self, symbol, price, reason):
        trade = self.portfolio.close_position(symbol, price)
        positions_open.dec()
        
        daily_pnl.set(float(trade.pnl))
        daily_pnl_percent.set(float(trade.pnl_pct))
```

---

## 📱 텔레그램 모니터링 봇

### 명령어 추가

```python
# trading_bot/telegram_commands.py
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 상태 확인"""
    bot = context.application.bot_data.get('trading_bot')
    
    if not bot or not bot.portfolio:
        await update.message.reply_text("❌ Bot not initialized")
        return
    
    status_text = f"""
🤖 <b>Trading Bot Status</b>

<b>Mode:</b> {bot.config.mode}
<b>Running:</b> {'✅ Yes' if bot.running else '❌ No'}
<b>Uptime:</b> {get_uptime()}

<b>Portfolio:</b>
• Total Value: ${bot.portfolio.get_total_value():.2f}
• Balance: ${bot.portfolio.balance:.2f}

<b>Positions:</b>
• Open: {len(bot.portfolio.positions)}
• Total PnL: ${bot.portfolio.total_pnl:.2f}

<b>Risk:</b>
• Daily PnL: {bot.risk_manager.daily_pnl:.2f}%
• Trading Allowed: {'✅ Yes' if bot.risk_manager.is_trading_allowed() else '❌ No'}
"""
    await update.message.reply_text(status_text, parse_mode='HTML')

async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """포지션 목록"""
    bot = context.application.bot_data.get('trading_bot')
    
    if not bot or not bot.portfolio:
        await update.message.reply_text("❌ Bot not initialized")
        return
    
    if not bot.portfolio.positions:
        await update.message.reply_text("📭 No open positions")
        return
    
    text = "📊 <b>Open Positions</b>\n\n"
    for symbol, position in bot.portfolio.positions.items():
        pnl = position.unrealized_pnl
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        text += f"{pnl_emoji} <b>{symbol}</b>\n"
        text += f"  Size: {position.size}\n"
        text += f"  Entry: ${position.entry_price:.2f}\n"
        text += f"  Current: ${position.current_price:.2f}\n"
        text += f"  PnL: ${pnl:.2f}\n\n"
    
    await update.message.reply_text(text, parse_mode='HTML')

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """봇 중지 (관리자 전용)"""
    user_id = update.effective_user.id
    
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("❌ Unauthorized")
        return
    
    await update.message.reply_text("🛑 Stopping bot...")
    
    bot = context.application.bot_data.get('trading_bot')
    if bot:
        await bot.stop()
```

---

## 🔍 로그 모니터링 (Loki)

### Loki + Grafana 설정

```yaml
# docker-compose.logging.yml
services:
  loki:
    image: grafana/loki:latest
    container_name: trading-loki
    restart: unless-stopped
    ports:
      - "3100:3100"
    volumes:
      - ./monitoring/loki-config.yml:/etc/loki/local-config.yaml
      - loki-data:/loki
    networks:
      - trading-network

  promtail:
    image: grafana/promtail:latest
    container_name: trading-promtail
    restart: unless-stopped
    volumes:
      - ./monitoring/promtail-config.yml:/etc/promtail/config.yml
      - /var/log:/var/log:ro
      - ./logs:/app/logs:ro
    networks:
      - trading-network

volumes:
  loki-data:
```

### Promtail 설정

```yaml
# monitoring/promtail-config.yml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: trading-bot-logs
    static_configs:
      - targets:
          - localhost
        labels:
          job: trading-bot
          __path__: /app/logs/*.log
```

---

## 📋 모니터링 체크리스트

### 기본 모니터링
- [ ] Docker health check 설정됨
- [ ] 로그 모니터링 가능
- [ ] 리소스 사용량 확인 가능

### 고급 모니터링
- [ ] Prometheus 설치 및 설정
- [ ] Grafana 대시보드 구성
- [ ] 알림 규칙 설정
- [ ] AlertManager 설정

### 알림 채널
- [ ] Telegram 알림 작동
- [ ] Slack 알림 설정 (선택)
- [ ] 이메일 알림 설정 (선택)

### 대시보드
- [ ] 시스템 리소스 대시보드
- [ ] 거래 성과 대시보드
- [ ] 리스크 모니터링 대시보드

---

## 🚀 빠른 시작

```bash
# 1. 기본 모니터링 (Docker)
docker-compose logs -f

# 2. 고급 모니터링 (Prometheus + Grafana)
docker-compose -f docker-compose.monitoring.yml up -d

# 3. Grafana 접속
open http://localhost:3000
# admin / admin

# 4. 대시보드 가져오기
# Grafana UI → Dashboards → Import
# ID: 1860 (Node Exporter Full)
# ID: 14282 (Docker and System Monitoring)
```

---

## 📚 참고 자료

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [Docker Logging Best Practices](https://docs.docker.com/config/containers/logging/)
