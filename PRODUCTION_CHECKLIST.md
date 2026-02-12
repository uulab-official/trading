# Production Deployment Checklist

이 체크리스트는 트레이딩 봇을 실전(Live Trading)으로 전환하기 전에 반드시 확인해야 할 사항들입니다.

---

## ✅ Phase 1: Pre-Deployment (필수)

### 1.1 환경 설정
- [ ] **Paper Trading 테스트 완료** (최소 2주)
  - [ ] 전략이 예상대로 작동하는지 확인
  - [ ] 수익률 추이 확인
  - [ ] 리스크 관리 기능 작동 확인
  
- [ ] **설정 파일 검증**
  ```bash
  python -m trading_core.config_cli validate
  ```
  - [ ] 모든 필수 필드가 채워져 있음
  - [ ] API 키가 올바르게 설정됨
  - [ ] Telegram 설정이 작동함 (선택)

- [ ] **API 키 보안**
  - [ ] `.gitignore`에 `config/secrets/`가 포함됨
  - [ ] API 키에 출금 권한이 없음 (거래만 가능)
  - [ ] IP 화이트리스트 설정됨 (가능한 경우)

### 1.2 시스템 요구사항
- [ ] **하드웨어 사양**
  - [ ] CPU: 최소 2코어 권장
  - [ ] RAM: 최소 2GB 권장
  - [ ] 디스크: 최소 10GB 여유 공간
  - [ ] 인터넷: 안정적인 연결 (99.9% 가동시간)

- [ ] **소프트웨어**
  - [ ] Docker 설치 및 실행 중
  - [ ] Docker Compose 설치
  - [ ] Python 3.11+ (직접 실행 시)

---

## ✅ Phase 2: Configuration (설정)

### 2.1 봇 설정 (`config/active/bot_config.yml`)
```yaml
bot:
  mode: paper  # 반드시 paper로 시작
  # ...
```
- [ ] **mode: paper**로 설정 (처음엔 반드시!)
- [ ] 적절한 타임존 설정 (`Asia/Seoul`)
- [ ] 봇 ID와 이름 설정

### 2.2 거래소 설정
```yaml
exchange:
  id: binance
  sandbox: true  # 테스트넷 사용
  testnet: true
```
- [ ] **sandbox: true** (처음엔 반드시!)
- [ ] 테스트넷 API 키 사용

### 2.3 리스크 관리 설정
```yaml
risk:
  max_daily_loss_pct: 2.0
  max_positions: 2
  risk_per_trade_pct: 0.5
  max_leverage: 1.0
```
- [ ] **보수적인 값**으로 시작 (max_daily_loss_pct: 2%)
- [ ] **레버리지 없음** (max_leverage: 1.0)
- [ ] 리스크 한도가 자본금에 적합함

### 2.4 전략 설정
- [ ] 전략이 논리적으로 검증됨
- [ ] 백테스팅 완료
- [ ] 타임프레임이 적절함 (1h, 4h 권장)

---

## ✅ Phase 3: Testing (테스트)

### 3.1 설정 초기화 테스트
```bash
python -m trading_core.config_cli init
python -m trading_core.config_cli validate
```
- [ ] 설정 파일이 올바르게 생성됨
- [ ] 검증이 오류 없이 통과됨

### 3.2 Telegram 테스트 (선택)
```bash
python -m trading_core.config_cli test-telegram
```
- [ ] 테스트 메시지가 도착함
- [ ] 봇 토큰이 유효함

### 3.3 Docker 빌드 테스트
```bash
docker build -f infra/docker/Dockerfile -t trading-bot:test .
docker run --rm trading-bot:test python -m trading_core.config_cli --help
```
- [ ] 이미지가 성공적으로 빌드됨
- [ ] CLI가 컨테이너 내에서 작동함

### 3.4 Paper Trading 실행 테스트
```bash
# Paper trading으로 24시간 실행
docker-compose up -d
```
- [ ] 봇이 오류 없이 시작됨
- [ ] 로그에 심각한 에러 없음
- [ ] WebSocket 연결이 안정적임
- [ ] 상태가 SQLite에 저장됨

---

## ✅ Phase 4: Monitoring Setup (모니터링)

### 4.1 로그 모니터링
```bash
# 실시간 로그 확인
docker-compose logs -f trading-bot-blue

# 로그 파일 확인
tail -f logs/trading-bot.log
```
- [ ] 로그가 정상적으로 기록됨
- [ ] 로그 로테이션 설정됨

### 4.2 알림 설정
- [ ] Telegram 알림 작동 확인
- [ ] 오류 알림 수신 테스트
- [ ] 거래 알림 수신 테스트

### 4.3 상태 모니터링
```bash
# 봇 상태 확인
docker-compose ps

# 리소스 사용량 확인
docker stats
```
- [ ] CPU 사용률이 정상 범위 (< 50%)
- [ ] 메모리 사용량이 정상 범위 (< 70%)
- [ ] 디스크 공간이 충분함 (> 20%)

---

## ✅ Phase 5: Live Trading 전환 (실전)

### ⚠️ 중요: 순차적으로 진행하세요!

### 5.1 소액으로 시작
- [ ] **최소 자본금으로 시작** (예: $100)
- [ ] **하나의 전략만 활성화**
- [ ] **낮은 리스크 설정 유지**

### 5.2 설정 변경
```yaml
bot:
  mode: live  # paper에서 live로 변경

exchange:
  sandbox: false  # sandbox 비활성화
  testnet: false
  api_key: "LIVE_API_KEY"  # 실제 API 키
  api_secret: "LIVE_SECRET"
```
- [ ] `mode: live`로 변경
- [ ] `sandbox: false`로 변경
- [ ] 실제 API 키로 교체
- [ ] 설정 검증 다시 실행

### 5.3 검증
```bash
python -m trading_core.config_cli validate
```
- [ ] "Not production ready" 경고가 사라짐
- [ ] 모든 설정이 유효함

### 5.4 배포
```bash
# Zero-downtime deployment
./infra/scripts/deploy.sh
```
- [ ] 배포가 성공적으로 완료됨
- [ ] Health check 통과
- [ ] 상태가 복원됨

---

## ✅ Phase 6: Post-Deployment (배포 후)

### 6.1 초기 모니터링 (첫 24시간)
- [ ] 1시간마다 로그 확인
- [ ] 텔레그램 알림 수신 확인
- [ ] 첫 거래가 정상적으로 실행됨
- [ ] 포지션이 올바르게 표시됨

### 6.2 주간 검토
- [ ] 수익률 분석
- [ ] 리스크 한도 준수 확인
- [ ] 전략 성능 평가
- [ ] 필요시 설정 조정

### 6.3 백업 확인
- [ ] 자동 백업이 생성됨
- [ ] 백업 파일이 접근 가능함
- [ ] 롤백 절차를 테스트했음

---

## 🚨 Emergency Procedures (비상 절차)

### 봇 중지가 필요한 경우
```bash
# 정상 중지 (권장)
docker-compose stop -t 60 trading-bot-blue

# 강제 중지 (비상시)
docker-compose kill trading-bot-blue
```

### 롤백이 필요한 경우
```bash
# 롤백 실행
./infra/scripts/rollback.sh

# 또는 특정 백업으로 롤백
./infra/scripts/rollback.sh backup_20260115_120000.db
```

### 문제 해결
```bash
# 로그 확인
docker-compose logs --tail=100 trading-bot-blue

# 상태 확인
docker-compose exec trading-bot-blue python -m trading_core.config_cli show

# 설정 검증
docker-compose exec trading-bot-blue python -m trading_core.config_cli validate
```

---

## 📊 Success Criteria (성공 기준)

Live Trading을 성공적으로 시작했는지 확인:

- [ ] 봇이 24시간 이상 중단 없이 실행됨
- [ ] 첫 거래가 정상적으로 체결됨
- [ ] 리스크 한도가 작동함 (손실 제한)
- [ ] 알림이 정상적으로 수신됨
- [ ] 수익률이 예상 범위 내에 있음

---

## ⚠️ 경고

**절대 하지 마세요:**
1. ❌ 테스트 없이 바로 live 모드로 전환
2. ❌ 모든 자본금을 한 번에 투입
3. ❌ 리스크 한도 없이 실행
4. ❌ 모니터링 없이 무인 운영
5. ❌ 백업 없이 시스템 변경

**반드시 하세요:**
1. ✅ Paper trading에서 충분한 테스트
2. ✅ 소액으로 시작해서 점진적 확대
3. ✅ 항상 백업 유지
4. ✅ 정기적인 모니터링
5. ✅ 손실 한도 설정

---

## 📞 지원

문제 발생 시:
1. 로그 확인: `docker-compose logs`
2. 설정 검증: `python -m trading_core.config_cli validate`
3. 문서 참고: `docs/` 디렉토리
4. 백업에서 복원: `./infra/scripts/rollback.sh`

---

**체크리스트 완료일:** _______________

**책임자 서명:** _______________

**검토자 서명:** _______________
