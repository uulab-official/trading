# Desktop Application Architecture

## 사용자 요구사항 분석

**현재**: 서버 기반 (Docker, 24/7 실행)
**원하는 것**: PC 설치형 프로그램 (사용자가 직접 실행/중지)

**핵심 차이점:**
- 서버형: 항상 켜져 있음, 원격 접속, Docker
- PC형: 사용자가 실행/중지, GUI 환경, Standalone

---

## 아키텍처 옵션 비교

### Option 1: Electron + Python (권장)

```
┌─────────────────────────────────────────┐
│         Electron Frontend               │
│  (React/Vue.js + Desktop GUI)           │
├─────────────────────────────────────────┤
│         Python Backend                  │
│  (Trading Bot Core - 현재 코드 재사용)   │
│                                         │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐  │
│  │Exchange │ │  Risk   │ │ Portfolio│  │
│  │ Manager │ │ Manager │ │ Manager  │  │
│  └────┬────┘ └────┬────┘ └────┬─────┘  │
│       └───────────┴───────────┘         │
│                   │                     │
│           ┌───────▼───────┐             │
│           │  SQLite DB    │             │
│           │  (Local)      │             │
│           └───────────────┘             │
└─────────────────────────────────────────┘
              │
              ▼
    ┌─────────────────────┐
    │   Python Subprocess │
    │   (spawned by Node) │
    └─────────────────────┘
```

**장점:**
- ✅ 현재 Python 코드 90% 재사용
- ✅ Modern UI (React/Vue)
- ✅ Windows/Mac/Linux 지원
- ✅ Auto-updater 내장 (Electron)
- ✅ 크로스 플랫폼 빌드 용이

**단점:**
- ⚠️ 앱 크기 큼 (~100-200MB)
- ⚠️ 메모리 사용량 높음

**사용 예시:**
```bash
# 설치
TradingBot-Setup-1.0.0.exe  # Windows
TradingBot-1.0.0.dmg        # macOS
TradingBot-1.0.0.AppImage   # Linux

# 실행
사용자가 아이콘 더블클릭 → GUI 창 열림 → "시작" 버튼 클릭 → 트레이딩 시작
```

---

### Option 2: PyQt6/PySide6 (Python Native)

```python
# main.py
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow
from trading_core import TradingBot

class TradingBotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bot = TradingBot()
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("Trading Bot")
        self.setGeometry(100, 100, 1200, 800)
        
        # Start/Stop 버튼
        # 설정 패널
        # 차트/그래프
        # 로그 뷰어
```

**장점:**
- ✅ 순수 Python (의존성 단순)
- ✅ 빠른 성능
- ✅ Native look & feel
- ✅ PyInstaller로 exe 생성

**단점:**
- ⚠️ UI 개발 속도 느림
- ⚠️ 현대적 UI 어려움
- ⚠️ Web 기술 스택 아님

---

### Option 3: Tauri + Python (경량)

```
┌─────────────────────────────┐
│      Tauri Frontend         │
│  (WebView2 + Rust core)     │
│  - React/Vue/Svelte         │
├─────────────────────────────┤
│      Rust Backend           │
│  - Window management        │
│  - System integration       │
│  - Spawns Python process    │
├─────────────────────────────┤
│      Python Engine          │
│  (Trading Bot Logic)        │
└─────────────────────────────┘
```

**장점:**
- ✅ 매우 작은 바이너리 (~5-10MB)
- ✅ 빠른 성능
- ✅ Modern web UI
- ✅ Rust의 안전성

**단점:**
- ⚠️ Rust 학습 필요
- ⚠️ Python interop 복잡
- ⚠️ 상대적으로 새로운 기술

---

### Option 4: Local Web Server + Browser (현재와 유사)

```
사용자 PC
├── Trading Bot Daemon (Python)
│   ├── Local Web Server (Flask/FastAPI)
│   └── Trading Engine
│
└── User opens browser
    └── http://localhost:8080
```

**장점:**
- ✅ 현재 아키텍처 그대로 사용
- ✅ 브라우저만 있으면 됨
- ✅ 모바일에서도 접속 가능

**단점:**
- ⚠️ "프로그램" 느낌 약함
- ⚠️ 브라우저 별도 실행 필요
- ⚠️ 아이콘/시스템 트레이 없음

---

## 추천: Electron + Python

### 왜 Electron인가?

| 기준 | Electron | PyQt | Tauri | Web |
|------|----------|------|-------|-----|
| 개발 속도 | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ | ⭐⭐⭐ |
| UI 품질 | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ |
| 코드 재사용 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |
| 배포 용이성 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| 사용자 경험 | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 학습 곡선 | ⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐⭐ |

**결정 이유:**
1. 현재 Python 코드 거의 그대로 사용 가능
2. React/Vue로 빠르게 UI 개발
3. 사용자에게 익숙한 "프로그램" 경험
4. VS Code, Slack, Discord 등 검증된 기술

---

## 상세 아키텍처

### 1. 프로젝트 구조

```
trading-bot-desktop/
├── desktop/                    # Electron 앱
│   ├── package.json
│   ├── src/
│   │   ├── main/              # Main process (Node.js)
│   │   │   ├── index.ts       # Entry point
│   │   │   ├── python-bridge.ts # Python 통신
│   │   │   └── auto-updater.ts  # 자동 업데이트
│   │   ├── renderer/          # Renderer process (React)
│   │   │   ├── components/    # UI 컴포넌트
│   │   │   ├── pages/         # 페이지
│   │   │   └── App.tsx        # 메인 앱
│   │   └── preload/           # Preload scripts
│   └── assets/                # 아이콘, 이미지
│
├── engine/                     # Python 트레이딩 엔진
│   └── trading-monorepo/      # 현재 코드 이동
│
├── shared/                     # 공통 타입/상수
│   └── types/
│
└── build/                      # 빌드 설정
    ├── electron-builder.yml
    └── scripts/
```

### 2. Python ↔ Electron 통신

**방식 1: IPC (권장)**
```typescript
// desktop/src/main/python-bridge.ts
import { spawn } from 'child_process';
import { ipcMain } from 'electron';

class PythonBridge {
  private pythonProcess: any;
  
  startEngine() {
    // Python 프로세스 시작
    this.pythonProcess = spawn('python', [
      '-m', 'trading_bot.desktop_server',  # 특별 서버 모드
      '--port', '0',  # Random available port
    ]);
    
    // stdout/stderr 파이프
    this.pythonProcess.stdout.on('data', (data) => {
      // Python 로그 → Electron
      mainWindow.webContents.send('python-log', data.toString());
    });
  }
  
  sendCommand(command: string, params: any) {
    this.pythonProcess.stdin.write(JSON.stringify({ command, params }) + '\n');
  }
}

// IPC 핸들러
ipcMain.handle('trading:start', async () => {
  return pythonBridge.startEngine();
});

ipcMain.handle('trading:stop', async () => {
  return pythonBridge.stopEngine();
});

ipcMain.handle('config:load', async () => {
  return pythonBridge.sendCommand('config:load', {});
});
```

**Python 측:**
```python
# trading_bot/desktop_server.py
import sys
import json
from trading_core import TradingBot

class DesktopServer:
    """Electron과 통신하는 서버"""
    
    def __init__(self):
        self.bot = TradingBot()
        self.running = False
    
    def run(self):
        """stdin/stdout 기반 IPC 루프"""
        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                
                message = json.loads(line)
                command = message.get('command')
                params = message.get('params', {})
                
                response = self.handle_command(command, params)
                print(json.dumps(response), flush=True)
                
            except Exception as e:
                print(json.dumps({'error': str(e)}), flush=True)
    
    def handle_command(self, command: str, params: dict) -> dict:
        if command == 'trading:start':
            self.bot.start()
            return {'status': 'started'}
        
        elif command == 'trading:stop':
            self.bot.stop()
            return {'status': 'stopped'}
        
        elif command == 'config:load':
            config = self.bot.load_config()
            return {'config': config}
        
        elif command == 'portfolio:get':
            portfolio = self.bot.portfolio.to_dict()
            return {'portfolio': portfolio}
        
        # ... 더 많은 명령어

if __name__ == '__main__':
    server = DesktopServer()
    server.run()
```

### 3. UI 레이아웃

```tsx
// desktop/src/renderer/App.tsx
import React, { useState, useEffect } from 'react';
import { Layout, Menu, StatusBar, Chart, LogViewer } from './components';

function App() {
  const [botStatus, setBotStatus] = useState<'stopped' | 'running' | 'error'>('stopped');
  const [portfolio, setPortfolio] = useState(null);
  const [logs, setLogs] = useState([]);
  
  // Python 로그 수신
  useEffect(() => {
    window.electron.onPythonLog((log) => {
      setLogs(prev => [...prev, log]);
    });
  }, []);
  
  const handleStart = async () => {
    await window.electron.invoke('trading:start');
    setBotStatus('running');
  };
  
  const handleStop = async () => {
    await window.electron.invoke('trading:stop');
    setBotStatus('stopped');
  };
  
  return (
    <Layout>
      <Menu />
      
      <main>
        {/* 상태 표시 */}
        <StatusBar status={botStatus} />
        
        {/* 시작/중지 버튼 */}
        <div className="controls">
          {botStatus === 'stopped' ? (
            <button onClick={handleStart} className="btn-primary">
              ▶ Start Trading
            </button>
          ) : (
            <button onClick={handleStop} className="btn-danger">
              ⏹ Stop Trading
            </button>
          )}
        </div>
        
        {/* 포트폴리오 차트 */}
        <Chart data={portfolio} />
        
        {/* 로그 뷰어 */}
        <LogViewer logs={logs} />
      </main>
    </Layout>
  );
}
```

### 4. 데이터 저장

```
사용자 PC
└── %APPDATA%/TradingBot/        (Windows)
    └── ~/Library/Application Support/TradingBot/  (macOS)
        └── ~/.config/TradingBot/                  (Linux)
            ├── config/
            │   ├── settings.json      # 앱 설정
│   │   └── trading_config.yml  # 트레이딩 설정
│   ├── data/
│   │   └── trading.db          # SQLite
│   └── logs/
│       └── trading.log         # 로그
```

### 5. 설치/배포

**electron-builder 설정:**
```yaml
# electron-builder.yml
appId: io.tradingbot.desktop
productName: TradingBot
copyright: Copyright © 2026 UULab

directories:
  output: dist
  buildResources: build

files:
  - "desktop/dist/**/*"
  - "engine/**/*"  # Python 코드 포함
  - "node_modules/**/*"

extraResources:
  - from: engine/
    to: engine/
    filter:
      - "**/*"
      - "!**/__pycache__/**"
      - "!**/*.pyc"

# Windows
win:
  target:
    - target: nsis
      arch:
        - x64
        - ia32
  icon: build/icon.ico

nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: always

# macOS
mac:
  target:
    - target: dmg
      arch:
        - x64
        - arm64
  icon: build/icon.icns
  category: public.app-category.finance

# Linux
linux:
  target:
    - AppImage
    - deb
    - rpm
  icon: build/icon.png
  category: Office
```

**빌드:**
```bash
# Python 종속성 설치
pip install -r engine/requirements.txt

# Desktop 빌드
cd desktop
npm install
npm run build

# 패키징
npm run dist

# 결과
# - dist/TradingBot-Setup-1.0.0.exe (Windows)
# - dist/TradingBot-1.0.0.dmg (macOS)
# - dist/TradingBot-1.0.0.AppImage (Linux)
```

---

## 구현 단계

### Phase 1: 기본 구조 (2주)
- [ ] Electron 프로젝트 설정
- [ ] Python IPC 서버 구현
- [ ] 기본 UI (시작/중지 버튼, 로그 뷰어)
- [ ] 데이터 저장소 설정

### Phase 2: 트레이딩 기능 (2주)
- [ ] 설정 화면
- [ ] 포트폴리오 표시
- [ ] 차트/그래프
- [ ] 텔레그램 알림 설정

### Phase 3: 고급 기능 (2주)
- [ ] 백테스팅 UI
- [ ] 전략 편집기
- [ ] 라이선스 관리
- [ ] 자동 업데이트

### Phase 4: 배포 (1주)
- [ ] 코드 서명
- [ ] 인스톨러 테스트
- [ ] 문서 작성

**총 예상 시간: 7주**

---

## 장단점 요약

### 장점
- ✅ 사용자 PC에서 직접 실행 (서버 불필요)
- ✅ 프로그램 느낌 (아이콘, 트레이, 알림)
- ✅ 오프라인 작동 가능
- ✅ 빠른 반응속도 (로컬 실행)
- ✅ 쉬운 설치 (exe/dmg)

### 단점
- ⚠️ 사용자 PC가 켜져 있어야 함 (24/7 불가)
- ⚠️ PC 리소스 사용
- ⚠️ 백신 오탐지 가능성 (Python 패키징)
- ⚠️ 업데이트 관리 복잡

---

## 결론

**Electron + Python**이 가장 현실적인 선택입니다:

1. **현재 코드 대부분 재사용**
2. **빠른 개발** (React UI)
3. **검증된 기술** (VS Code, Discord 등)
4. **사용자에게 익숙한 경험**

**단, 한 가지 중요한 차이점:**
- 서버형: 24/7 실행, 항상 모니터링
- PC형: 사용자가 실행할 때만 작동

**권장:** 하이브리드 접근
- **Desktop App**: 설정, 모니터링, 백테스팅
- **Background Service**: 실제 트레이딩 (Windows Service/Systemd)

이렇게 하면 사용자는 GUI로 설정하고, 백그라운드에서 자동으로 트레이딩합니다.
