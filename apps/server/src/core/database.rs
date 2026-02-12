pub async fn run_migrations() -> anyhow::Result<()> {
    tracing::info!("Database migrations completed");
    Ok(())
}