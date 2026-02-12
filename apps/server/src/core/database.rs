use anyhow::Result;
use chrono::{DateTime, Utc};
use sqlx::{migrate::MigrateDatabase, sqlite::SqlitePool, Row};
use tracing::{error, info};

use crate::core::models::{Order, OrderSide, OrderStatus, OrderType, Position, Trade};

pub struct Database {
    pool: SqlitePool,
}

impl Database {
    pub async fn new(db_url: &str) -> Result<Self> {
        if !sqlx::Sqlite::database_exists(db_url).await.unwrap_or(false) {
            sqlx::Sqlite::create_database(db_url).await?;
            info!("Created new database at {}", db_url);
        }

        let pool = SqlitePool::connect(db_url).await?;
        let db = Self { pool };
        db.init_tables().await?;
        
        info!("Database connected and initialized");
        Ok(db)
    }

    async fn init_tables(&self) -> Result<()> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                exchange_order_id TEXT,
                filled_quantity REAL DEFAULT 0,
                average_price REAL,
                commission REAL,
                commission_asset TEXT
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                opened_at TEXT NOT NULL,
                leverage REAL DEFAULT 1
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                timestamp TEXT NOT NULL,
                commission REAL NOT NULL,
                commission_asset TEXT NOT NULL
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, timestamp)
            )
            "#,
        )
        .execute(&self.pool)
        .await?;

        info!("Database tables initialized");
        Ok(())
    }

    pub async fn save_order(&self, order: &Order) -> Result<()> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO orders 
            (id, symbol, side, order_type, quantity, price, status, created_at, updated_at, 
             exchange_order_id, filled_quantity, average_price, commission, commission_asset)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&order.id)
        .bind(&order.symbol)
        .bind(format!("{:?}", order.side))
        .bind(format!("{:?}", order.order_type))
        .bind(order.quantity)
        .bind(order.price)
        .bind(format!("{:?}", order.status))
        .bind(order.created_at.to_rfc3339())
        .bind(order.updated_at.to_rfc3339())
        .bind(&order.exchange_order_id)
        .bind(order.filled_quantity)
        .bind(order.average_price)
        .bind(order.commission)
        .bind(&order.commission_asset)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    pub async fn get_order(&self, order_id: &str) -> Result<Option<Order>> {
        let row = sqlx::query(
            r#"
            SELECT id, symbol, side, order_type, quantity, price, status, 
                   created_at, updated_at, exchange_order_id, filled_quantity, 
                   average_price, commission, commission_asset
            FROM orders WHERE id = ?
            "#,
        )
        .bind(order_id)
        .fetch_optional(&self.pool)
        .await?;

        match row {
            Some(row) => Ok(Some(self.row_to_order(&row)?)),
            None => Ok(None),
        }
    }

    pub async fn get_open_orders(&self) -> Result<Vec<Order>> {
        let rows = sqlx::query(
            r#"
            SELECT id, symbol, side, order_type, quantity, price, status, 
                   created_at, updated_at, exchange_order_id, filled_quantity, 
                   average_price, commission, commission_asset
            FROM orders WHERE status IN ('Open', 'PartiallyFilled', 'Pending')
            ORDER BY created_at DESC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter()
            .map(|row| self.row_to_order(&row))
            .collect::<Result<Vec<_>>>()
    }

    pub async fn save_position(&self, position: &Position) -> Result<()> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO positions 
            (symbol, side, quantity, entry_price, current_price, unrealized_pnl, 
             realized_pnl, opened_at, leverage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&position.symbol)
        .bind(format!("{:?}", position.side))
        .bind(position.quantity)
        .bind(position.entry_price)
        .bind(position.current_price)
        .bind(position.unrealized_pnl)
        .bind(position.realized_pnl)
        .bind(position.opened_at.to_rfc3339())
        .bind(position.leverage)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    pub async fn get_position(&self, symbol: &str) -> Result<Option<Position>> {
        let row = sqlx::query(
            r#"
            SELECT symbol, side, quantity, entry_price, current_price, 
                   unrealized_pnl, realized_pnl, opened_at, leverage
            FROM positions WHERE symbol = ?
            "#,
        )
        .bind(symbol)
        .fetch_optional(&self.pool)
        .await?;

        match row {
            Some(row) => Ok(Some(self.row_to_position(&row)?)),
            None => Ok(None),
        }
    }

    pub async fn get_all_positions(&self) -> Result<Vec<Position>> {
        let rows = sqlx::query(
            r#"
            SELECT symbol, side, quantity, entry_price, current_price, 
                   unrealized_pnl, realized_pnl, opened_at, leverage
            FROM positions
            "#,
        )
        .fetch_all(&self.pool)
        .await?;

        rows.into_iter()
            .map(|row| self.row_to_position(&row))
            .collect::<Result<Vec<_>>>()
    }

    pub async fn delete_position(&self, symbol: &str) -> Result<()> {
        sqlx::query("DELETE FROM positions WHERE symbol = ?")
            .bind(symbol)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn save_trade(&self, trade: &Trade) -> Result<()> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO trades 
            (id, order_id, symbol, side, quantity, price, timestamp, commission, commission_asset)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&trade.id)
        .bind(&trade.order_id)
        .bind(&trade.symbol)
        .bind(format!("{:?}", trade.side))
        .bind(trade.quantity)
        .bind(trade.price)
        .bind(trade.timestamp.to_rfc3339())
        .bind(trade.commission)
        .bind(&trade.commission_asset)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    pub async fn save_candle(&self, candle: &crate::core::models::Candle) -> Result<()> {
        sqlx::query(
            r#"
            INSERT OR REPLACE INTO candles 
            (symbol, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            "#,
        )
        .bind(&candle.symbol)
        .bind(candle.timestamp.to_rfc3339())
        .bind(candle.open)
        .bind(candle.high)
        .bind(candle.low)
        .bind(candle.close)
        .bind(candle.volume)
        .execute(&self.pool)
        .await?;

        Ok(())
    }

    fn row_to_order(&self, row: &sqlx::sqlite::SqliteRow) -> Result<Order> {
        let side_str: String = row.try_get("side")?;
        let order_type_str: String = row.try_get("order_type")?;
        let status_str: String = row.try_get("status")?;

        Ok(Order {
            id: row.try_get("id")?,
            symbol: row.try_get("symbol")?,
            side: parse_order_side(&side_str),
            order_type: parse_order_type(&order_type_str),
            quantity: row.try_get("quantity")?,
            price: row.try_get("price")?,
            status: parse_order_status(&status_str),
            created_at: row.try_get::<String, _>("created_at")?.parse().unwrap_or_else(|_| Utc::now()),
            updated_at: row.try_get::<String, _>("updated_at")?.parse().unwrap_or_else(|_| Utc::now()),
            exchange_order_id: row.try_get("exchange_order_id")?,
            filled_quantity: row.try_get("filled_quantity")?,
            average_price: row.try_get("average_price")?,
            commission: row.try_get("commission")?,
            commission_asset: row.try_get("commission_asset")?,
        })
    }

    fn row_to_position(&self, row: &sqlx::sqlite::SqliteRow) -> Result<Position> {
        let side_str: String = row.try_get("side")?;

        Ok(Position {
            symbol: row.try_get("symbol")?,
            side: parse_order_side(&side_str),
            quantity: row.try_get("quantity")?,
            entry_price: row.try_get("entry_price")?,
            current_price: row.try_get("current_price")?,
            unrealized_pnl: row.try_get("unrealized_pnl")?,
            realized_pnl: row.try_get("realized_pnl")?,
            opened_at: row.try_get::<String, _>("opened_at")?.parse().unwrap_or_else(|_| Utc::now()),
            leverage: row.try_get("leverage")?,
        })
    }
}

fn parse_order_side(s: &str) -> OrderSide {
    match s {
        "Buy" => OrderSide::Buy,
        _ => OrderSide::Sell,
    }
}

fn parse_order_type(s: &str) -> OrderType {
    match s {
        "Market" => OrderType::Market,
        "Limit" => OrderType::Limit,
        "StopLoss" => OrderType::StopLoss,
        _ => OrderType::TakeProfit,
    }
}

fn parse_order_status(s: &str) -> OrderStatus {
    match s {
        "Pending" => OrderStatus::Pending,
        "Open" => OrderStatus::Open,
        "PartiallyFilled" => OrderStatus::PartiallyFilled,
        "Filled" => OrderStatus::Filled,
        "Canceled" => OrderStatus::Canceled,
        _ => OrderStatus::Failed,
    }
}

pub async fn run_migrations() -> anyhow::Result<()> {
    let db = Database::new("sqlite://data/trading.db").await?;
    info!("Database migrations completed");
    Ok(())
}