"""Historical data loader - 히스토리컬 데이터 로더"""

import pandas as pd
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """백테스트용 히스토리컬 데이터 로더"""
    
    def __init__(self, data_dir: str = "data/historical"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    def load_from_csv(
        self,
        symbol: str,
        timeframe: str = "1h",
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        CSV 파일에서 데이터 로드
        
        Expected CSV format:
        timestamp,open,high,low,close,volume
        """
        filepath = self.data_dir / f"{symbol.replace('/', '_')}_{timeframe}.csv"
        
        if not filepath.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")
        
        df = pd.read_csv(filepath)
        
        # timestamp 컬럼 처리
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # 날짜 필터링
        if start_date:
            df = df[df['timestamp'] >= start_date]
        if end_date:
            df = df[df['timestamp'] <= end_date]
        
        logger.info(f"Loaded {len(df)} rows from {filepath}")
        return df
    
    def save_to_csv(self, df: pd.DataFrame, symbol: str, timeframe: str) -> None:
        """데이터를 CSV로 저장"""
        filepath = self.data_dir / f"{symbol.replace('/', '_')}_{timeframe}.csv"
        df.to_csv(filepath, index=False)
        logger.info(f"Saved {len(df)} rows to {filepath}")
    
    def fetch_from_exchange(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365
    ) -> pd.DataFrame:
        """
        거래소에서 데이터 수집 (CCXT 사용)
        
        Args:
            exchange_id: 'binance', 'bybit', etc.
            symbol: 'BTC/USDT'
            timeframe: '1h', '4h', '1d'
            days: 수집할 일수
        """
        import ccxt
        
        exchange_class = getattr(ccxt, exchange_id)
        exchange = exchange_class({'enableRateLimit': True})
        
        # 시간프레임을 밀리초로 변환
        timeframe_ms = self._timeframe_to_ms(timeframe)
        since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        
        all_ohlcv = []
        while since < int(datetime.utcnow().timestamp() * 1000):
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since, limit=1000)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                since = ohlcv[-1][0] + timeframe_ms
                logger.debug(f"Fetched {len(ohlcv)} candles, since: {since}")
            except Exception as e:
                logger.error(f"Error fetching data: {e}")
                break
        
        # DataFrame 변환
        df = pd.DataFrame(
            all_ohlcv,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
        )
        
        # 중복 제거 및 정렬
        df = df.drop_duplicates(subset=['timestamp'])
        df = df.sort_values('timestamp')
        
        logger.info(f"Fetched {len(df)} candles from {exchange_id}")
        return df
    
    def _timeframe_to_ms(self, timeframe: str) -> int:
        """시간프레임을 밀리초로 변환"""
        mapping = {
            '1m': 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '30m': 30 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '2h': 2 * 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '6h': 6 * 60 * 60 * 1000,
            '8h': 8 * 60 * 60 * 1000,
            '12h': 12 * 60 * 60 * 1000,
            '1d': 24 * 60 * 60 * 1000,
            '3d': 3 * 24 * 60 * 60 * 1000,
            '1w': 7 * 24 * 60 * 60 * 1000,
        }
        return mapping.get(timeframe, 60 * 60 * 1000)  # 기본 1h
    
    def prepare_data(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 365,
        exchange_id: str = "binance"
    ) -> pd.DataFrame:
        """
        데이터 준비 (로컬 있으면 로드, 없으면 거래소에서 수집)
        """
        try:
            # 로컬 데이터 먼저 시도
            return self.load_from_csv(symbol, timeframe)
        except FileNotFoundError:
            logger.info(f"Local data not found, fetching from {exchange_id}...")
            df = self.fetch_from_exchange(exchange_id, symbol, timeframe, days)
            self.save_to_csv(df, symbol, timeframe)
            return df
