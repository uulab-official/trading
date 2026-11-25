//! SQLite-backed simulated-event journal. No broker or market-data access.
//! Always replay verified events before returning account state or appending.

use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use sqlx::{
    sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous},
    Row, Sqlite, SqlitePool, Transaction,
};
use std::{fs::OpenOptions, path::Path, time::Duration};

use super::paper_ledger::{AccountSnapshot, ApplyOutcome, JournalEvent, PaperConfig, PaperLedger};

const SCHEMA_VERSION: i64 = 1;
/// This development store replays the entire journal for each operation.
pub const MAX_EVENTS: i64 = 2048;
const MAX_PAYLOAD_BYTES: usize = 4096;
const BACKUP_VERSION: u32 = 1;
/// Bound the complete logical backup before parsing untrusted input.
pub const MAX_BACKUP_BYTES: usize = 9 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JournalHead {
    pub sequence: i64,
    pub hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedState {
    pub head: JournalHead,
    pub account: AccountSnapshot,
}

/// Store independently of the database. Trust in this value is supplied by the
/// caller, not by the SHA-256 chain itself.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JournalCheckpoint {
    pub ledger_id: String,
    pub head: JournalHead,
}

#[derive(Debug, Clone)]
pub struct ExportedBackup {
    pub checkpoint: JournalCheckpoint,
    pub bytes: Vec<u8>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct BackupV1 {
    version: u32,
    ledger_id: String,
    config: PaperConfig,
    head: JournalHead,
    events: Vec<JournalEvent>,
}

/// An explicitly identified, isolated, single-currency paper account. Separate
/// instances coordinate writes through SQLite; no in-memory state is authoritative.
pub struct PaperStore {
    pool: SqlitePool,
    ledger_id: String,
    config: PaperConfig,
    checkpoint_floor: Option<JournalCheckpoint>,
}

impl PaperStore {
    /// Never opens or replaces a pre-existing file. If initialization fails,
    /// leave the file in place for inspection rather than silently resetting it.
    pub async fn create(path: &Path, ledger_id: &str, config: PaperConfig) -> Result<Self> {
        let ledger = PaperLedger::new(config)?;
        Self::create_from_ledger(path, ledger_id, ledger, None).await
    }

    async fn create_from_ledger(
        path: &Path,
        ledger_id: &str,
        ledger: PaperLedger,
        checkpoint_floor: Option<JournalCheckpoint>,
    ) -> Result<Self> {
        validate_identity(ledger_id, ledger.config())?;
        ensure!(
            ledger.journal().len() <= MAX_EVENTS as usize,
            "Paper journal capacity exceeded"
        );
        let (head, _) = replay_with_head(ledger.config(), ledger_id, ledger.journal(), checkpoint_floor.as_ref())?;
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(path)
            .context("Paper ledger must be created at a new file path")?
            .sync_all()?;

        let mut store = Self::connect(path, ledger_id, ledger.config().clone()).await?;
        store.checkpoint_floor = checkpoint_floor;
        let config_json = encode(&store.config)?;
        let hash = genesis(&store.ledger_id, &config_json);
        let mut tx = store.pool.begin().await?;
        sqlx::query(
            r#"CREATE TABLE paper_meta (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                schema_version INTEGER NOT NULL,
                ledger_id TEXT NOT NULL,
                config_json TEXT NOT NULL,
                head_seq INTEGER NOT NULL CHECK(head_seq >= 0),
                head_hash TEXT NOT NULL CHECK(length(head_hash) = 64)
            )"#,
        )
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            r#"CREATE TABLE paper_events (
                seq INTEGER PRIMARY KEY CHECK(seq > 0),
                event_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB)) <= 4096),
                prev_hash TEXT NOT NULL CHECK(length(prev_hash) = 64),
                event_hash TEXT NOT NULL CHECK(length(event_hash) = 64)
            )"#,
        )
        .execute(&mut *tx)
        .await?;
        let mut previous_hash = hash;
        for (index, event) in ledger.journal().iter().enumerate() {
            let sequence = i64::try_from(index)? + 1;
            let payload = encode(event)?;
            let next_hash = event_digest(sequence, &previous_hash, &payload);
            sqlx::query(
                "INSERT INTO paper_events (seq, event_id, payload, prev_hash, event_hash) VALUES (?, ?, ?, ?, ?)",
            )
            .bind(sequence)
            .bind(&event.event_id)
            .bind(&payload)
            .bind(&previous_hash)
            .bind(&next_hash)
            .execute(&mut *tx)
            .await?;
            previous_hash = next_hash;
        }
        ensure!(previous_hash == head.hash, "Unexpected restore head");
        sqlx::query("INSERT INTO paper_meta VALUES (1, ?, ?, ?, ?, ?)")
            .bind(SCHEMA_VERSION)
            .bind(&store.ledger_id)
            .bind(&config_json)
            .bind(head.sequence)
            .bind(&head.hash)
            .execute(&mut *tx)
            .await?;
        // Verify inside the same transaction so a failed initialization never
        // publishes a valid-looking, empty account in place of a restored one.
        let (restored, restored_head) = store.load(&mut tx).await?;
        ensure!(
            restored_head == head && restored.snapshot()? == ledger.snapshot()?,
            "Restored paper journal differs from verified input"
        );
        tx.commit().await?;
        Ok(store)
    }

    /// Requires an existing file, exact original account ID and configuration.
    /// Missing, corrupt and unsupported databases fail closed; no auto-migration.
    pub async fn open(path: &Path, ledger_id: &str, config: PaperConfig) -> Result<Self> {
        validate_identity(ledger_id, &config)?;
        let store = Self::connect(path, ledger_id, config).await?;
        store.audit().await?;
        Ok(store)
    }

    /// A trusted checkpoint is a lower bound on the accepted history, not an
    /// equality check. Later valid descendants may continue trading in paper mode.
    /// Every subsequent audit, export and append checks this floor in its own
    /// transaction. The caller must supply the latest independently retained
    /// checkpoint when restarting the process.
    pub async fn open_with_checkpoint(
        path: &Path,
        ledger_id: &str,
        config: PaperConfig,
        checkpoint: JournalCheckpoint,
    ) -> Result<Self> {
        validate_identity(ledger_id, &config)?;
        validate_checkpoint(ledger_id, &checkpoint)?;
        let mut store = Self::connect(path, ledger_id, config).await?;
        store.checkpoint_floor = Some(checkpoint);
        store.audit().await?;
        Ok(store)
    }

    /// Produce an internally consistent logical backup without copying SQLite
    /// files or depending on WAL checkpoint timing. Persist the returned
    /// checkpoint independently if rollback detection is required.
    pub async fn export_backup(&self) -> Result<ExportedBackup> {
        let mut tx = self.pool.begin().await?;
        let (ledger, head) = self.load(&mut tx).await?;
        let backup = BackupV1 {
            version: BACKUP_VERSION,
            ledger_id: self.ledger_id.clone(),
            config: self.config.clone(),
            head: head.clone(),
            events: ledger.journal().to_vec(),
        };
        let bytes = serde_json::to_vec(&backup)?;
        ensure!(bytes.len() <= MAX_BACKUP_BYTES, "Paper backup too large");
        tx.commit().await?;
        Ok(ExportedBackup {
            checkpoint: JournalCheckpoint { ledger_id: self.ledger_id.clone(), head },
            bytes,
        })
    }

    /// Restore ONLY into a new file. The supplied checkpoint must match the
    /// exact backup head; a prefix or checkpoint read only from this untrusted
    /// backup is not independent evidence. Validate before touching the path.
    /// All events and metadata are created in a single SQLite transaction.
    pub async fn restore_backup(
        path: &Path,
        ledger_id: &str,
        config: PaperConfig,
        bytes: &[u8],
        expected: JournalCheckpoint,
    ) -> Result<Self> {
        validate_identity(ledger_id, &config)?;
        validate_checkpoint(ledger_id, &expected)?;
        ensure!(bytes.len() <= MAX_BACKUP_BYTES, "Paper backup too large");
        let backup: BackupV1 = serde_json::from_slice(bytes)?;
        ensure!(backup.version == BACKUP_VERSION, "Unsupported paper backup version");
        ensure!(
            backup.ledger_id == ledger_id && backup.config == config,
            "Paper backup account or configuration mismatch"
        );
        // Reject silently ignored fields, alternative encodings and trailing bytes.
        ensure!(serde_json::to_vec(&backup)? == bytes, "Noncanonical paper backup");
        ensure!(backup.head == expected.head, "Paper backup checkpoint mismatch");
        let (head, ledger) = replay_with_head(&config, ledger_id, &backup.events, Some(&expected))?;
        ensure!(head == expected.head, "Paper backup replay head mismatch");
        Self::create_from_ledger(path, ledger_id, ledger, Some(expected)).await
    }

    async fn connect(path: &Path, ledger_id: &str, config: PaperConfig) -> Result<Self> {
        ensure!(
            std::fs::symlink_metadata(path)?.file_type().is_file(),
            "Paper ledger requires a regular file (not a symlink)"
        );
        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(false)
            .journal_mode(SqliteJournalMode::Wal)
            .synchronous(SqliteSynchronous::Full)
            .busy_timeout(Duration::from_secs(5));
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        Ok(Self {
            pool,
            ledger_id: ledger_id.to_owned(),
            config,
            checkpoint_floor: None,
        })
    }

    pub async fn close(self) {
        self.pool.close().await;
    }

    /// A consistent read transaction checks every accepted event and the stored
    /// journal head before exposing a derived snapshot.
    pub async fn audit(&self) -> Result<VerifiedState> {
        let mut tx = self.pool.begin().await?;
        let (ledger, head) = self.load(&mut tx).await?;
        let account = ledger.snapshot()?;
        tx.commit().await?;
        Ok(VerifiedState { head, account })
    }

    /// The latest verified head may be retained by an independent system.
    pub async fn checkpoint(&self) -> Result<JournalCheckpoint> {
        let state = self.audit().await?;
        Ok(JournalCheckpoint { ledger_id: self.ledger_id.clone(), head: state.head })
    }

    /// Acquire SQLite's writer lock *before* reading any state. Never append
    /// from a cached snapshot. Commit the event and journal head atomically.
    /// A failed/ambiguous commit can safely be retried using the same event ID.
    pub async fn apply(&self, event: JournalEvent) -> Result<ApplyOutcome> {
        let mut tx = self.pool.begin().await?;
        let locked = sqlx::query("UPDATE paper_meta SET head_seq = head_seq WHERE id = 1")
            .execute(&mut *tx)
            .await?;
        ensure!(locked.rows_affected() == 1, "Missing paper ledger metadata");

        let (mut ledger, previous) = self.load(&mut tx).await?;
        let outcome = ledger.apply(event.clone())?;
        if outcome == ApplyOutcome::Duplicate {
            tx.rollback().await?;
            return Ok(outcome);
        }
        ensure!(previous.sequence < MAX_EVENTS, "Paper journal capacity reached");
        let next_sequence = previous.sequence + 1;
        let payload = encode(&event)?;
        let next_hash = event_digest(next_sequence, &previous.hash, &payload);

        sqlx::query(
            "INSERT INTO paper_events (seq, event_id, payload, prev_hash, event_hash) VALUES (?, ?, ?, ?, ?)",
        )
        .bind(next_sequence)
        .bind(&event.event_id)
        .bind(&payload)
        .bind(&previous.hash)
        .bind(&next_hash)
        .execute(&mut *tx)
        .await?;

        let updated = sqlx::query(
            "UPDATE paper_meta SET head_seq = ?, head_hash = ? WHERE id = 1 AND head_seq = ? AND head_hash = ?",
        )
        .bind(next_sequence)
        .bind(&next_hash)
        .bind(previous.sequence)
        .bind(&previous.hash)
        .execute(&mut *tx)
        .await?;
        ensure!(updated.rows_affected() == 1, "Paper journal head changed");
        tx.commit().await?;
        Ok(outcome)
    }

    async fn load(&self, tx: &mut Transaction<'_, Sqlite>) -> Result<(PaperLedger, JournalHead)> {
        let metadata = sqlx::query(
            r#"SELECT id, schema_version, ledger_id,
                CASE WHEN length(CAST(config_json AS BLOB)) <= 4096 THEN config_json END AS config_json,
                head_seq, head_hash
                FROM paper_meta LIMIT 2"#,
        )
        .fetch_all(&mut **tx)
        .await?;
        ensure!(metadata.len() == 1, "Invalid paper ledger metadata count");
        let meta = &metadata[0];
        ensure!(
            meta.try_get::<i64, _>("id")? == 1
                && meta.try_get::<i64, _>("schema_version")? == SCHEMA_VERSION,
            "Unsupported paper ledger schema"
        );
        ensure!(
            meta.try_get::<String, _>("ledger_id")? == self.ledger_id,
            "Paper ledger identity mismatch"
        );
        let config_json = encode(&self.config)?;
        ensure!(
            meta.try_get::<Option<String>, _>("config_json")?.as_deref()
                == Some(config_json.as_str()),
            "Paper ledger configuration mismatch"
        );
        let head = JournalHead {
            sequence: meta.try_get("head_seq")?,
            hash: meta.try_get("head_hash")?,
        };
        ensure!(
            (0..=MAX_EVENTS).contains(&head.sequence),
            "Invalid paper journal head sequence"
        );

        let rows = sqlx::query(
            r#"SELECT seq, event_id,
                CASE WHEN length(CAST(payload AS BLOB)) <= 4096 THEN payload END AS payload,
                prev_hash, event_hash
                FROM paper_events ORDER BY seq LIMIT ?"#,
        )
        .bind(MAX_EVENTS + 1)
        .fetch_all(&mut **tx)
        .await?;
        ensure!(
            i64::try_from(rows.len())? == head.sequence,
            "Paper journal length differs from committed head"
        );

        let mut previous_hash = genesis(&self.ledger_id, &config_json);
        let mut checkpoint_matched = false;
        if let Some(checkpoint) = &self.checkpoint_floor {
            ensure!(head.sequence >= checkpoint.head.sequence, "Paper journal rolled back");
            checkpoint_matched = check_prefix(checkpoint, 0, &previous_hash)?;
        }
        let mut ledger = PaperLedger::new(self.config.clone())?;
        for (index, row) in rows.iter().enumerate() {
            let sequence = i64::try_from(index)? + 1;
            ensure!(
                row.try_get::<i64, _>("seq")? == sequence,
                "Paper journal sequence gap"
            );
            let payload: String = row
                .try_get::<Option<String>, _>("payload")?
                .context("Missing or oversized paper event")?;
            let event: JournalEvent = serde_json::from_str(&payload)?;
            ensure!(
                encode(&event)? == payload
                    && row.try_get::<String, _>("event_id")? == event.event_id,
                "Noncanonical or mismatched paper event"
            );
            let expected_hash = event_digest(sequence, &previous_hash, &payload);
            ensure!(
                row.try_get::<String, _>("prev_hash")? == previous_hash
                    && row.try_get::<String, _>("event_hash")? == expected_hash,
                "Paper event digest mismatch"
            );
            ensure!(
                ledger.apply(event)? == ApplyOutcome::Applied,
                "Duplicate accepted paper event"
            );
            previous_hash = expected_hash;
            if let Some(checkpoint) = &self.checkpoint_floor {
                checkpoint_matched |= check_prefix(checkpoint, sequence, &previous_hash)?;
            }
        }
        ensure!(
            self.checkpoint_floor.is_none() || checkpoint_matched,
            "Trusted paper checkpoint not found"
        );
        ensure!(previous_hash == head.hash, "Paper journal head digest mismatch");
        Ok((ledger, head))
    }
}

fn validate_identity(ledger_id: &str, config: &PaperConfig) -> Result<()> {
    ensure!(
        !ledger_id.is_empty()
            && ledger_id.len() <= 128
            && ledger_id.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.' | b':')),
        "Invalid paper ledger identity"
    );
    PaperLedger::new(config.clone())?;
    Ok(())
}

fn encode(value: &impl serde::Serialize) -> Result<String> {
    let json = serde_json::to_string(value)?;
    ensure!(json.len() <= MAX_PAYLOAD_BYTES, "Paper journal payload too large");
    Ok(json)
}

/// Length-framed fields and separate versioned domains avoid concatenation
/// ambiguity. These are integrity checks, not signatures or authentication.
fn digest(parts: &[&[u8]]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part);
    }
    hex::encode(hasher.finalize())
}

fn genesis(ledger_id: &str, config_json: &str) -> String {
    digest(&[
        b"trading-paper-ledger/v1/genesis",
        ledger_id.as_bytes(),
        config_json.as_bytes(),
    ])
}

fn event_digest(sequence: i64, previous_hash: &str, payload: &str) -> String {
    digest(&[
        b"trading-paper-ledger/v1/event",
        &sequence.to_be_bytes(),
        previous_hash.as_bytes(),
        payload.as_bytes(),
    ])
}

fn validate_checkpoint(ledger_id: &str, checkpoint: &JournalCheckpoint) -> Result<()> {
    ensure!(checkpoint.ledger_id == ledger_id, "Paper checkpoint account mismatch");
    ensure!(
        (0..=MAX_EVENTS).contains(&checkpoint.head.sequence)
            && checkpoint.head.hash.len() == 64
            && checkpoint.head.hash.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)),
        "Invalid paper checkpoint"
    );
    Ok(())
}

/// A valid newer head must contain the exact trusted prefix. A matching
/// sequence with different bytes indicates a fork, not a legitimate extension.
fn check_prefix(checkpoint: &JournalCheckpoint, sequence: i64, hash: &str) -> Result<bool> {
    if checkpoint.head.sequence != sequence {
        return Ok(false);
    }
    ensure!(checkpoint.head.hash == hash, "Paper journal diverges from trusted checkpoint");
    Ok(true)
}

fn replay_with_head(
    config: &PaperConfig,
    ledger_id: &str,
    events: &[JournalEvent],
    floor: Option<&JournalCheckpoint>,
) -> Result<(JournalHead, PaperLedger)> {
    ensure!(events.len() <= MAX_EVENTS as usize, "Paper journal capacity exceeded");
    let mut ledger = PaperLedger::new(config.clone())?;
    let mut hash = genesis(ledger_id, &encode(config)?);
    let mut matched = false;
    if let Some(checkpoint) = floor {
        validate_checkpoint(ledger_id, checkpoint)?;
        matched = check_prefix(checkpoint, 0, &hash)?;
    }
    for (index, event) in events.iter().enumerate() {
        let sequence = i64::try_from(index)? + 1;
        hash = event_digest(sequence, &hash, &encode(event)?);
        ensure!(
            ledger.apply(event.clone())? == ApplyOutcome::Applied,
            "Duplicate accepted paper event"
        );
        if let Some(checkpoint) = floor {
            matched |= check_prefix(checkpoint, sequence, &hash)?;
        }
    }
    ensure!(floor.is_none() || matched, "Trusted paper checkpoint not found");
    Ok((JournalHead { sequence: i64::try_from(events.len())?, hash }, ledger))
}
