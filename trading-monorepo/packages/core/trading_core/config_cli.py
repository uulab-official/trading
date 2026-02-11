"""Configuration CLI - 설정 관리 CLI 도구"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from trading_core.config_manager import ConfigManager, get_config_manager
import yaml


def cmd_init(args):
    """초기 설정 생성"""
    print("🚀 Trading Bot Configuration Setup\n")
    
    config_dir = Path(args.config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # 템플릿 복사
    templates_dir = config_dir / "templates"
    active_dir = config_dir / "active"
    secrets_dir = config_dir / "secrets"
    
    active_dir.mkdir(exist_ok=True)
    secrets_dir.mkdir(exist_ok=True)
    
    # bot_config 복사
    bot_config_template = templates_dir / "bot_config.template.yml"
    bot_config_target = active_dir / "bot_config.yml"
    
    if not bot_config_target.exists():
        import shutil
        shutil.copy(bot_config_template, bot_config_target)
        print(f"✅ Created: {bot_config_target}")
    else:
        print(f"⚠️  Already exists: {bot_config_target}")
    
    # secrets 복사
    secrets_template = templates_dir / "secrets.template.yml"
    secrets_target = secrets_dir / "secrets.yml"
    
    if not secrets_target.exists():
        import shutil
        shutil.copy(secrets_template, secrets_target)
        print(f"✅ Created: {secrets_target}")
        print("\n⚠️  IMPORTANT: Edit secrets.yml with your actual API keys!")
    else:
        print(f"⚠️  Already exists: {secrets_target}")
    
    print("\n" + "="*60)
    print("Next steps:")
    print("  1. Edit config/active/bot_config.yml")
    print("  2. Edit config/secrets/secrets.yml (NEVER commit this!)")
    print("  3. Run: python -m trading_core.config_cli validate")
    print("="*60)


def cmd_validate(args):
    """설정 검증"""
    print("🔍 Validating configuration...\n")
    
    try:
        mgr = get_config_manager(args.config_dir)
        config = mgr.load_config()
        
        errors = mgr.validate_config()
        
        if errors:
            print("❌ Configuration errors found:")
            for error in errors:
                print(f"   - {error}")
            sys.exit(1)
        else:
            print("✅ Configuration is valid!")
            print(f"\nBot: {config.name} (v{config.version})")
            print(f"Mode: {config.mode}")
            print(f"Exchange: {config.exchange.id}")
            print(f"Telegram: {'enabled' if config.telegram.enabled else 'disabled'}")
            print(f"Strategies: {len([s for s in config.strategies if s.enabled])} active")
            
            if mgr.is_production_ready():
                print("\n🚀 Production ready!")
            else:
                print("\n⚠️  Not production ready (paper mode or missing settings)")
                
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_telegram_setup(args):
    """텔레그램 설정 마법사"""
    print("🤖 Telegram Bot Setup Wizard\n")
    print("="*60)
    
    # Bot Token 입력
    print("\n1. Bot Token (from @BotFather)")
    print("   Format: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    bot_token = getpass.getpass("   Bot Token: ")
    
    if not bot_token or ":" not in bot_token:
        print("❌ Invalid bot token format!")
        sys.exit(1)
    
    # Chat ID 입력
    print("\n2. Chat ID")
    print("   Personal: 123456789 (positive)")
    print("   Group: -1001234567890 (negative)")
    chat_id = input("   Chat ID: ").strip()
    
    if not chat_id:
        print("❌ Chat ID is required!")
        sys.exit(1)
    
    # 설정 파일 업데이트
    config_dir = Path(args.config_dir)
    secrets_file = config_dir / "secrets" / "secrets.yml"
    
    if not secrets_file.exists():
        print(f"❌ Secrets file not found: {secrets_file}")
        print("Run: python -m trading_core.config_cli init")
        sys.exit(1)
    
    # 기존 설정 로드
    with open(secrets_file, 'r') as f:
        secrets = yaml.safe_load(f) or {}
    
    # 텔레그램 설정 업데이트
    secrets['telegram'] = {
        'bot_token': bot_token,
        'chat_id': chat_id,
        'allowed_user_ids': []
    }
    
    # 저장
    with open(secrets_file, 'w') as f:
        yaml.dump(secrets, f, default_flow_style=False)
    
    # bot_config도 업데이트
    bot_config_file = config_dir / "active" / "bot_config.yml"
    if bot_config_file.exists():
        with open(bot_config_file, 'r') as f:
            config = yaml.safe_load(f) or {}
        
        if 'telegram' not in config:
            config['telegram'] = {}
        
        config['telegram']['enabled'] = True
        
        with open(bot_config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
    
    print("\n✅ Telegram configuration saved!")
    print(f"   Secrets file: {secrets_file}")
    print("\n🧪 Test your bot:")
    print(f"   python -m trading_core.config_cli test-telegram")


def cmd_test_telegram(args):
    """텔레그램 연결 테스트"""
    import asyncio
    from trading_monitor import TelegramNotifier
    
    print("🧪 Testing Telegram connection...\n")
    
    try:
        mgr = get_config_manager(args.config_dir)
        config = mgr.load_config()
        
        if not config.telegram.enabled:
            print("❌ Telegram is not enabled in config")
            sys.exit(1)
        
        if not config.telegram.bot_token or not config.telegram.chat_id:
            print("❌ Bot token or chat ID not set")
            sys.exit(1)
        
        async def test():
            notifier = TelegramNotifier(
                bot_token=config.telegram.bot_token,
                chat_id=config.telegram.chat_id
            )
            await notifier.initialize()
            await notifier.send_message(
                "🧪 <b>Test Message</b>\n\n"
                "Your Trading Bot is configured correctly!\n"
                "✅ Connection successful"
            )
            print("✅ Test message sent successfully!")
        
        asyncio.run(test())
        
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def cmd_show(args):
    """현재 설정 표시 (민감 정보 마스킹)"""
    try:
        mgr = get_config_manager(args.config_dir)
        config = mgr.load_config()
        
        print(f"\n🤖 {config.name} v{config.version}")
        print("="*60)
        print(f"ID: {config.bot_id}")
        print(f"Mode: {config.mode.upper()}")
        print(f"Timezone: {config.timezone}")
        print()
        print("📊 Exchange:")
        print(f"   Provider: {config.exchange.id}")
        print(f"   Sandbox: {config.exchange.sandbox}")
        print(f"   Testnet: {config.exchange.testnet}")
        print(f"   API Key: {'✅ Set' if config.exchange.api_key else '❌ Not set'}")
        print()
        print("📱 Telegram:")
        print(f"   Enabled: {config.telegram.enabled}")
        if config.telegram.enabled:
            print(f"   Bot Token: {'✅ Set' if config.telegram.bot_token else '❌ Not set'}")
            print(f"   Chat ID: {config.telegram.chat_id or '❌ Not set'}")
        print()
        print("⚠️  Risk Management:")
        print(f"   Max Daily Loss: {config.risk.max_daily_loss_pct}%")
        print(f"   Max Positions: {config.risk.max_positions}")
        print(f"   Position Size: {config.risk.max_position_size_pct}%")
        print()
        print("📈 Active Strategies:")
        for strategy in config.strategies:
            if strategy.enabled:
                print(f"   ✅ {strategy.name} ({strategy.type}) - {strategy.symbol}")
        
        print()
        if mgr.is_production_ready():
            print("🚀 Status: Production Ready")
        else:
            print("⚠️  Status: Development Mode")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Trading Bot Configuration Manager',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initialize configuration
  python -m trading_core.config_cli init
  
  # Validate configuration
  python -m trading_core.config_cli validate
  
  # Setup Telegram
  python -m trading_core.config_cli telegram-setup
  
  # Test Telegram
  python -m trading_core.config_cli test-telegram
  
  # Show current configuration
  python -m trading_core.config_cli show
        """
    )
    
    parser.add_argument(
        '--config-dir',
        default='config',
        help='Configuration directory (default: config)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # init
    init_parser = subparsers.add_parser('init', help='Initialize configuration files')
    
    # validate
    validate_parser = subparsers.add_parser('validate', help='Validate configuration')
    
    # telegram-setup
    telegram_parser = subparsers.add_parser('telegram-setup', help='Setup Telegram bot')
    
    # test-telegram
    test_parser = subparsers.add_parser('test-telegram', help='Test Telegram connection')
    
    # show
    show_parser = subparsers.add_parser('show', help='Show current configuration')
    
    args = parser.parse_args()
    
    if args.command == 'init':
        cmd_init(args)
    elif args.command == 'validate':
        cmd_validate(args)
    elif args.command == 'telegram-setup':
        cmd_telegram_setup(args)
    elif args.command == 'test-telegram':
        cmd_test_telegram(args)
    elif args.command == 'show':
        cmd_show(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
