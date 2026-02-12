# Appliance Architecture for Trading Bot

## Executive Summary

상업용 어플라이언스 모델로 배포하기 위한 완전한 아키텍처 설계안

---

## 1. 추천 배포 모델: Docker Compose + Management UI

### 왜 이 모델인가?

| 모델 | Pros | Cons | 적합성 |
|------|------|------|--------|
| **Docker Compose + UI** | ⭐ 쉬운 설치, 널리 사용됨, 업데이트 용이 | Docker 필요 | **최적** |
| VM Appliance (OVA) | 완전한 격리 | 큰 용량, 느림, 업데이트 어려움 | 부적합 |
| Kubernetes | 확장성 | 복잡함, 과잉 | 부적합 |
| Snap/Flatpak | 쉬운 설치 | 제한적, 네트워크 문제 | 부적합 |
| Binary Installer | 단순함 | 의존성 지옥 | 부적합 |

**선택 이유:**
- 현대적인 어플라이언스 표준 (Home Assistant, Pi-hole 등 참고)
- 개발/유지보수 용이
- 고객이 익숙한 Docker 생태계
- Zero-downtime 업데이트 가능

---

## 2. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                    Customer Infrastructure                   │
│  (NAS, VPS, Raspberry Pi, Home Server, Cloud VM)            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │         Trading Bot Appliance Stack                  │    │
│  │                                                      │    │
│  │  ┌─────────────┐    ┌─────────────┐                 │    │
│  │  │  Manager    │    │   Trading   │                 │    │
│  │  │    UI       │◄──►│    Bot      │                 │    │
│  │  │  (Web)      │    │  (Python)   │                 │    │
│  │  └──────┬──────┘    └──────┬──────┘                 │    │
│  │         │                  │                         │    │
│  │         └────────┬─────────┘                         │    │
│  │                  │                                    │    │
│  │  ┌───────────────┴───────────────┐                  │    │
│  │  │        SQLite Database        │                  │    │
│  │  │    (State, Config, License)   │                  │    │
│  │  └───────────────────────────────┘                  │    │
│  │                                                      │    │
│  │  ┌─────────────┐    ┌─────────────┐                 │    │
│  │  │   OTA       │    │  License    │                 │    │
│  │  │  Updater    │    │  Daemon     │                 │    │
│  │  └─────────────┘    └─────────────┘                 │    │
│  │                                                      │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Network:                                                    │
│  ├── External: Exchange APIs (Binance, etc.)                │
│  ├── Optional: Telemetry (customer opt-in)                  │
│  └── Optional: Remote Access (VPN/SSH)                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 디렉토리 구조

```
/opt/trading-bot/                    # 설치 경로 (Linux)
~/TradingBot/                        # 설치 경로 (macOS/Windows)
├── docker-compose.yml               # 메인 오케스트레이션
├── .env                             # 환경 변수
├── docker-compose.override.yml      # 사용자 커스터마이징
│
├── config/                          # 설정 (백업 대상)
│   ├── appliance.yml               # 어플라이언스 설정
│   ├── trading/                    # 트레이딩 설정
│   └── ssl/                        # SSL 인증서
│
├── data/                            # 데이터 (백업 대상)
│   ├── trading.db                  # SQLite DB
│   ├── state/                      # 상태 파일
│   └── backups/                    # 자동 백업
│
├── logs/                            # 로그
│   ├── trading/
│   ├── manager/
│   └── updater/
│
├── manager/                         # 관리 UI
│   ├── static/
│   └── config/
│
├── updater/                         # OTA 업데이터
│   └── scripts/
│
├── scripts/                         # 유틸리티
│   ├── install.sh                  # 설치 스크립트
│   ├── backup.sh                   # 백업 스크립트
│   ├── restore.sh                  # 복구 스크립트
│   └── update.sh                   # 업데이트 스크립트
│
└── LICENSE.key                      # 라이선스 파일
```

---

## 4. 핵심 컴포넌트

### 4.1 Appliance Manager (관리 UI)

```yaml
# Docker Compose에 추가
services:
  manager:
    image: trading-bot/manager:latest
    container_name: trading-manager
    restart: unless-stopped
    ports:
      - "8080:8080"  # Web UI
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./config:/app/config:ro
      - ./data:/app/data:ro
      - ./logs:/app/logs:ro
    environment:
      - LICENSE_KEY_FILE=/app/config/LICENSE.key
      - APPLIANCE_VERSION=${VERSION}
```

**기능:**
- Web 기반 설정 관리
- 실시간 상태 모니터링
- 라이선스 관리
- 백업/복구 인터페이스
- 업데이트 관리
- 로그 뷰어

### 4.2 License Daemon

```python
# packages/core/trading_core/license_manager.py
import hashlib
import json
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

class LicenseManager:
    """오프라인 가능한 라이선스 관리"""
    
    def __init__(self, license_file: str):
        self.license_file = license_file
        self.license_data = None
        self._load_license()
    
    def _load_license(self):
        """라이선스 파일 로드 및 검증"""
        try:
            with open(self.license_file, 'r') as f:
                license_content = f.read()
            
            self.license_data = self._verify_license(license_content)
        except Exception as e:
            logger.error(f"Failed to load license: {e}")
            self.license_data = None
    
    def _verify_license(self, content: str) -> dict:
        """라이선스 서명 검증"""
        # JWT-like 구조: header.payload.signature
        parts = content.split('.')
        if len(parts) != 3:
            raise ValueError("Invalid license format")
        
        header, payload, signature = parts
        
        # 공개 키로 서명 검증
        public_key = self._get_public_key()
        
        try:
            public_key.verify(
                base64.b64decode(signature),
                f"{header}.{payload}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
        except:
            raise ValueError("Invalid license signature")
        
        payload_data = json.loads(base64.b64decode(payload))
        
        # 만료일 검증
        if datetime.fromisoformat(payload_data['exp']) < datetime.utcnow():
            raise ValueError("License expired")
        
        # 하드웨어 ID 검증 (선택)
        if 'hwid' in payload_data:
            current_hwid = self._get_hardware_id()
            if payload_data['hwid'] != current_hwid:
                raise ValueError("License not valid for this hardware")
        
        return payload_data
    
    def is_valid(self) -> bool:
        """라이선스 유효성 확인"""
        if not self.license_data:
            return False
        
        # 만료일 재확인
        try:
            exp = datetime.fromisoformat(self.license_data['exp'])
            return exp > datetime.utcnow()
        except:
            return False
    
    def get_features(self) -> list:
        """라이선스에 포함된 기능 목록"""
        if not self.is_valid():
            return []
        return self.license_data.get('features', [])
    
    def is_feature_enabled(self, feature: str) -> bool:
        """특정 기능 활성화 여부"""
        return feature in self.get_features()
    
    def _get_hardware_id(self) -> str:
        """하드웨어 ID 생성"""
        # CPU 시리얼 + MAC 주소 기반
        import subprocess
        
        try:
            # Linux
            cpu_info = subprocess.check_output(['cat', '/proc/cpuinfo']).decode()
            serial = [line for line in cpu_info.split('\n') if 'Serial' in line][0]
            serial = serial.split(':')[1].strip()
        except:
            serial = "UNKNOWN"
        
        try:
            mac = subprocess.check_output(['cat', '/sys/class/net/eth0/address']).decode().strip()
        except:
            mac = "00:00:00:00:00:00"
        
        return hashlib.sha256(f"{serial}{mac}".encode()).hexdigest()[:16]
```

### 4.3 OTA Updater

```yaml
# updater 서비스
services:
  updater:
    image: trading-bot/updater:latest
    container_name: trading-updater
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - .:/app/appliance:rw
      - ./data/updater:/app/data:rw
    environment:
      - UPDATE_CHANNEL=stable  # stable, beta, nightly
      - AUTO_UPDATE=false      # 사용자가 활성화
      - LICENSE_KEY_FILE=/app/appliance/LICENSE.key
```

**업데이트 프로세스:**
1. 버전 확인 (GitHub Releases API)
2. 라이선스 검증 (업데이트 권한)
3. 업데이트 다운로드
4. 백업 생성
5. Zero-downtime 배포
6. Health check
7. 실패 시 롤백

```python
# updater/update_engine.py
class UpdateEngine:
    """OTA 업데이트 엔진"""
    
    async def check_for_updates(self) -> Optional[UpdateInfo]:
        """새 버전 확인"""
        current_version = self.get_current_version()
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'https://api.trading-bot.io/v1/releases/latest',
                headers={'X-License-Key': self.license_key}
            ) as resp:
                if resp.status == 200:
                    latest = await resp.json()
                    if semver.compare(latest['version'], current_version) > 0:
                        return UpdateInfo(**latest)
        return None
    
    async def perform_update(self, version: str) -> UpdateResult:
        """업데이트 실행"""
        try:
            # 1. 사전 체크
            if not self.license_manager.is_feature_enabled('updates'):
                return UpdateResult.error("License does not allow updates")
            
            # 2. 백업
            backup_path = await self.create_backup()
            
            # 3. 새 버전 다운로드
            await self.download_version(version)
            
            # 4. Zero-downtime 배포
            await self.zero_downtime_deploy()
            
            # 5. 검증
            if not await self.verify_deployment():
                await self.rollback(backup_path)
                return UpdateResult.error("Deployment verification failed")
            
            return UpdateResult.success(version)
            
        except Exception as e:
            logger.exception("Update failed")
            await self.rollback(backup_path)
            return UpdateResult.error(str(e))
    
    async def zero_downtime_deploy(self):
        """무중단 배포"""
        # Blue-Green 배포 사용
        await self.deploy_blue_green()
```

### 4.4 Telemetry (선택적)

```python
# 선택적 원격 모니터링 (고객 동의 필요)
class TelemetryClient:
    """개인정보 보호를 고려한 원격 모니터링"""
    
    def __init__(self, enabled: bool, license_key: str):
        self.enabled = enabled
        self.license_key = license_key
    
    async def send_telemetry(self, metrics: dict):
        """텔레메트리 전송"""
        if not self.enabled:
            return
        
        # 민감한 정보 제거
        safe_metrics = {
            'version': metrics.get('version'),
            'uptime': metrics.get('uptime'),
            'license_tier': metrics.get('license_tier'),
            'error_count': metrics.get('error_count'),
            # NO: API keys, balances, strategies
        }
        
        async with aiohttp.ClientSession() as session:
            await session.post(
                'https://telemetry.trading-bot.io/v1/metrics',
                json=safe_metrics,
                headers={'X-License-Key': self.license_key}
            )
```

---

## 5. 설치 프로세스

### 5.1 원클릭 설치 스크립트

```bash
#!/bin/bash
# install.sh - 원클릭 설치

set -e

APPLIANCE_VERSION="${1:-latest}"
INSTALL_DIR="${2:-/opt/trading-bot}"

log() { echo "[Trading Bot] $1"; }

# 1. 시스템 체크
check_system() {
    log "Checking system requirements..."
    
    # OS 체크
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    else
        log "Error: Unsupported OS"
        exit 1
    fi
    
    # Docker 체크
    if ! command -v docker &> /dev/null; then
        log "Docker not found. Installing..."
        install_docker
    fi
    
    # Docker Compose 체크
    if ! command -v docker-compose &> /dev/null; then
        log "Docker Compose not found. Installing..."
        install_docker_compose
    fi
    
    log "System check passed"
}

# 2. 디렉토리 생성
setup_directories() {
    log "Creating directories..."
    
    sudo mkdir -p "$INSTALL_DIR"
    sudo mkdir -p "$INSTALL_DIR"/{config,data,logs,backups}
    
    # 현재 사용자 권한 설정
    sudo chown -R $(whoami):$(whoami) "$INSTALL_DIR"
}

# 3. 파일 다운로드
download_files() {
    log "Downloading appliance files..."
    
    cd "$INSTALL_DIR"
    
    # Docker Compose 파일
    curl -fsSL \
        "https://releases.trading-bot.io/${APPLIANCE_VERSION}/docker-compose.yml" \
        -o docker-compose.yml
    
    # 스크립트
    curl -fsSL \
        "https://releases.trading-bot.io/${APPLIANCE_VERSION}/scripts.tar.gz" \
        | tar -xz
    
    # 기본 설정
    curl -fsSL \
        "https://releases.trading-bot.io/${APPLIANCE_VERSION}/config.defaults.yml" \
        -o config/appliance.yml
}

# 4. 라이선스 입력
setup_license() {
    log "Setting up license..."
    
    echo ""
    echo "Please enter your license key:"
    read -s LICENSE_KEY
    
    echo "$LICENSE_KEY" > "$INSTALL_DIR/LICENSE.key"
    
    # 라이선스 검증
    if ! verify_license; then
        log "Error: Invalid license key"
        exit 1
    fi
}

# 5. 시작
start_appliance() {
    log "Starting Trading Bot Appliance..."
    
    cd "$INSTALL_DIR"
    docker-compose up -d
    
    # Health check
    sleep 10
    
    if docker-compose ps | grep -q "healthy"; then
        log "✓ Appliance started successfully!"
        log ""
        log "Management UI: http://localhost:8080"
        log "Default credentials: admin / (setup during first run)"
    else
        log "✗ Failed to start appliance"
        docker-compose logs
        exit 1
    fi
}

# 메인
main() {
    echo "========================================"
    echo "  Trading Bot Appliance Installer"
    echo "========================================"
    echo ""
    
    check_system
    setup_directories
    download_files
    setup_license
    start_appliance
    
    echo ""
    echo "Installation complete!"
    echo "Run 'sudo $INSTALL_DIR/scripts/status.sh' to check status"
}

main "$@"
```

### 5.2 설치 방법

**Linux/macOS:**
```bash
# 원클릭 설치
curl -fsSL https://get.trading-bot.io | bash

# 또는 특정 버전
curl -fsSL https://get.trading-bot.io | bash -s -- v1.2.0
```

**Windows (PowerShell):**
```powershell
# 원클릭 설치
iwr -useb https://get.trading-bot.io/win | iex
```

---

## 6. 업데이트 메커니즘

### 6.1 자동 업데이트 (선택)

```yaml
# config/appliance.yml
updates:
  enabled: false              # 기본 비활성화
  channel: stable             # stable, beta, nightly
  auto_install: false         # 자동 설치 여부
  schedule: "0 3 * * *"      # 업데이트 확인 시간 (3AM)
  backup_before_update: true  # 업데이트 전 백업
```

### 6.2 수동 업데이트

```bash
# CLI
/opt/trading-bot/scripts/update.sh

# 또는 Manager UI에서 "Check for Updates" 클릭
```

---

## 7. 백업/복구

### 7.1 자동 백업

```yaml
# config/appliance.yml
backup:
  enabled: true
  schedule: "0 2 * * *"      # 매일 2AM
  retention: 7               # 7일간 보관
  destination: local          # local, s3, gcs
  
  s3:                        # S3 사용 시 (선택)
    bucket: my-backups
    region: us-east-1
```

### 7.2 수동 백업/복구

```bash
# 백업
/opt/trading-bot/scripts/backup.sh

# 복구
/opt/trading-bot/scripts/restore.sh /path/to/backup.tar.gz
```

---

## 8. 라이선스 모델

### 8.1 라이선스 티어

| 티어 | 가격 | 기능 | 기간 |
|------|------|------|------|
| **Trial** | Free | 기본 전략 1개, Paper Trading | 14일 |
| **Basic** | $99/mo | 전략 3개, Live Trading | 월간 |
| **Pro** | $299/mo | 무제한 전략, 모든 기능 | 월간 |
| **Enterprise** | Custom | 멀티 인스턴스, SLA | 연간 |

### 8.2 라이선스 파일 형식

```json
{
  "license_id": "LIC-2026-ABC123",
  "customer": "customer@example.com",
  "tier": "pro",
  "features": ["live_trading", "unlimited_strategies", "telegram", "backtesting"],
  "exp": "2027-02-12T00:00:00Z",
  "hwid": "optional-hardware-binding",
  "max_instances": 1,
  "signature": "RSA-SHA256-signature"
}
```

---

## 9. 구현 단계

### Phase 1: 기본 구조 (2주)
- [ ] Appliance Manager UI 개발
- [ ] Docker Compose 구조 재설계
- [ ] 설치 스크립트 작성

### Phase 2: 라이선스 시스템 (1주)
- [ ] License Manager 구현
- [ ] 라이선스 생성 도구 (내부용)
- [ ] 하드웨어 ID 바인딩 (선택)

### Phase 3: OTA 업데이터 (2주)
- [ ] Update Engine 구현
- [ ] Zero-downtime 배포
- [ ] 롤백 메커니즘

### Phase 4: 백업/복구 (1주)
- [ ] 자동 백업 시스템
- [ ] 원격 백업 (S3) 지원
- [ ] 원클릭 복구

### Phase 5: 테스트/문서화 (2주)
- [ ] 다양한 환경에서 테스트
- [ ] 설치 가이드 작성
- [ ] 비디오 튜토리얼 제작

---

## 10. 비용 분석

### 개발 비용
- Appliance Manager UI: 2주
- License System: 1주
- OTA Updater: 2주
- Testing: 2주
**총: 7주**

### 인프라 비용 (월간)
- 업데이트 서버: $50
- 라이선스 검증 API: $30
- 텔레메트리 (선택): $20
**총: ~$100/월**

---

## 결론

**Docker Compose + Web Manager** 모델이 최적입니다:

✅ **설치 용이성**: 원클릭 설치 스크립트
✅ **무중단 업데이트**: Blue-Green 배포
✅ **라이선스 관리**: 오프라인 가능한 검증
✅ **모니터링**: 선택적 텔레메트리
✅ **백업/복구**: 자동화된 시스템
✅ **유지보수**: Docker 기반으로 용이

**다음 단계**: Phase 1 구현 시작
