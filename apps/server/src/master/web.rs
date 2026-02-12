use actix_web::{web, App, HttpResponse, HttpServer};
use std::sync::Arc;
use tracing::{error, info};

pub struct WebDashboard {
    port: u16,
}

impl WebDashboard {
    pub fn new(port: u16) -> Self {
        Self { port }
    }

    pub async fn run(self) -> std::io::Result<()> {
        info!("Starting web dashboard on port {}", self.port);

        HttpServer::new(move || {
            App::new()
                .route("/health", web::get().to(health_check))
                .route("/api/status", web::get().to(get_status))
        })
        .bind(format!("0.0.0.0:{}", self.port))?
        .run()
        .await
    }
}

async fn health_check() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "status": "healthy",
        "service": "trading-server"
    }))
}

async fn get_status() -> HttpResponse {
    HttpResponse::Ok().json(serde_json::json!({
        "status": "running",
        "mode": "master",
        "timestamp": chrono::Utc::now().to_rfc3339()
    }))
}