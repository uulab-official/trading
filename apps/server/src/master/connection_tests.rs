use super::*;
use tokio::io::AsyncWriteExt;
use tokio::time::{timeout, Duration};

async fn send(stream: &mut UnixStream, message: WorkerMessage) {
    let body = serde_json::to_vec(&message).unwrap();
    stream
        .write_all(&(body.len() as u32).to_be_bytes())
        .await
        .unwrap();
    stream.write_all(&body).await.unwrap();
}

async fn make_receiving(stream: &mut UnixStream, symbol: &str, price: f64) {
    send(
        stream,
        WorkerMessage::SystemStatus {
            status: "observation_only".into(),
            message: "Public observations".into(),
        },
    )
    .await;
    send(stream, WorkerMessage::Heartbeat).await;
    send(
        stream,
        WorkerMessage::PriceUpdate {
            symbol: symbol.into(),
            price,
        },
    )
    .await;
}

async fn wait_healthy(dashboard: &WebDashboard, generation: u64) {
    timeout(Duration::from_secs(3), async {
        loop {
            let snapshot = dashboard.snapshot().await;
            if snapshot["connection_generation"] == generation && snapshot["healthy"] == true {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn socket_eof_marks_previously_observed_prices_unavailable() {
    let dashboard = Arc::new(WebDashboard::new(0));
    let generation = dashboard.begin_worker_connection().await.unwrap();
    let (master, mut worker) = UnixStream::pair().unwrap();
    let task = tokio::spawn(handle_worker_connection(
        master,
        dashboard.clone(),
        generation,
    ));
    make_receiving(&mut worker, "BTCUSDT", 100.0).await;
    wait_healthy(&dashboard, generation).await;
    worker.shutdown().await.unwrap();
    timeout(Duration::from_secs(3), task)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let snapshot = dashboard.snapshot().await;
    assert_eq!(snapshot["healthy"], false);
    assert_eq!(snapshot["status"], "worker_disconnected");
    assert_eq!(snapshot["prices"]["BTCUSDT"]["stale"], true);
}

#[tokio::test]
async fn terminal_worker_status_survives_eof_without_a_browser() {
    for status in ["feed_unavailable", "feed_halted", "stopped"] {
        let dashboard = Arc::new(WebDashboard::new(0));
        let generation = dashboard.begin_worker_connection().await.unwrap();
        let (master, mut worker) = UnixStream::pair().unwrap();
        let task = tokio::spawn(handle_worker_connection(
            master,
            dashboard.clone(),
            generation,
        ));
        send(
            &mut worker,
            WorkerMessage::SystemStatus {
                status: status.into(),
                message: "Observations stopped".into(),
            },
        )
        .await;
        worker.shutdown().await.unwrap();
        task.await.unwrap().unwrap();
        let snapshot = dashboard.snapshot().await;
        assert_eq!(snapshot["status"], status);
        assert_eq!(snapshot["healthy"], false);
        assert_eq!(snapshot["connection_state"], "disconnected");
    }
}

#[tokio::test]
async fn old_connection_messages_and_eof_cannot_replace_new_worker_state() {
    let dashboard = Arc::new(WebDashboard::new(0));
    let old_generation = dashboard.begin_worker_connection().await.unwrap();
    let (old_master, mut old_worker) = UnixStream::pair().unwrap();
    let old_task = tokio::spawn(handle_worker_connection(
        old_master,
        dashboard.clone(),
        old_generation,
    ));
    make_receiving(&mut old_worker, "BTCUSDT", 100.0).await;
    wait_healthy(&dashboard, old_generation).await;
    let new_generation = dashboard.begin_worker_connection().await.unwrap();
    let (new_master, mut new_worker) = UnixStream::pair().unwrap();
    let new_task = tokio::spawn(handle_worker_connection(
        new_master,
        dashboard.clone(),
        new_generation,
    ));
    make_receiving(&mut new_worker, "ETHUSDT", 200.0).await;
    wait_healthy(&dashboard, new_generation).await;
    send(
        &mut old_worker,
        WorkerMessage::SystemStatus {
            status: "feed_halted".into(),
            message: "Old worker stopped".into(),
        },
    )
    .await;
    send(
        &mut old_worker,
        WorkerMessage::PriceUpdate {
            symbol: "BTCUSDT".into(),
            price: 1.0,
        },
    )
    .await;
    old_worker.shutdown().await.unwrap();
    old_task.await.unwrap().unwrap();
    let snapshot = dashboard.snapshot().await;
    assert_eq!(snapshot["healthy"], true);
    assert_eq!(snapshot["connection_generation"], new_generation);
    assert_eq!(snapshot["prices"]["ETHUSDT"]["price"], 200.0);
    assert!(snapshot["prices"].get("BTCUSDT").is_none());
    new_worker.shutdown().await.unwrap();
    new_task.await.unwrap().unwrap();
}

#[tokio::test]
async fn malformed_or_oversized_ipc_frames_latch_connection_failure() {
    for body in [
        vec![0xff, 0xff, 0xff, 0xff],
        vec![0, 0, 0, 10, b'{'],
        vec![0, 0, 0],
    ] {
        let dashboard = Arc::new(WebDashboard::new(0));
        let generation = dashboard.begin_worker_connection().await.unwrap();
        let (master, mut worker) = UnixStream::pair().unwrap();
        let task = tokio::spawn(handle_worker_connection(
            master,
            dashboard.clone(),
            generation,
        ));
        worker.write_all(&body).await.unwrap();
        worker.shutdown().await.unwrap();
        assert!(task.await.unwrap().is_err());
        let snapshot = dashboard.snapshot().await;
        assert_eq!(snapshot["healthy"], false);
        assert_eq!(snapshot["connection_state"], "error");
    }
}
