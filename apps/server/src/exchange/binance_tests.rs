use super::*;
use tokio::net::TcpListener;
use tokio::sync::broadcast::error::TryRecvError;
use tokio::time::{timeout, Duration};
use tokio_tungstenite::{
    accept_async, accept_hdr_async,
    tungstenite::handshake::server::{Request, Response},
};

const EVENT_TIME: i64 = 1_700_000_100_000;
const OPEN_TIME: i64 = 1_700_000_040_000;
const SECRET: &str = "synthetic-secret-must-not-appear";

fn client(url: &str) -> BinanceClient {
    BinanceClient::new(ExchangeConfig {
        id: "binance".into(),
        api_key: SECRET.into(),
        api_secret: SECRET.into(),
        ws_url: url.into(),
        rest_url: "http://127.0.0.1:1/never-used".into(),
        testnet: false,
    })
}

fn ticker() -> Value {
    json!({"e":"24hrTicker", "E":EVENT_TIME, "s":"BTCUSDT",
        "b":"99", "a":"101", "c":"100", "v":"12"})
}

fn kline() -> Value {
    json!({"e":"kline", "E":EVENT_TIME, "s":"BTCUSDT", "k":{
        "t":OPEN_TIME, "T":EVENT_TIME - 1, "s":"BTCUSDT", "i":"1m",
        "f":1, "L":2, "o":"100", "c":"102", "h":"105", "l":"98",
        "v":"12", "n":2, "x":true, "q":"1212", "V":"5", "Q":"505", "B":"0"
    }})
}

async fn reject_without_event(value: Value) {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    let error = exchange
        .handle_message(Message::Text(value.to_string()))
        .await
        .expect_err("Invalid feed input must be rejected");
    for diagnostic in [
        error.to_string(),
        format!("{error:#}"),
        format!("{error:?}"),
    ] {
        assert!(
            !diagnostic.contains(SECRET),
            "Raw input escaped into an error"
        );
    }
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn raw_ticker_preserves_exchange_event_time() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    exchange
        .handle_message(Message::Text(ticker().to_string()))
        .await
        .unwrap();
    let ExchangeEvent::Ticker(ticker) = receiver.try_recv().unwrap() else {
        panic!("Expected ticker");
    };
    assert_eq!(ticker.symbol, "BTCUSDT");
    assert_eq!(ticker.timestamp.timestamp_millis(), EVENT_TIME);
    assert_eq!(
        (ticker.bid, ticker.ask, ticker.last, ticker.volume),
        (99.0, 101.0, 100.0, 12.0)
    );
}

#[tokio::test]
async fn combined_ticker_unwraps_data_into_one_event() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    exchange
        .handle_message(Message::Text(
            json!({"stream":"btcusdt@ticker", "data":ticker()}).to_string(),
        ))
        .await
        .unwrap();
    let ExchangeEvent::Ticker(ticker) = receiver.try_recv().expect("Combined stream lost ticker")
    else {
        panic!("Expected ticker");
    };
    assert_eq!(ticker.timestamp.timestamp_millis(), EVENT_TIME);
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn closed_candle_preserves_exchange_open_time() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    exchange
        .handle_message(Message::Text(kline().to_string()))
        .await
        .unwrap();
    let ExchangeEvent::Candle(candle) = receiver.try_recv().unwrap() else {
        panic!("Expected candle");
    };
    assert_eq!(candle.timestamp.timestamp_millis(), OPEN_TIME);
    assert_eq!(
        serde_json::to_value(&candle).unwrap()["timeframe"],
        json!("1m")
    );
    assert_eq!(
        (
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            candle.volume
        ),
        (100.0, 105.0, 98.0, 102.0, 12.0)
    );
}

#[tokio::test]
async fn unfinished_candles_never_reach_strategy_consumers() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    let mut value = kline();
    value["k"]["x"] = json!(false);
    exchange
        .handle_message(Message::Text(value.to_string()))
        .await
        .unwrap();
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn malformed_ticker_numbers_identity_and_time_emit_nothing() {
    for field in ["b", "a", "c", "v", "E", "s"] {
        let mut value = ticker();
        value.as_object_mut().unwrap().remove(field);
        reject_without_event(value).await;
    }
    for (field, invalid) in [
        ("b", json!("0")),
        ("a", json!("NaN")),
        ("c", json!("inf")),
        ("c", json!("-1")),
        ("v", json!("-1")),
        ("v", json!(12)),
        ("E", json!(-1)),
        ("E", json!(1.5)),
        ("E", json!(i64::MAX)),
        ("s", json!(SECRET)),
        ("s", json!("BTC/USDT")),
        ("s", json!("")),
    ] {
        let mut value = ticker();
        value[field] = invalid;
        reject_without_event(value).await;
    }
}

#[tokio::test]
async fn malformed_closed_candles_emit_no_partial_event() {
    for field in ["t", "T", "s", "i", "o", "h", "l", "c", "v", "x"] {
        let mut value = kline();
        value["k"].as_object_mut().unwrap().remove(field);
        reject_without_event(value).await;
    }
    for (field, invalid) in [
        ("t", json!(-1)),
        ("t", json!(OPEN_TIME + 1)),
        ("T", json!(OPEN_TIME - 1)),
        ("T", json!(i64::MAX)),
        ("T", json!(EVENT_TIME - 2)),
        ("s", json!("ETHUSDT")),
        ("i", json!("7m")),
        ("x", json!(1)),
        ("o", json!("0")),
        ("h", json!("99")),
        ("l", json!("103")),
        ("c", json!("NaN")),
        ("v", json!("-1")),
        ("v", json!("inf")),
    ] {
        let mut value = kline();
        value["k"][field] = invalid;
        reject_without_event(value).await;
    }
    let mut missing_event_time = kline();
    missing_event_time.as_object_mut().unwrap().remove("E");
    reject_without_event(missing_event_time).await;
    let mut premature_close = kline();
    premature_close["E"] = json!(OPEN_TIME);
    reject_without_event(premature_close).await;
}

#[tokio::test]
async fn malformed_orderbook_never_drops_bad_levels_and_emits_rest() {
    for invalid in [
        json!([["NaN", "1"]]),
        json!([["1", "-2"]]),
        json!([["1"]]),
        json!([[1, 2]]),
        json!(null),
    ] {
        reject_without_event(json!({"e":"depthUpdate", "E":EVENT_TIME, "s":"BTCUSDT", "U":1, "u":2, "b":invalid, "a":[["101","1"]]})).await;
    }
}

#[tokio::test]
async fn control_errors_are_sanitized_and_acknowledgments_do_not_emit_quotes() {
    reject_without_event(json!({"code":2, "msg":SECRET, "id":1})).await;
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    exchange
        .handle_message(Message::Text(json!({"result":null,"id":1}).to_string()))
        .await
        .unwrap();
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
}

#[tokio::test]
async fn combined_stream_identity_cannot_disagree_with_the_payload() {
    for stream in [
        "ethusdt@ticker",
        "btcusdt@kline_1m",
        "btcusdt@ticker?token=synthetic",
    ] {
        reject_without_event(json!({"stream":stream,"data":ticker()})).await;
    }
    reject_without_event(json!({"stream":"btcusdt@ticker","data":null})).await;
}

#[tokio::test]
async fn orderbook_preserves_event_time_and_zero_quantity_deletions() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    exchange
        .handle_message(Message::Text(
            json!({"e":"depthUpdate", "E":EVENT_TIME,
        "s":"BTCUSDT", "U":1, "u":2, "b":[["99","0"]], "a":[["101","2"]]})
            .to_string(),
        ))
        .await
        .unwrap();
    let ExchangeEvent::OrderBook(book) = receiver.try_recv().unwrap() else {
        panic!("Expected depth update");
    };
    assert_eq!(book.timestamp.timestamp_millis(), EVENT_TIME);
    assert_eq!(book.bids, vec![(99.0, 0.0)]);
    assert_eq!(book.asks, vec![(101.0, 2.0)]);
}

#[tokio::test]
async fn malformed_json_diagnostics_never_quote_wire_input() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let error = exchange
        .handle_message(Message::Text(format!("{{\"{SECRET}\": invalid")))
        .await
        .unwrap_err();
    for diagnostic in [
        error.to_string(),
        format!("{error:#}"),
        format!("{error:?}"),
    ] {
        assert!(!diagnostic.contains(SECRET));
    }
}

#[tokio::test]
async fn offline_subscriptions_are_deduplicated_and_orderbook_is_retained() {
    let mut exchange = client("ws://127.0.0.1:1/ws");
    for _ in 0..2 {
        exchange.subscribe_ticker("BTC/USDT").await.unwrap();
        exchange.subscribe_candles("BTC/USDT", "1m").await.unwrap();
        exchange.subscribe_orderbook("BTC/USDT").await.unwrap();
    }
    let url = exchange.build_stream_url();
    assert_eq!(url.matches("btcusdt@ticker").count(), 1);
    assert_eq!(url.matches("btcusdt@kline_1m").count(), 1);
    assert_eq!(url.matches("btcusdt@depth").count(), 1);
}

#[tokio::test]
async fn unsupported_timeframe_and_invalid_symbol_are_refused_before_queuing() {
    let mut exchange = client("ws://127.0.0.1:1/ws");
    assert!(exchange.subscribe_candles("BTC/USDT", "7m").await.is_err());
    assert!(exchange
        .subscribe_ticker("BTC/USDT?credential=secret")
        .await
        .is_err());
    assert!(exchange.subscribe_orderbook("").await.is_err());
    assert!(!exchange.build_stream_url().contains("@"));
}

async fn loopback_peer(close_immediately: bool) -> (String, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = timeout(Duration::from_secs(3), listener.accept())
            .await
            .unwrap()
            .unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        if close_immediately {
            socket.send(Message::Close(None)).await.unwrap();
        }
        let _ = timeout(Duration::from_secs(3), async {
            while let Some(Ok(message)) = socket.next().await {
                if matches!(message, Message::Close(_)) {
                    let _ = socket.flush().await;
                    break;
                }
            }
        })
        .await;
    });
    (format!("ws://{address}/ws"), task)
}

#[tokio::test]
async fn connection_with_no_event_subscriber_remains_successful() {
    let (url, peer) = loopback_peer(false).await;
    let mut exchange = client(&url);
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    let result = timeout(Duration::from_secs(3), exchange.connect())
        .await
        .unwrap();
    assert!(
        result.is_ok(),
        "No local receiver must not invalidate a transport connection"
    );
    exchange.disconnect().await.unwrap();
    peer.await.unwrap();
}

#[tokio::test]
async fn explicit_disconnect_clears_transport_state_and_preserves_subscriptions() {
    let (url, peer) = loopback_peer(false).await;
    let mut exchange = client(&url);
    let _receiver = exchange.get_event_receiver();
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    exchange.disconnect().await.unwrap();
    assert!(
        !exchange.is_connected,
        "Disconnected transport was still marked connected"
    );
    assert!(exchange.ws_stream.is_none());
    assert!(exchange.build_stream_url().contains("btcusdt@ticker"));
    peer.await.unwrap();
}

#[tokio::test]
async fn peer_close_clears_transport_and_emits_one_disconnection() {
    let (url, peer) = loopback_peer(true).await;
    let mut exchange = client(&url);
    let mut receiver = exchange.get_event_receiver();
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    assert!(matches!(receiver.try_recv(), Ok(ExchangeEvent::Connected)));
    timeout(Duration::from_secs(3), exchange.run_message_loop())
        .await
        .unwrap()
        .unwrap();
    assert!(!exchange.is_connected);
    assert!(exchange.ws_stream.is_none());
    assert!(matches!(
        receiver.try_recv(),
        Ok(ExchangeEvent::Disconnected)
    ));
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    peer.await.unwrap();
}

#[tokio::test]
async fn subscriptions_added_after_connection_survive_an_explicit_reconnect() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let mut requests = Vec::new();
        let mut messages = Vec::new();
        for _ in 0..2 {
            let (stream, _) = timeout(Duration::from_secs(3), listener.accept())
                .await
                .unwrap()
                .unwrap();
            let mut socket = accept_hdr_async(stream, |request: &Request, response: Response| {
                requests.push(request.uri().to_string());
                Ok(response)
            })
            .await
            .unwrap();
            timeout(Duration::from_secs(3), async {
                while let Some(Ok(message)) = socket.next().await {
                    match message {
                        Message::Text(body) => {
                            messages.push(serde_json::from_str::<Value>(&body).unwrap())
                        }
                        Message::Close(_) => {
                            let _ = socket.flush().await;
                            break;
                        }
                        _ => {}
                    }
                }
            })
            .await
            .unwrap();
        }
        (requests, messages)
    });
    let mut exchange = client(&format!("ws://{address}/ws"));
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    for _ in 0..2 {
        exchange.subscribe_candles("ETH/USDT", "1m").await.unwrap();
        exchange.subscribe_orderbook("ETH/USDT").await.unwrap();
    }
    exchange.disconnect().await.unwrap();
    exchange.connect().await.unwrap();
    exchange.disconnect().await.unwrap();
    let (requests, messages) = peer.await.unwrap();
    assert_eq!(requests[0], "/stream?streams=btcusdt@ticker");
    assert_eq!(
        requests[1],
        "/stream?streams=btcusdt@ticker/ethusdt@kline_1m/ethusdt@depth"
    );
    assert_eq!(messages.len(), 2, "Duplicate subscriptions were sent");
    assert_eq!(messages[0]["method"], "SUBSCRIBE");
    assert_eq!(messages[0]["params"], json!(["ethusdt@kline_1m"]));
    assert_eq!(messages[1]["params"], json!(["ethusdt@depth"]));
    assert_ne!(messages[0]["id"], messages[1]["id"]);
}

#[tokio::test]
async fn ping_is_answered_before_the_peer_sends_the_next_market_frame() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        socket.send(Message::Ping(vec![1, 3, 5, 7])).await.unwrap();
        let message = timeout(Duration::from_secs(2), socket.next())
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert_eq!(message, Message::Pong(vec![1, 3, 5, 7]));
        socket
            .send(Message::Text(ticker().to_string()))
            .await
            .unwrap();
        socket.send(Message::Close(None)).await.unwrap();
        let _ = timeout(Duration::from_secs(2), socket.next()).await;
    });
    let mut exchange = client(&format!("ws://{address}/ws"));
    let mut receiver = exchange.get_event_receiver();
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    assert!(matches!(receiver.try_recv(), Ok(ExchangeEvent::Connected)));
    timeout(Duration::from_secs(3), exchange.run_message_loop())
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(receiver.try_recv(), Ok(ExchangeEvent::Ticker(_))));
    assert!(matches!(
        receiver.try_recv(),
        Ok(ExchangeEvent::Disconnected)
    ));
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    peer.await.unwrap();
}

#[tokio::test]
async fn abrupt_peer_eof_clears_connection_before_returning_an_error() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let socket = accept_async(stream).await.unwrap();
        drop(socket);
    });
    let mut exchange = client(&format!("ws://{address}/ws"));
    let _receiver = exchange.get_event_receiver();
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    assert!(timeout(Duration::from_secs(3), exchange.run_message_loop())
        .await
        .unwrap()
        .is_err());
    assert!(!exchange.is_connected);
    assert!(exchange.ws_stream.is_none());
    peer.await.unwrap();
}

#[tokio::test]
async fn corrupt_market_packet_stops_the_loop_without_publishing_a_quote() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut invalid = ticker();
        invalid["c"] = json!(SECRET);
        socket
            .send(Message::Text(invalid.to_string()))
            .await
            .unwrap();
        let _ = timeout(Duration::from_secs(2), socket.next()).await;
    });
    let mut exchange = client(&format!("ws://{address}/ws"));
    let mut receiver = exchange.get_event_receiver();
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    exchange.connect().await.unwrap();
    assert!(matches!(receiver.try_recv(), Ok(ExchangeEvent::Connected)));
    let error = timeout(Duration::from_secs(3), exchange.run_message_loop())
        .await
        .unwrap()
        .unwrap_err();
    assert!(!format!("{error:#}").contains(SECRET));
    let ExchangeEvent::Error(error) = receiver.try_recv().unwrap() else {
        panic!("Expected sanitized transport stop");
    };
    assert!(!error.contains(SECRET));
    assert!(matches!(
        receiver.try_recv(),
        Ok(ExchangeEvent::Disconnected)
    ));
    assert!(matches!(receiver.try_recv(), Err(TryRecvError::Empty)));
    assert!(!exchange.is_connected);
    assert!(exchange.ws_stream.is_none());
    peer.await.unwrap();
}

#[tokio::test]
async fn failed_handshake_has_no_subscription_free_fallback_or_source_error() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let refused = accept_hdr_async(stream, |_request: &Request, _response: Response| {
            Err(tokio_tungstenite::tungstenite::http::Response::builder()
                .status(400)
                .body(Some(SECRET.to_string()))
                .unwrap())
        })
        .await;
        assert!(refused.is_err());
        timeout(Duration::from_millis(200), listener.accept())
            .await
            .is_err()
    });
    let mut exchange = client(&format!("ws://{address}/ws"));
    exchange.subscribe_ticker("BTC/USDT").await.unwrap();
    let error = exchange
        .connect()
        .await
        .err()
        .expect("Rejected handshake must fail");
    assert!(!format!("{error:#}").contains(SECRET));
    assert!(!exchange.is_connected);
    assert!(exchange.ws_stream.is_none());
    assert!(
        peer.await.unwrap(),
        "A subscription-free retry connected unexpectedly"
    );
}

#[tokio::test]
async fn calendar_month_candles_accept_the_leap_year_february_boundary() {
    let exchange = client("ws://127.0.0.1:1/ws");
    let mut receiver = exchange.get_event_receiver();
    let mut value = kline();
    value["E"] = json!(1_709_251_200_000_i64); // 2024-03-01T00:00:00Z
    value["k"]["t"] = json!(1_706_745_600_000_i64); // 2024-02-01T00:00:00Z
    value["k"]["T"] = json!(1_709_251_199_999_i64);
    value["k"]["i"] = json!("1M");
    exchange
        .handle_message(Message::Text(value.to_string()))
        .await
        .unwrap();
    let ExchangeEvent::Candle(candle) = receiver.try_recv().unwrap() else {
        panic!("Expected monthly candle");
    };
    assert_eq!(candle.timeframe, "1M");
    assert_eq!(candle.timestamp.timestamp_millis(), 1_706_745_600_000);
}
