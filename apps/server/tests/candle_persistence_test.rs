use chrono::{DateTime, Utc};
use sqlx::{migrate::MigrateDatabase, SqlitePool};
use std::path::PathBuf;
use trading_server::core::database::Database;
use trading_server::core::models::Candle;
use uuid::Uuid;

struct OwnedDirectory(PathBuf);

impl OwnedDirectory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("trading-candles-{}", Uuid::new_v4()));
        std::fs::create_dir(&path).unwrap();
        Self(path)
    }

    fn url(&self) -> String {
        format!("sqlite://{}", self.0.join("candles.db").display())
    }
}

impl Drop for OwnedDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn candle(timeframe: &str) -> Candle {
    Candle {
        symbol: "BTCUSDT".into(),
        timeframe: timeframe.into(),
        timestamp: DateTime::parse_from_rfc3339("2024-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc),
        open: 100.0,
        high: 110.0,
        low: 90.0,
        close: 105.0,
        volume: 12.0,
    }
}

type StoredCandle = (String, String, String, f64, f64, f64, f64, f64);

async fn stored_candles(connection: &SqlitePool) -> Vec<StoredCandle> {
    sqlx::query_as(
        "SELECT symbol, timeframe, timestamp, open, high, low, close, volume \
         FROM closed_candles ORDER BY symbol, timeframe, timestamp",
    )
    .fetch_all(connection)
    .await
    .unwrap()
}

fn expected_row(timeframe: &str) -> StoredCandle {
    (
        "BTCUSDT".into(),
        timeframe.into(),
        "2024-01-01T00:00:00+00:00".into(),
        100.0,
        110.0,
        90.0,
        105.0,
        12.0,
    )
}

#[tokio::test]
async fn same_symbol_and_open_time_keep_distinct_intervals() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();

    database.save_candle(&candle("1m")).await.unwrap();
    database.save_candle(&candle("5m")).await.unwrap();

    assert_eq!(
        stored_candles(&connection).await,
        vec![expected_row("1m"), expected_row("5m")]
    );
    let legacy_count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM candles")
        .fetch_one(&connection)
        .await
        .unwrap();
    assert_eq!(legacy_count, 0);
    connection.close().await;
}

#[tokio::test]
async fn one_interval_preserves_distinct_symbols_and_open_times() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    database.save_candle(&candle("1m")).await.unwrap();

    let mut another_symbol = candle("1m");
    another_symbol.symbol = "ETHUSDT".into();
    database.save_candle(&another_symbol).await.unwrap();
    let mut next_open = candle("1m");
    next_open.timestamp = DateTime::parse_from_rfc3339("2024-01-01T00:01:00+00:00")
        .unwrap()
        .with_timezone(&Utc);
    database.save_candle(&next_open).await.unwrap();

    let identities: Vec<(String, String, String)> = sqlx::query_as(
        "SELECT symbol, timeframe, timestamp FROM closed_candles ORDER BY symbol, timestamp",
    )
    .fetch_all(&connection)
    .await
    .unwrap();
    assert_eq!(
        identities,
        vec![
            (
                "BTCUSDT".into(),
                "1m".into(),
                "2024-01-01T00:00:00+00:00".into(),
            ),
            (
                "BTCUSDT".into(),
                "1m".into(),
                "2024-01-01T00:01:00+00:00".into(),
            ),
            (
                "ETHUSDT".into(),
                "1m".into(),
                "2024-01-01T00:00:00+00:00".into(),
            ),
        ]
    );
    connection.close().await;
}

#[tokio::test]
async fn identical_replay_performs_no_insert_update_or_delete() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    let original = candle("1m");
    database.save_candle(&original).await.unwrap();

    // Detect replacements and updates as well as duplicate rows. Replays must
    // not become writes that could trigger downstream processing again.
    sqlx::query("CREATE TABLE candle_mutations (operation TEXT NOT NULL)")
        .execute(&connection)
        .await
        .unwrap();
    for operation in ["INSERT", "UPDATE", "DELETE"] {
        let sql = format!(
            "CREATE TRIGGER observe_candle_{operation} AFTER {operation} ON closed_candles \
             BEGIN INSERT INTO candle_mutations (operation) VALUES ('{operation}'); END"
        );
        sqlx::query(&sql).execute(&connection).await.unwrap();
    }

    database.save_candle(&original).await.unwrap();
    database.save_candle(&original).await.unwrap();

    assert_eq!(stored_candles(&connection).await, vec![expected_row("1m")]);
    let mutations: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM candle_mutations")
        .fetch_one(&connection)
        .await
        .unwrap();
    assert_eq!(mutations, 0);
    connection.close().await;
}

#[tokio::test]
async fn conflicting_replays_are_refused_without_changing_the_first_observation() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    database.save_candle(&candle("1m")).await.unwrap();

    let mutations: [fn(&mut Candle); 5] = [
        |value| value.open = 101.0,
        |value| value.high = 111.0,
        |value| value.low = 89.0,
        |value| value.close = 106.0,
        |value| value.volume = 13.0,
    ];
    let mut first_error = None;
    for mutate in mutations {
        let mut conflicting = candle("1m");
        mutate(&mut conflicting);
        let error = database.save_candle(&conflicting).await.unwrap_err();
        let message = error.to_string();
        if let Some(expected) = &first_error {
            assert_eq!(&message, expected, "conflicts must use one fixed error");
        } else {
            first_error = Some(message);
        }
        assert_eq!(stored_candles(&connection).await, vec![expected_row("1m")]);
    }
    connection.close().await;
}

#[tokio::test]
async fn malformed_closed_candles_never_create_a_row() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    let mutations: [(&str, fn(&mut Candle)); 24] = [
        ("missing symbol", |value| value.symbol.clear()),
        ("lowercase symbol", |value| value.symbol = "btcusdt".into()),
        ("slash symbol", |value| value.symbol = "BTC/USDT".into()),
        ("whitespace symbol", |value| {
            value.symbol = " BTCUSDT".into()
        }),
        ("non-ASCII symbol", |value| value.symbol = "ＢTCUSDT".into()),
        ("punctuated symbol", |value| {
            value.symbol = "BTC-USDT".into()
        }),
        ("missing interval", |value| value.timeframe.clear()),
        ("unsupported interval", |value| {
            value.timeframe = "2m".into()
        }),
        ("incorrect interval case", |value| {
            value.timeframe = "1H".into()
        }),
        ("negative timestamp", |value| {
            value.timestamp = DateTime::from_timestamp_millis(-1).unwrap();
        }),
        ("zero open", |value| value.open = 0.0),
        ("negative high", |value| value.high = -1.0),
        ("zero low", |value| value.low = 0.0),
        ("negative close", |value| value.close = -1.0),
        ("NaN open", |value| value.open = f64::NAN),
        ("infinite high", |value| value.high = f64::INFINITY),
        ("negative infinite low", |value| {
            value.low = f64::NEG_INFINITY
        }),
        ("NaN close", |value| value.close = f64::NAN),
        ("high below close", |value| value.high = 104.0),
        ("low above open", |value| value.low = 101.0),
        ("inverted range", |value| {
            value.low = 111.0;
            value.high = 89.0;
        }),
        ("negative volume", |value| value.volume = -1.0),
        ("NaN volume", |value| value.volume = f64::NAN),
        ("infinite volume", |value| value.volume = f64::INFINITY),
    ];
    for (name, mutate) in mutations {
        let mut invalid = candle("1m");
        mutate(&mut invalid);
        assert!(database.save_candle(&invalid).await.is_err(), "{name}");
        assert!(stored_candles(&connection).await.is_empty(), "{name}");
    }
    let legacy_count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM candles")
        .fetch_one(&connection)
        .await
        .unwrap();
    assert_eq!(legacy_count, 0);
    connection.close().await;
}

#[tokio::test]
async fn all_supported_intervals_and_zero_volume_are_storable() {
    let directory = OwnedDirectory::new();
    let database = Database::new(&directory.url()).await.unwrap();
    let connection = SqlitePool::connect(&directory.url()).await.unwrap();
    for timeframe in [
        "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d",
        "1w", "1M",
    ] {
        let mut valid = candle(timeframe);
        valid.volume = 0.0;
        database.save_candle(&valid).await.unwrap();
    }
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM closed_candles WHERE volume = 0")
        .fetch_one(&connection)
        .await
        .unwrap();
    assert_eq!(count, 16);
    connection.close().await;
}

#[tokio::test]
async fn reopening_preserves_legacy_candles_without_guessing_an_interval() {
    let directory = OwnedDirectory::new();
    let url = directory.url();
    sqlx::Sqlite::create_database(&url).await.unwrap();
    let connection = SqlitePool::connect(&url).await.unwrap();
    // This is the schema that predates interval-qualified storage.
    sqlx::query(
        "CREATE TABLE candles (
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (symbol, timestamp)
        )",
    )
    .execute(&connection)
    .await
    .unwrap();
    sqlx::query(
        "INSERT INTO candles (symbol, timestamp, open, high, low, close, volume) \
         VALUES ('BTCUSDT', '2024-01-01T00:00:00+00:00', 200, 210, 190, 205, 24)",
    )
    .execute(&connection)
    .await
    .unwrap();
    let original_schema: String =
        sqlx::query_scalar("SELECT sql FROM sqlite_master WHERE name = 'candles'")
            .fetch_one(&connection)
            .await
            .unwrap();

    let database = Database::new(&url).await.unwrap();
    assert!(stored_candles(&connection).await.is_empty());
    database.save_candle(&candle("1m")).await.unwrap();
    drop(database);
    let reopened = Database::new(&url).await.unwrap();
    reopened.save_candle(&candle("1m")).await.unwrap();
    reopened.save_candle(&candle("5m")).await.unwrap();

    let legacy_rows: Vec<(String, String, f64, f64, f64, f64, f64)> =
        sqlx::query_as("SELECT symbol, timestamp, open, high, low, close, volume FROM candles")
            .fetch_all(&connection)
            .await
            .unwrap();
    assert_eq!(
        legacy_rows,
        vec![(
            "BTCUSDT".into(),
            "2024-01-01T00:00:00+00:00".into(),
            200.0,
            210.0,
            190.0,
            205.0,
            24.0,
        )]
    );
    let current_schema: String =
        sqlx::query_scalar("SELECT sql FROM sqlite_master WHERE name = 'candles'")
            .fetch_one(&connection)
            .await
            .unwrap();
    assert_eq!(current_schema, original_schema);
    assert_eq!(
        stored_candles(&connection).await,
        vec![expected_row("1m"), expected_row("5m")]
    );
    connection.close().await;
}
