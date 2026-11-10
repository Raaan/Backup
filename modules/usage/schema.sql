CREATE TABLE hourly_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,

    cumulative_upload_bytes INTEGER NOT NULL,
    cumulative_download_bytes INTEGER NOT NULL,

    upload_bytes INTEGER NOT NULL,
    download_bytes INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,

    UNIQUE(ts, user)
);

CREATE INDEX idx_hourly_usage_ts
ON hourly_usage(ts);

CREATE INDEX idx_hourly_usage_user
ON hourly_usage(user);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE accounts (
    account_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE account_aliases (
    alias TEXT PRIMARY KEY COLLATE NOCASE,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    kind TEXT NOT NULL
);

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL,
    adapter TEXT NOT NULL,
    service_name TEXT,
    endpoint TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);

CREATE TABLE source_accounts (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    raw_identity TEXT NOT NULL,
    account_id TEXT REFERENCES accounts(account_id),
    mapping_status TEXT NOT NULL
        CHECK(mapping_status IN ('mapped','pending')),
    PRIMARY KEY(source_id,raw_identity),
    CHECK (
        (mapping_status='mapped' AND account_id IS NOT NULL)
        OR
        (mapping_status='pending' AND account_id IS NULL)
    )
);

CREATE TABLE billing_periods (
    period_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL UNIQUE,
    ended_at_utc TEXT,
    legacy_baseline_bytes INTEGER NOT NULL DEFAULT 0
        CHECK(legacy_baseline_bytes >= 0)
);

CREATE TABLE source_counter_state (
    source_id TEXT NOT NULL,
    raw_identity TEXT NOT NULL,
    direction TEXT NOT NULL
        CHECK(direction IN ('uplink','downlink')),
    generation_id TEXT NOT NULL,
    last_counter_bytes INTEGER NOT NULL
        CHECK(last_counter_bytes >= 0),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(source_id,raw_identity,direction),
    FOREIGN KEY(source_id,raw_identity)
        REFERENCES source_accounts(source_id,raw_identity)
);

CREATE TABLE source_hourly_usage (
    period_id TEXT NOT NULL REFERENCES billing_periods(period_id),
    hour_start_utc TEXT NOT NULL,
    source_id TEXT NOT NULL,
    raw_identity TEXT NOT NULL,
    account_id TEXT REFERENCES accounts(account_id),
    upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes>=0),
    download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes>=0),
    PRIMARY KEY(period_id,hour_start_utc,source_id,raw_identity),
    FOREIGN KEY(source_id,raw_identity)
        REFERENCES source_accounts(source_id,raw_identity)
);

CREATE TABLE collection_batches (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    batch_id TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(source_id,batch_id)
);

CREATE INDEX idx_source_hourly_account
ON source_hourly_usage(period_id,account_id,hour_start_utc);

CREATE INDEX idx_source_accounts_account
ON source_accounts(account_id);

CREATE TABLE v1_bridge_checkpoint (
    period_id TEXT PRIMARY KEY
        REFERENCES billing_periods(period_id),
    cutover_utc TEXT NOT NULL,
    last_legacy_hour_utc TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL
);

CREATE TABLE v1_bridge_gap (
    period_id TEXT NOT NULL
        REFERENCES billing_periods(period_id),
    hour_start_utc TEXT NOT NULL,
    account_id TEXT NOT NULL
        REFERENCES accounts(account_id),
    upload_bytes INTEGER NOT NULL CHECK(upload_bytes>=0),
    download_bytes INTEGER NOT NULL CHECK(download_bytes>=0),
    PRIMARY KEY(period_id,account_id)
);

CREATE VIEW v1_source_hourly AS
SELECT h.period_id,h.hour_start_utc,
       h.source_id,h.raw_identity,
       m.account_id,a.canonical_name,
       m.mapping_status,
       h.upload_bytes,h.download_bytes,
       h.upload_bytes+h.download_bytes AS total_bytes
FROM source_hourly_usage h
LEFT JOIN source_accounts m
  ON m.source_id=h.source_id
 AND m.raw_identity=h.raw_identity
LEFT JOIN accounts a ON a.account_id=m.account_id;

CREATE VIEW v1_account_hourly_segments AS
SELECT
    CASE WHEN julianday(h.ts)<julianday(b.period_id)
         THEN NULL ELSE b.period_id END AS period_id,
    strftime('%Y-%m-%dT%H:00:00+00:00',h.ts)
        AS hour_start_utc,
    m.account_id,
    CASE WHEN julianday(h.ts)<julianday(b.period_id)
         THEN 'legacy_archive'
         ELSE 'legacy_snapshot' END AS segment,
    h.upload_bytes,h.download_bytes
FROM hourly_usage h
CROSS JOIN v1_bridge_checkpoint b
JOIN account_aliases m ON m.alias=h.user
WHERE julianday(h.ts)<julianday(b.period_id)
   OR (
       julianday(h.ts)>=julianday(b.period_id)
       AND julianday(h.ts)<=julianday(b.last_legacy_hour_utc)
   )

UNION ALL

SELECT period_id,hour_start_utc,account_id,
       'cutover_gap',upload_bytes,download_bytes
FROM v1_bridge_gap

UNION ALL

SELECT period_id,hour_start_utc,account_id,
       'source_metered',upload_bytes,download_bytes
FROM v1_source_hourly
WHERE account_id IS NOT NULL;

CREATE VIEW v1_account_hourly_all AS
SELECT period_id,hour_start_utc,account_id,
       SUM(upload_bytes) AS upload_bytes,
       SUM(download_bytes) AS download_bytes,
       SUM(upload_bytes+download_bytes) AS total_bytes
FROM v1_account_hourly_segments
GROUP BY period_id,hour_start_utc,account_id;

CREATE VIEW v1_account_hourly AS
SELECT * FROM v1_account_hourly_all
WHERE period_id IS NOT NULL;

CREATE VIEW v1_account_period AS
SELECT p.period_id,a.account_id,a.canonical_name,
       COALESCE(SUM(h.upload_bytes),0) AS upload_bytes,
       COALESCE(SUM(h.download_bytes),0) AS download_bytes,
       COALESCE(SUM(h.total_bytes),0) AS total_bytes
FROM billing_periods p
CROSS JOIN accounts a
LEFT JOIN v1_account_hourly h
  ON h.period_id=p.period_id
 AND h.account_id=a.account_id
GROUP BY p.period_id,a.account_id,a.canonical_name;

CREATE VIEW v1_unmapped_hourly AS
SELECT * FROM v1_source_hourly WHERE account_id IS NULL;

CREATE VIEW v1_period_unallocated AS
SELECT period_id,legacy_baseline_bytes AS total_bytes
FROM billing_periods;

CREATE TABLE v1_live_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
