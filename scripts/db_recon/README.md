# DB2 Migration Reconciliation Toolkit

Daily validation of a DB2 → DB2 migration across the ORDER / FILL / ST_CONTRACT
domain. Both databases are the **same DB2 version**, so the row-hash is computed
in-database on each side with identical SQL and the hashes compare directly.

## What's in here

| File | Role |
|---|---|
| `db2_extract.py` | Connects to **both** DB2 databases, generates a normalized row-hash query per table from the catalog, and writes `key + anchor + ROW_HASH` extracts into a folder. |
| `recon_compare.py` | Reads the extracts from that folder, compares source vs target by key + hash, and writes break files + metrics **back into the same folder**. |
| `run_daily_recon.py` | One-command wrapper: extract → compare into a dated folder. |
| `db2_recon_config.yaml` | Shared config: connections, hash settings, per-table keys/anchors/excludes. |

## Data flow

```
   ┌────────────┐        ┌────────────┐
   │ SOURCE DB2 │        │ TARGET DB2 │
   └─────┬──────┘        └─────┬──────┘
         │  key+anchor+hash    │  key+anchor+hash   (db2_extract.py)
         ▼                     ▼
   ./recon/2026-07-01/  ORDER_FILL__source.csv , ORDER_FILL__target.csv , ...
         │
         ▼  (recon_compare.py, same folder)
   ORDER_FILL__only_in_source.csv
   ORDER_FILL__only_in_target.csv
   ORDER_FILL__hash_mismatch.csv     <- carries ORDER_ID / FILL_ID anchor
   ORDER_FILL__metrics.json
   recon_summary.csv / recon_summary.json
```

## Setup

### 1. Python environment

```bash
# From the repo root
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r scripts/db_recon/requirements.txt
```

### 2. Credentials

Copy `.env.example` to `.env` (repo root) and fill in real values:

```bash
cp .env.example .env
```

Then source it before running (or set the vars in your CI/scheduler):

```bash
# Linux / macOS
source .env
# Windows PowerShell
Get-Content .env | ForEach-Object { $v = $_ -split '=',2; if ($v[0]) { [System.Environment]::SetEnvironmentVariable($v[0], $v[1]) } }
```

Required variables: `DB2_SRC_USER`, `DB2_SRC_PASSWORD`, `DB2_TGT_USER`, `DB2_TGT_PASSWORD`.

### 3. Config

Edit `scripts/db_recon/db2_recon_config.yaml`:
- Set `driver.jar` to the absolute path of `db2jcc4.jar`.
- Set `connections.source.jdbc_url` / `connections.target.jdbc_url` and both `schema` values.
- **Confirm each table's `key_columns` against `SYSCAT.KEYCOLUSE`** — the shipped values are best guesses.

## Output location

Dated run folders land under the project data directory by default:

```
data/db_recon/<YYYY-MM-DD>/
```

This is controlled by `output.data_dir` in `db2_recon_config.yaml` (resolved relative
to the config file). Override at invocation with `--base-dir /some/other/path`.

## Run

```bash
# Preview the exact SQL sent to DB2 — no connection needed, good for sign-off:
npm run recon:dry-run
# or directly:
python3 scripts/db_recon/db2_extract.py --config scripts/db_recon/db2_recon_config.yaml --dry-run

# Print the resolved output folder (no connection, no files written):
npm run recon:path

# Full daily run — extract + compare, output under data/db_recon/<today>/:
npm run recon:daily
# or directly:
python3 scripts/db_recon/run_daily_recon.py --config scripts/db_recon/db2_recon_config.yaml

# Override output location:
python3 scripts/db_recon/run_daily_recon.py \
    --config scripts/db_recon/db2_recon_config.yaml \
    --base-dir /custom/path

# Or run the two steps separately:
python3 scripts/db_recon/db2_extract.py   --config scripts/db_recon/db2_recon_config.yaml --output-dir data/db_recon/2026-07-01
python3 scripts/db_recon/recon_compare.py --config scripts/db_recon/db2_recon_config.yaml --folder    data/db_recon/2026-07-01
```

Exit code `0` = all tables PASS, `2` = at least one BREAK — wire the `2` into
cron/Airflow/Control-M alerting.

## Comparison metrics (per table, in `*__metrics.json` and `recon_summary.csv`)

rows source/target, duplicate keys per side, matched keys, only-in-source,
only-in-target, hash matches, hash mismatches, match-rate %, and PASS/BREAK.

## Traceability

Every break file carries the **anchor** columns (ORDER_ID / FILL_ID /
CONTRACT_ID), so a break in a child table like `ENRICHED_EXECUTION_DERIVED`
surfaces the FILL/ORDER to investigate.

## Detail mode — from "which rows broke" to "which fields differ"

The daily extract is intentionally lightweight (`key + anchor + hash`), so it
tells you *which* rows broke, not *which columns*. Two detail modes re-pull the
**full rows** from both DB2s for just the broken rows and produce a cell-level
diff — from the same config, no separate tool.

**By break list** — re-pull every broken key found in a folder's break files:
```bash
python db2_extract.py --config db2_recon_config.yaml \
    --folder ./recon/2026-07-01 --detail-keys --tolerance 0.001
# optionally restrict:  --only-table ORDER_FILL
```
Writes per table: `<TABLE>__detail_source.csv`, `<TABLE>__detail_target.csv`,
`<TABLE>__detail_diff.csv` (long format: key, column, value_source,
value_target), and `<TABLE>__detail_summary.json`.

**By entity** — investigate one order/fill/contract across every table that
carries that anchor:
```bash
python db2_extract.py --config db2_recon_config.yaml \
    --folder ./recon/2026-07-01 --detail-entity ORDER_ID=100234
```
Writes `<TABLE>__entity_source/target.csv` and `<TABLE>__entity_diff.csv` for
each related table — the full lifecycle of one broken entity, both sides.

Both modes apply the config's `exclude_columns` and an optional `--tolerance`
so the diff shows real business differences, not formatting noise.

## Before you trust the numbers

1. Confirm `key_columns` are actually unique — the tool flags duplicate keys,
   which invalidate everything downstream if present.
2. Confirm the SHA256 algorithm constant on your platform
   (`SELECT HEX(HASH('ABC', 2)) FROM SYSIBM.SYSDUMMY1`). Both sides use it
   identically, so this is a correctness nicety, not a break risk.
3. Keep `exclude_columns` current — migration audit/ETL fields (load timestamps,
   batch ids, row versions) differ by design and must be excluded from the hash.
4. BLOB/XML/binary columns are auto-excluded with a warning; handle them
   explicitly if they carry business data.
```
