use chrono::Utc;
use sqlx::SqlitePool;
use std::path::PathBuf;
use trading_server::core::database::Database;
use trading_server::core::models::{Order, OrderSide, OrderStatus, OrderType, Position};
use uuid::Uuid;

struct OwnedDirectory(PathBuf);

impl OwnedDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("trading-db-boundary-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn url(&self) -> String {
        format!("sqlite://{}", self.0.join("orders.db").display())
    }
}

impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn pending_order() -> Order {
    let now = Utc::now();
    Order {
        id: "synthetic-db-order".into(),
        symbol: "BTC/USDT".into(),
        side: OrderSide::Buy,
        order_type: OrderType::Limit,
        quantity: 10.0,
        price: Some(100.0),
        status: OrderStatus::Pending,
        created_at: now,
        updated_at: now,
        exchange_order_id: None,
        filled_quantity: 0.0,
        average_price: None,
        commission: None,
        commission_asset: None,
    }
}

#[tokio::test]
async fn null_filled_quantity_is_unknown_and_never_restored_as_observed_zero() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    let now = Utc::now().to_rfc3339();
    // A genuinely omitted column still uses the existing DEFAULT 0 contract.
    sqlx::query(
        "INSERT INTO orders (id, symbol, side, order_type, quantity, price, status, created_at, updated_at) \
         VALUES ('null-fill', 'BTC/USDT', 'Buy', 'Limit', 10, 100, 'Pending', ?, ?)",
    )
    .bind(&now)
    .bind(&now)
    .execute(&connection)
    .await
    .unwrap();
    let before = database.get_order("null-fill").await.unwrap().unwrap();
    assert_eq!(before.filled_quantity, 0.0);

    sqlx::query("UPDATE orders SET filled_quantity = NULL WHERE id = 'null-fill'")
        .execute(&connection)
        .await
        .unwrap();
    assert!(database.get_order("null-fill").await.is_err());
    assert!(database.get_open_orders().await.is_err());
    let stored: Option<f64> =
        sqlx::query_scalar("SELECT filled_quantity FROM orders WHERE id = 'null-fill'")
            .fetch_one(&connection)
            .await
            .unwrap();
    assert!(stored.is_none());
    connection.close().await;
}

#[tokio::test]
async fn malformed_order_discriminants_are_refused_without_rewriting_storage() {
    for column in ["side", "order_type", "status"] {
        let directory = OwnedDirectory::new();
        let database = Database::new(&directory.url()).await.unwrap();
        let connection = SqlitePool::connect(&directory.url()).await.unwrap();
        database.save_order(&pending_order()).await.unwrap();
        let sql = format!("UPDATE orders SET {column} = 'unrecognized-value'");
        sqlx::query(&sql).execute(&connection).await.unwrap();

        assert!(
            database.get_order("synthetic-db-order").await.is_err(),
            "{column}"
        );
        let sql = format!("SELECT {column} FROM orders");
        let stored: String = sqlx::query_scalar(&sql)
            .fetch_one(&connection)
            .await
            .unwrap();
        assert_eq!(stored, "unrecognized-value");
        connection.close().await;
    }
}

#[tokio::test]
async fn unknown_status_cannot_disappear_from_startup_order_loading() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    database.save_order(&pending_order()).await.unwrap();
    sqlx::query("UPDATE orders SET status = 'unrecognized-status'")
        .execute(&connection)
        .await
        .unwrap();
    assert!(database.get_open_orders().await.is_err());
    connection.close().await;
}

#[tokio::test]
async fn invalid_order_timestamps_are_not_replaced_by_the_current_time() {
    for column in ["created_at", "updated_at"] {
        let directory = OwnedDirectory::new();
        let database = Database::new(&directory.url()).await.unwrap();
        let connection = SqlitePool::connect(&directory.url()).await.unwrap();
        database.save_order(&pending_order()).await.unwrap();
        let sql = format!("UPDATE orders SET {column} = 'not-a-timestamp'");
        sqlx::query(&sql).execute(&connection).await.unwrap();
        assert!(
            database.get_order("synthetic-db-order").await.is_err(),
            "{column}"
        );
        assert!(database.get_open_orders().await.is_err(), "{column}");
        connection.close().await;
    }
}

#[tokio::test]
async fn valid_discriminants_roundtrip_and_only_unresolved_orders_load() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let mut expected_unresolved = Vec::new();
    for side in [OrderSide::Buy, OrderSide::Sell] {
        for order_type in [
            OrderType::Market,
            OrderType::Limit,
            OrderType::StopLoss,
            OrderType::TakeProfit,
        ] {
            for status in [
                OrderStatus::Pending,
                OrderStatus::Open,
                OrderStatus::PartiallyFilled,
                OrderStatus::Filled,
                OrderStatus::Canceled,
                OrderStatus::Failed,
            ] {
                let mut order = pending_order();
                order.id = format!("{side:?}-{order_type:?}-{status:?}");
                order.side = side.clone();
                order.order_type = order_type.clone();
                order.status = status;
                if matches!(
                    order.status,
                    OrderStatus::PartiallyFilled | OrderStatus::Filled
                ) {
                    order.filled_quantity = if order.status == OrderStatus::Filled {
                        10.0
                    } else {
                        3.0
                    };
                    order.average_price = Some(100.0);
                }
                database.save_order(&order).await.unwrap();
                let actual = database.get_order(&order.id).await.unwrap().unwrap();
                assert_eq!(
                    serde_json::to_value(actual).unwrap(),
                    serde_json::to_value(&order).unwrap()
                );
                if matches!(
                    order.status,
                    OrderStatus::Pending | OrderStatus::Open | OrderStatus::PartiallyFilled
                ) {
                    expected_unresolved.push(order.id);
                }
            }
        }
    }
    let mut actual: Vec<_> = database
        .get_open_orders()
        .await
        .unwrap()
        .into_iter()
        .map(|order| order.id)
        .collect();
    actual.sort();
    expected_unresolved.sort();
    assert_eq!(actual, expected_unresolved);
    assert_eq!(actual.len(), 24);
}

#[tokio::test]
async fn malformed_position_side_or_time_is_refused_during_startup_loading() {
    for column in ["side", "opened_at"] {
        let directory = OwnedDirectory::new();
        let database = Database::new(&directory.url()).await.unwrap();
        let connection = SqlitePool::connect(&directory.url()).await.unwrap();
        let position = Position {
            symbol: "BTC/USDT".into(),
            side: OrderSide::Buy,
            quantity: 1.0,
            entry_price: 100.0,
            current_price: 100.0,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            opened_at: Utc::now(),
            leverage: 1.0,
        };
        database.save_position(&position).await.unwrap();
        assert_eq!(database.get_all_positions().await.unwrap().len(), 1);
        let sql = format!("UPDATE positions SET {column} = 'invalid'");
        sqlx::query(&sql).execute(&connection).await.unwrap();
        assert!(database.get_all_positions().await.is_err(), "{column}");
        assert!(database.get_position("BTC/USDT").await.is_err(), "{column}");
        connection.close().await;
    }
}
