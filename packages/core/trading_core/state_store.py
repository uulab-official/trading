"""State persistence - 상태 영속성 (재시작 복구)"""

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class StateStore(ABC):
    """상태 저장소 인터페이스"""
    
    @abstractmethod
    def save(self, key: str, data: Dict[str, Any]) -> None:
        """상태 저장"""
        pass
    
    @abstractmethod
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """상태 로드"""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> None:
        """상태 삭제"""
        pass
    
    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """키 목록 조회"""
        pass


class SQLiteStateStore(StateStore):
    """SQLite 기반 상태 저장"""
    
    def __init__(self, db_path: str = "data/state.db", *, initialize: bool = True):
        self.db_path = Path(db_path)
        # A new empty file is not interchangeable with an unreadable prior state.
        self.existed_at_open = self.db_path.exists()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The strict legacy paper loader defers creation until writer ownership
        # and first-start provenance have been established.
        if initialize:
            self._init_db()
        
    def _init_db(self) -> None:
        """데이터베이스 초기화"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def save(self, key: str, data: Dict[str, Any]) -> None:
        """상태 저장"""
        try:
            json_data = json.dumps(data, default=str)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO state (key, data, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        data = excluded.data,
                        updated_at = excluded.updated_at
                    """,
                    (key, json_data, datetime.utcnow().isoformat())
                )
                conn.commit()
            logger.debug(f"State saved: {key}")
        except Exception as e:
            logger.error(f"Failed to save state {key}: {e}")
            raise
    
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """상태 로드"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT data FROM state WHERE key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                
                if row:
                    return json.loads(row[0])
                return None
        except Exception as e:
            logger.error(f"Failed to load state {key}: {e}")
            return None
    
    def delete(self, key: str) -> None:
        """상태 삭제"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM state WHERE key = ?", (key,))
                conn.commit()
            logger.debug(f"State deleted: {key}")
        except Exception as e:
            logger.error(f"Failed to delete state {key}: {e}")
    
    def list_keys(self, prefix: str = "") -> list[str]:
        """키 목록 조회"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT key FROM state WHERE key LIKE ?",
                    (f"{prefix}%",)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to list keys: {e}")
            return []
    
    def get_last_updated(self, key: str) -> Optional[datetime]:
        """마지막 업데이트 시간 조회"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT updated_at FROM state WHERE key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                
                if row:
                    return datetime.fromisoformat(row[0])
                return None
        except Exception as e:
            logger.error(f"Failed to get updated time: {e}")
            return None


class JSONStateStore(StateStore):
    """JSON 파일 기반 상태 저장 (간단한 용도)"""
    
    def __init__(self, base_path: str = "data/state"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        
    def _get_file_path(self, key: str) -> Path:
        """키로부터 파일 경로 생성"""
        # 안전한 파일명으로 변환
        safe_key = key.replace("/", "_").replace("\\", "_")
        return self.base_path / f"{safe_key}.json"
    
    def save(self, key: str, data: Dict[str, Any]) -> None:
        """JSON 파일로 저장"""
        try:
            file_path = self._get_file_path(key)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2, default=str)
                
            logger.debug(f"State saved to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save state {key}: {e}")
            raise
    
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """JSON 파일에서 로드"""
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                return None
            
            with open(file_path, 'r') as f:
                return json.load(f)
                
        except Exception as e:
            logger.error(f"Failed to load state {key}: {e}")
            return None
    
    def delete(self, key: str) -> None:
        """파일 삭제"""
        try:
            file_path = self._get_file_path(key)
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"State file deleted: {file_path}")
        except Exception as e:
            logger.error(f"Failed to delete state {key}: {e}")
    
    def list_keys(self, prefix: str = "") -> list[str]:
        """파일 목록 조회"""
        try:
            keys = []
            for file_path in self.base_path.glob("*.json"):
                key = file_path.stem.replace("_", "/")
                if key.startswith(prefix):
                    keys.append(key)
            return keys
        except Exception as e:
            logger.error(f"Failed to list keys: {e}")
            return []


class BotState:
    """봇 상태 관리 헬퍼"""
    
    def __init__(self, store: StateStore, bot_id: str = "main"):
        self.store = store
        self.bot_id = bot_id
        
    def _make_key(self, name: str) -> str:
        """키 생성"""
        return f"bot:{self.bot_id}:{name}"
    
    def save_portfolio(self, portfolio_data: Dict) -> None:
        """포트폴리오 저장"""
        self.store.save(self._make_key("portfolio"), portfolio_data)
    
    def load_portfolio(self) -> Optional[Dict]:
        """포트폴리오 로드"""
        return self.store.load(self._make_key("portfolio"))
    
    def save_orders(self, orders_data: Dict) -> None:
        """주문 상태 저장"""
        self.store.save(self._make_key("orders"), orders_data)
    
    def load_orders(self) -> Optional[Dict]:
        """주문 상태 로드"""
        return self.store.load(self._make_key("orders"))
    
    def save_risk_state(self, risk_data: Dict) -> None:
        """리스크 상태 저장"""
        self.store.save(self._make_key("risk"), risk_data)
    
    def load_risk_state(self) -> Optional[Dict]:
        """리스크 상태 로드"""
        return self.store.load(self._make_key("risk"))
    
    def save_config(self, config: Dict) -> None:
        """설정 저장"""
        self.store.save(self._make_key("config"), config)
    
    def load_config(self) -> Optional[Dict]:
        """설정 로드"""
        return self.store.load(self._make_key("config"))
    
    def save_checkpoint(self, checkpoint: Dict) -> None:
        """체크포인트 저장 (모든 상태)"""
        checkpoint['timestamp'] = datetime.utcnow().isoformat()
        self.store.save(self._make_key("checkpoint"), checkpoint)
    
    def load_checkpoint(self) -> Optional[Dict]:
        """체크포인트 로드"""
        return self.store.load(self._make_key("checkpoint"))
