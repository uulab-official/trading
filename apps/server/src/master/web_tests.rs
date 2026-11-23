use super::*;
use actix_web::{http::StatusCode, test};

#[actix_web::test]
async fn no_worker_observation_is_service_unavailable() {
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(WebDashboard::new(0)))
            .route("/health", web::get().to(health_check)),
    )
    .await;
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[actix_web::test]
async fn status_never_advertises_unimplemented_execution() {
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(WebDashboard::new(0)))
            .route("/api/status", web::get().to(get_status)),
    )
    .await;
    let body: serde_json::Value = test::call_and_read_body_json(
        &app,
        test::TestRequest::get().uri("/api/status").to_request(),
    )
    .await;
    assert_eq!(body["mode"], "observation_only");
    assert_eq!(body["execution_enabled"], false);
    assert_eq!(body["healthy"], false);
}

#[actix_web::test]
async fn absent_account_adapters_are_unavailable_instead_of_empty_balances() {
    let app = test::init_service(
        App::new()
            .route("/api/positions", web::get().to(get_positions))
            .route("/api/orders", web::get().to(get_orders)),
    )
    .await;
    for path in ["/api/positions", "/api/orders"] {
        let body: serde_json::Value =
            test::call_and_read_body_json(&app, test::TestRequest::get().uri(path).to_request())
                .await;
        assert_eq!(
            body["available"], false,
            "{path} fabricated available account data"
        );
        assert_eq!(body["execution_enabled"], false);
    }
}

async fn receiving_dashboard() -> (WebDashboard, u64) {
    let dashboard = WebDashboard::new(0);
    let generation = dashboard.begin_worker_connection().await.unwrap();
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "observation_only".into(),
                message: "Public market observations".into(),
            },
        )
        .await
        .unwrap();
    dashboard
        .record_worker_message(generation, WorkerMessage::Heartbeat)
        .await
        .unwrap();
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::PriceUpdate {
                symbol: "BTC/USDT".into(),
                price: 100.0,
            },
        )
        .await
        .unwrap();
    (dashboard, generation)
}

#[actix_web::test]
async fn recent_valid_observation_and_heartbeat_are_both_required_for_http_200() {
    let dashboard = WebDashboard::new(0);
    let generation = dashboard.begin_worker_connection().await.unwrap();
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(dashboard.clone()))
            .route("/health", web::get().to(health_check)),
    )
    .await;
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "observation_only".into(),
                message: "Connected".into(),
            },
        )
        .await
        .unwrap();
    dashboard
        .record_worker_message(generation, WorkerMessage::Heartbeat)
        .await
        .unwrap();
    let request = || test::TestRequest::get().uri("/health").to_request();
    assert_eq!(
        test::call_service(&app, request()).await.status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::PriceUpdate {
                symbol: "BTCUSDT".into(),
                price: 100.0,
            },
        )
        .await
        .unwrap();
    assert_eq!(
        test::call_service(&app, request()).await.status(),
        StatusCode::OK
    );
}

#[actix_web::test]
async fn heartbeat_and_connected_messages_cannot_revive_a_halted_generation() {
    let (dashboard, generation) = receiving_dashboard().await;
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(dashboard.clone()))
            .route("/health", web::get().to(health_check)),
    )
    .await;
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "feed_halted".into(),
                message: "Feed stopped".into(),
            },
        )
        .await
        .unwrap();
    dashboard
        .record_worker_message(generation, WorkerMessage::Heartbeat)
        .await
        .unwrap();
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "observation_only".into(),
                message: "Delayed connected event".into(),
            },
        )
        .await
        .unwrap();
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::PriceUpdate {
                symbol: "BTCUSDT".into(),
                price: 999.0,
            },
        )
        .await
        .unwrap();
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body: Value = test::read_body_json(response).await;
    assert_eq!(body["status"], "feed_halted");
    assert_eq!(body["prices"]["BTCUSDT"]["price"], 100.0);
}

#[actix_web::test]
async fn an_open_connection_with_old_heartbeat_or_old_market_is_unavailable() {
    let (dashboard, generation) = receiving_dashboard().await;
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(dashboard.clone()))
            .route("/health", web::get().to(health_check)),
    )
    .await;
    dashboard
        .state
        .write()
        .await
        .heartbeat
        .as_mut()
        .unwrap()
        .tick = Instant::now() - Duration::from_secs(91);
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body: Value = test::read_body_json(response).await;
    assert_eq!(body["readiness_reason"], "heartbeat_stale");
    dashboard
        .record_worker_message(generation, WorkerMessage::Heartbeat)
        .await
        .unwrap();
    dashboard.state.write().await.market.as_mut().unwrap().tick =
        Instant::now() - Duration::from_secs(91);
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body: Value = test::read_body_json(response).await;
    assert_eq!(body["readiness_reason"], "market_observation_stale");
    assert_eq!(body["exchange_event_freshness_verified"], false);
}

#[actix_web::test]
async fn late_subscriber_receives_current_state_then_new_status_events() {
    let (dashboard, generation) = receiving_dashboard().await;
    let (mut receiver, snapshot) = dashboard.subscribe_snapshot().await;
    assert_eq!(snapshot["healthy"], true);
    assert_eq!(snapshot["prices"]["BTCUSDT"]["price"], 100.0);
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "feed_halted".into(),
                message: "Stopped".into(),
            },
        )
        .await
        .unwrap();
    let event = receiver.recv().await.unwrap();
    assert_eq!(event.body["type"], "status");
    assert_eq!(event.body["state"]["status"], "feed_halted");
    assert_eq!(event.body["state"]["healthy"], false);
    assert!(event.revision > snapshot["revision"].as_u64().unwrap());
}

#[actix_web::test]
async fn websocket_first_frame_contains_the_stored_status_snapshot() {
    let (dashboard, generation) = receiving_dashboard().await;
    dashboard
        .record_worker_message(
            generation,
            WorkerMessage::SystemStatus {
                status: "feed_halted".into(),
                message: "Stopped before browser connected".into(),
            },
        )
        .await
        .unwrap();
    let app = test::init_service(
        App::new()
            .app_data(web::Data::new(dashboard))
            .route("/ws", web::get().to(websocket_handler)),
    )
    .await;
    let response = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/ws")
            .insert_header(("Connection", "upgrade"))
            .insert_header(("Upgrade", "websocket"))
            .insert_header(("Sec-WebSocket-Version", "13"))
            .insert_header(("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ=="))
            .to_request(),
    )
    .await;
    assert_eq!(response.status(), StatusCode::SWITCHING_PROTOCOLS);
    let body = tokio::time::timeout(Duration::from_secs(3), test::read_body(response))
        .await
        .unwrap();
    assert_eq!(body[0], 0x81, "First frame must be the text snapshot");
    let (offset, length) = match body[1] & 0x7f {
        126 => (4, usize::from(u16::from_be_bytes([body[2], body[3]]))),
        127 => (
            10,
            u64::from_be_bytes(body[2..10].try_into().unwrap()) as usize,
        ),
        length => (2, usize::from(length)),
    };
    let snapshot: Value = serde_json::from_slice(&body[offset..offset + length]).unwrap();
    assert_eq!(snapshot["type"], "snapshot");
    assert_eq!(snapshot["state"]["status"], "feed_halted");
    assert_eq!(snapshot["state"]["execution_enabled"], false);
}

#[actix_web::test]
async fn instance_identity_survives_worker_replacement_and_changes_for_a_new_master() {
    let dashboard = WebDashboard::new(0);
    let original = dashboard.snapshot().await["instance_id"].clone();
    dashboard.begin_worker_connection().await.unwrap();
    dashboard.begin_worker_connection().await.unwrap();
    assert_eq!(dashboard.snapshot().await["instance_id"], original);
    assert_ne!(
        WebDashboard::new(0).snapshot().await["instance_id"],
        original
    );
}
