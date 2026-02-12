"""Configuration manager - 설정 관리 시스템"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    """텔레그램 설정"""
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    notifications: Dict[str, bool] = field(default_factory=dict)
    commands_enabled: bool = True
    allowed_users: List[int] = field(default_factory=list)


@dataclass
class ExchangeConfig:
    """거래소 설정"""
    id: str = "binance"
    sandbox: bool = True
    api_key: str = ""
    api_secret: str = ""
    default_type: str = "spot"
    testnet: bool = True


@dataclass
class RiskConfig:
    """리스크 관리 설정"""
    max_daily_loss_pct: float = 3.0
    max_daily_trades: int = 10
    max_position_size_pct: float = 10.0
    max_positions: int = 2
    risk_per_trade_pct: float = 1.0
    max_leverage: float = 1.0
    default_stop_loss_pct: float = 2.0
    default_take_profit_pct: float = 4.0
    circuit_breaker_loss_pct: float = 5.0
    cooldown_minutes_after_loss: int = 30


@dataclass
class StrategyConfig:
    """전략 설정"""
    name: str = ""
    enabled: bool = False
    type: str = ""
    symbol: str = ""
    timeframe: str = "1h"
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BotConfig:
    """봇 전체 설정"""
    bot_id: str = "trading-bot"
    name: str = "Trading Bot"
    version: str = "1.0.0"
    mode: str = "paper"  # paper | live
    timezone: str = "Asia/Seoul"
    
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategies: List[StrategyConfig] = field(default_factory=list)


class ConfigManager:
    """설정 관리자 - 템플릿 기반 설정 로드"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # 하위 디렉토리 생성
        (self.config_dir / "active").mkdir(exist_ok=True)
        (self.config_dir / "secrets").mkdir(exist_ok=True)
        
        self._config: Optional[BotConfig] = None
        self._secrets: Dict[str, Any] = {}
        
    def load_config(
        self,
        config_name: str = "bot_config",
        secrets_name: str = "secrets",
        use_env_vars: bool = True
    ) -> BotConfig:
        """
        설정 로드
        
        Args:
            config_name: 설정 파일 이름 (확장자 제외)
            secrets_name: secrets 파일 이름
            use_env_vars: 환경 변수 치환 사용
        
        Returns:
            BotConfig
        """
        # 설정 파일 경로
        config_path = self.config_dir / "active" / f"{config_name}.yml"
        secrets_path = self.config_dir / "secrets" / f"{secrets_name}.yml"
        
        # 기본 설정 로드 (템플릿에서 복사된 파일)
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Please copy from config/templates/bot_config.template.yml"
            )
        
        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
        
        # 환경 변수 치환
        if use_env_vars:
            raw_config = self._substitute_env_vars(raw_config)
        
        # Secrets 로드
        if secrets_path.exists():
            with open(secrets_path, 'r', encoding='utf-8') as f:
                self._secrets = yaml.safe_load(f) or {}
        else:
            logger.warning(f"Secrets file not found: {secrets_path}")
            self._secrets = {}
        
        # Secrets 적용
        raw_config = self._apply_secrets(raw_config)
        
        # BotConfig 생성
        self._config = self._parse_config(raw_config)
        
        logger.info(f"Configuration loaded: {config_path}")
        return self._config
    
    def _substitute_env_vars(self, obj: Any) -> Any:
        """환경 변수 치환"""
        if isinstance(obj, dict):
            return {k: self._substitute_env_vars(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # ${VAR_NAME} 패턴 치환
            pattern = r'\$\{([^}]+)\}'
            
            def replace_var(match):
                var_name = match.group(1)
                return os.getenv(var_name, match.group(0))
            
            return re.sub(pattern, replace_var, obj)
        else:
            return obj
    
    def _apply_secrets(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Secrets 적용"""
        # Exchange API keys
        if 'exchange' in config:
            exchange_id = config['exchange'].get('id', 'binance')
            if exchange_id in self._secrets:
                exchange_secrets = self._secrets[exchange_id]
                config['exchange']['api_key'] = exchange_secrets.get('api_key', '')
                config['exchange']['api_secret'] = exchange_secrets.get('api_secret', '')
        
        # Telegram
        if 'telegram' in config and 'telegram' in self._secrets:
            tg_secrets = self._secrets['telegram']
            config['telegram']['bot_token'] = tg_secrets.get('bot_token', '')
            config['telegram']['chat_id'] = tg_secrets.get('chat_id', '')
            config['telegram']['allowed_users'] = tg_secrets.get('allowed_user_ids', [])
        
        return config
    
    def _parse_config(self, raw: Dict[str, Any]) -> BotConfig:
        """원시 설정을 BotConfig로 변환"""
        config = BotConfig()
        
        # 기본 설정
        bot_settings = raw.get('bot', {})
        config.bot_id = bot_settings.get('id', 'trading-bot')
        config.name = bot_settings.get('name', 'Trading Bot')
        config.version = bot_settings.get('version', '1.0.0')
        config.mode = bot_settings.get('mode', 'paper')
        config.timezone = bot_settings.get('timezone', 'Asia/Seoul')
        
        # Exchange
        if 'exchange' in raw:
            ex = raw['exchange']
            config.exchange = ExchangeConfig(
                id=ex.get('id', 'binance'),
                sandbox=ex.get('sandbox', True),
                api_key=ex.get('api_key', ''),
                api_secret=ex.get('api_secret', ''),
                default_type=ex.get('default_type', 'spot'),
                testnet=ex.get('testnet', True)
            )
        
        # Telegram
        if 'telegram' in raw:
            tg = raw['telegram']
            config.telegram = TelegramConfig(
                enabled=tg.get('enabled', False),
                bot_token=tg.get('bot_token', ''),
                chat_id=tg.get('chat_id', ''),
                notifications=tg.get('notifications', {}),
                commands_enabled=tg.get('commands', {}).get('enabled', True),
                allowed_users=tg.get('commands', {}).get('allowed_users', [])
            )
        
        # Risk
        if 'risk' in raw:
            r = raw['risk']
            config.risk = RiskConfig(
                max_daily_loss_pct=r.get('max_daily_loss_pct', 3.0),
                max_daily_trades=r.get('max_daily_trades', 10),
                max_position_size_pct=r.get('max_position_size_pct', 10.0),
                max_positions=r.get('max_positions', 2),
                risk_per_trade_pct=r.get('risk_per_trade_pct', 1.0),
                max_leverage=r.get('max_leverage', 1.0),
                default_stop_loss_pct=r.get('default_stop_loss_pct', 2.0),
                default_take_profit_pct=r.get('default_take_profit_pct', 4.0),
                circuit_breaker_loss_pct=r.get('circuit_breaker_loss_pct', 5.0),
                cooldown_minutes_after_loss=r.get('cooldown_minutes_after_loss', 30)
            )
        
        # Strategies
        if 'strategies' in raw:
            for s in raw['strategies']:
                config.strategies.append(StrategyConfig(
                    name=s.get('name', ''),
                    enabled=s.get('enabled', False),
                    type=s.get('type', ''),
                    symbol=s.get('symbol', ''),
                    timeframe=s.get('timeframe', '1h'),
                    params=s.get('params', {})
                ))
        
        return config
    
    def get_config(self) -> Optional[BotConfig]:
        """현재 설정 반환"""
        return self._config
    
    def validate_config(self) -> List[str]:
        """설정 검증 - 오류 메시지 리스트 반환"""
        errors = []
        
        if not self._config:
            errors.append("Configuration not loaded")
            return errors
        
        # Paper/Live 모드 검증
        if self._config.mode == 'live':
            if not self._config.exchange.api_key or not self._config.exchange.api_secret:
                errors.append("Live mode requires API keys")
            
            if self._config.exchange.sandbox:
                errors.append("Live mode should not use sandbox")
        
        # Telegram 설정 검증
        if self._config.telegram.enabled:
            if not self._config.telegram.bot_token:
                errors.append("Telegram enabled but bot_token not set")
            if not self._config.telegram.chat_id:
                errors.append("Telegram enabled but chat_id not set")
        
        # 리스크 설정 검증
        if self._config.risk.max_daily_loss_pct <= 0:
            errors.append("max_daily_loss_pct must be positive")
        
        if self._config.risk.max_leverage > 10:
            errors.append("max_leverage seems too high (>10)")
        
        # 전략 검증
        active_strategies = [s for s in self._config.strategies if s.enabled]
        if not active_strategies:
            errors.append("No active strategies")
        
        return errors
    
    def create_from_template(
        self,
        template_name: str = "bot_config.template",
        target_name: str = "bot_config"
    ) -> Path:
        """
        템플릿에서 새 설정 파일 생성
        
        Returns:
            생성된 파일 경로
        """
        template_path = self.config_dir / "templates" / f"{template_name}.yml"
        target_path = self.config_dir / "active" / f"{target_name}.yml"
        
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")
        
        if target_path.exists():
            raise FileExistsError(f"Config already exists: {target_path}")
        
        import shutil
        shutil.copy(template_path, target_path)
        
        logger.info(f"Config created from template: {target_path}")
        return target_path
    
    def is_production_ready(self) -> bool:
        """실전 투입 준비되었는지 확인"""
        if not self._config:
            return False
        
        return (
            self._config.mode == 'live' and
            bool(self._config.exchange.api_key) and
            bool(self._config.exchange.api_secret) and
            not self._config.exchange.sandbox and
            len(self.validate_config()) == 0
        )


# 싱글톤 인스턴스
_config_manager: Optional[ConfigManager] = None


def get_config_manager(config_dir: str = "config") -> ConfigManager:
    """설정 관리자 싱글톤 반환"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_dir)
    return _config_manager


def load_config(config_dir: str = "config", **kwargs) -> BotConfig:
    """간편 설정 로드 함수"""
    return get_config_manager(config_dir).load_config(**kwargs)
