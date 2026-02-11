# 텔레그램 알림 설정 가이드

## 개요

Trading Bot의 텔레그램 알림 기능을 설정하는 방법을 설명합니다.

**보안 주의**: 봇 토큰과 채팅 ID는 민감한 정보이므로 절대 Git에 커밋하지 마세요!

---

## 1. 텔레그램 봇 생성

### 1.1 BotFather로 봇 생성

1. 텔레그램에서 **@BotFather** 검색
2. `/newbot` 명령어 입력
3. 봇 이름 입력 (예: "My Trading Bot")
4. 봇 사용자명 입력 (예: "my_trading_bot_123") - **bot**으로 끝나야 함
5. **Bot Token**을 받음 (형식: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

```
예시 출력:
Done! Congratulations on your new bot. You will find it at t.me/my_trading_bot_123. 
You can now add a description, about section and profile picture for your bot, 
see /help for a list of commands.

Use this token to access the HTTP API:
123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

### 1.2 채팅 ID 확인

봇 토큰을 받은 후, 채팅 ID를 확인해야 합니다.

**방법 1: @userinfobot 사용**
1. @userinfobot 검색 후 시작
2. "Id: 123456789" 형태로 ID 확인

**방법 2: API 직접 호출**
```bash
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

채팅 ID는 양수(개인) 또는 음수(그룹)일 수 있습니다.

---

## 2. 설정 파일 구성

### 2.1 템플릿에서 설정 파일 생성

```bash
# 템플릿 복사
cp config/templates/secrets.template.yml config/secrets/secrets.yml
```

### 2.2 secrets.yml 편집

```yaml
telegram:
  bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
  chat_id: "123456789"
  allowed_user_ids: []  # 선택: 특정 사용자만 허용
```

### 2.3 봇 설정 활성화

```bash
# 템플릿 복사
cp config/templates/bot_config.template.yml config/active/bot_config.yml
```

`bot_config.yml`에서 텔레그램 섹션을 활성화:

```yaml
telegram:
  enabled: true
  bot_token_ref: "${TELEGRAM_BOT_TOKEN}"  # 또는 secrets에서 자동 로드
  chat_id_ref: "${TELEGRAM_CHAT_ID}"
  
  notifications:
    order_filled: true
    position_closed: true
    daily_report: true
    risk_alert: true
    error_alert: true
  
  commands:
    enabled: true
    allowed_users: []
```

---

## 3. 환경 변수 사용 (선택)

보안을 위해 환경 변수를 사용할 수 있습니다:

```bash
# .env 파일 생성
echo "TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz" >> apps/trading-bot/.env
echo "TELEGRAM_CHAT_ID=123456789" >> apps/trading-bot/.env
```

또는 실행 시 직접 설정:

```bash
TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy python -m trading_bot.main
```

---

## 4. 사용 가능한 알림

### 4.1 주문 알림
- 주문 생성/체결 시
- 매수/매도 방향
- 수량 및 가격
- 수수료 정보

### 4.2 포지션 알림
- 포지션 진입/청산
- 진입가/청산가
- 손익 (PnL)
- 보유 기간

### 4.3 리스크 알림
- 일일 손실 한도 도달
- 서킷 브레이커 발동
- 비정상적인 거래 패턴

### 4.4 일일 리포트
- 매일 UTC 00:00에 자동 전송
- 총 수익률
- 거래 통계
- 현재 포지션

### 4.5 에러 알림
- 시스템 오류
- API 연결 문제
- 주문 실패

---

## 5. 텔레그램 명령어

봇이 실행 중일 때 다음 명령어를 사용할 수 있습니다:

| 명령어 | 설명 |
|--------|------|
| `/start` | 봇 시작 및 도움말 |
| `/status` | 봇 상태 확인 |
| `/portfolio` | 포트폴리오 조회 |
| `/positions` | 열린 포지션 조회 |
| `/daily` | 오늘의 리포트 |
| `/help` | 명령어 목록 |

---

## 6. 보안 설정

### 6.1 사용자 제한

특정 사용자만 명령어를 사용하도록 제한:

```yaml
telegram:
  commands:
    enabled: true
    allowed_users:
      - 123456789   # 허용된 사용자 ID
      - 987654321
```

### 6.2 프라이빗 채널 사용

봇을 프라이빗 채널에 추가하여 그룹 알림 사용:

1. 새로운 그룹 생성
2. 봇을 그룹에 추가
3. 그룹 ID 확인 (음수 값)

```yaml
chat_id: "-1001234567890"  # 그룹 ID는 음수
```

---

## 7. 테스트

### 7.1 알림 테스트

```python
from trading_monitor import TelegramNotifier
import asyncio

async def test():
    notifier = TelegramNotifier(
        bot_token="YOUR_TOKEN",
        chat_id="YOUR_CHAT_ID"
    )
    await notifier.initialize()
    await notifier.send_message("🧪 테스트 메시지입니다!")

asyncio.run(test())
```

### 7.2 설정 검증

```bash
python -c "
from trading_core import ConfigManager
mgr = ConfigManager()
config = mgr.load_config()
errors = mgr.validate_config()
if errors:
    print('❌ Errors:', errors)
else:
    print('✅ Config valid!')
    print(f'Telegram enabled: {config.telegram.enabled}')
"
```

---

## 8. 문제 해결

### 봇이 응답하지 않음
- 봇 토큰 확인
- chat_id 정확성 확인
- 봇이 차단되지 않았는지 확인

### 메시지가 안 옴
- `enabled: true` 확인
- `notifications` 섹션 확인
- 로그 확인 (`logs/trading-bot.log`)

### 403 Forbidden 에러
- 봇이 채널/그룹에 추가되었는지 확인
- 권한 확인

---

## 9. 상용화 시 주의사항

### 고객에게 제공 시
1. **템플릿만 제공**: `secrets.template.yml`만 제공
2. **설정 가이드**: 이 문서를 함께 제공
3. **보안 강조**: 고객이 직접 토큰을 입력하도록 안내
4. **라이선스**: LICENSE 파일 포함

### 예시 제공 파일 구조
```
trading-bot-commercial/
├── config/
│   ├── templates/
│   │   ├── bot_config.template.yml
│   │   └── secrets.template.yml
│   ├── active/           # 고객이 생성
│   └── secrets/          # 고객이 생성
├── docs/
│   └── TELEGRAM_SETUP.md
├── LICENSE
└── README.md
```

---

## 10. 참고 자료

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [python-telegram-bot](https://python-telegram-bot.org/)
- [BotFather](https://t.me/botfather)
