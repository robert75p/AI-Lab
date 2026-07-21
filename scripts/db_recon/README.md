# DB2 Reconciliation Toolkit

Config-driven pipeline that compares two DB2 databases across 12 tables and produces a single self-contained HTML sign-off report.

## Prerequisites

```bash
cd scripts/db_recon
pip install -r requirements.txt
# jaydebeapi also requires a DB2 JDBC driver JAR (db2jcc4.jar)
```

## Credentials

Never stored in the config. Set before running a live extract:

```bash
export DB2_SRC_USER=...
export DB2_SRC_PASSWORD=...
export DB2_TGT_USER=...
export DB2_TGT_PASSWORD=...
```

## Daily run

```bash
# From repo root
npm run recon:daily
# or directly:
python scripts/db_recon/run_daily_recon.py --config scripts/db_recon/db2_recon_config.yaml
```

Output: `data/db_recon/<YYYY-MM-DD>/index.html`  
Exit code: **0** = all PASS, **2** = at least one BREAK.

## Other commands

| Command | Effect |
|---|---|
| `npm run recon:dry-run` | Print SQL for all 12 tables — no DB connection |
| `npm run recon:path` | Print the resolved output folder path |
| `npm run recon:compare` | Re-run comparison on existing CSVs (skip extract) |
| `npm run recon:test` | Run property tests + golden fixtures (no DB needed) |

## Single-table runs

```bash
python scripts/db_recon/run_daily_recon.py \
  --config scripts/db_recon/db2_recon_config.yaml \
  --only-table FILL
```

## Output structure

```
data/db_recon/
  manifest.json                    ← read by the AI-Tools tab (run history)
  2026-07-21/
    index.html                     ← self-contained single-file report
    FILL_metrics.json
    FILL_breaks.csv
    ENTITY_metrics.json
    ...
```

## Key column status

| Table | Keys | Status |
|---|---|---|
| ORDER | ORDER_ID, ORDER_VID | CONFIRMED |
| ORDER_FILL | ORDER_ID, ORDER_VID, FILL_ID, FILL_VID | CONFIRMED |
| FILL | FILL_ID, FILL_VID | CONFIRMED |
| ORDER_PARENT | ORDER_ID, ORDER_VID | **UNCONFIRMED** |
| ORDER_OUTGOING | ORDER_ID, ORDER_VID | **UNCONFIRMED** |
| ORDER_INCOMING | ORDER_ID, ORDER_VID | **UNCONFIRMED** |
| ORDER_COMMENT | ORDER_ID, ORDER_VID, COMMENT_SEQ | **UNCONFIRMED** |
| ORDER_GROUP_MEMBER | GROUP_ID, ORDER_ID, ORDER_VID | **UNCONFIRMED** |
| ORDER_INST_ALL | ORDER_ID, ORDER_VID, INST_SEQ | **UNCONFIRMED** |
| ENRICHED_EXECUTION | EXECUTION_ID, EXECUTION_VID | **UNCONFIRMED** |
| ENRICHED_EXECUTION_DERIVED | EXECUTION_ID, EXECUTION_VID | **UNCONFIRMED** |
| ST_CONTRACT | CONTRACT_ID, CONTRACT_VID | **UNCONFIRMED** |

Confirm UNCONFIRMED keys against `SYSCAT.KEYCOLUSE` before a production run. Use `--dry-run` to review the generated SQL first.

## Pipeline flow

```
db2_extract.py      →  <TABLE>__source.csv + <TABLE>__target.csv
recon_fast.py       →  per-table HTML, payload.json, metrics.json, breaks.csv
recon_entities.py   →  ENTITY_metrics.json (ORDER / ORDER_FILL / FILL)
recon_bundle.py     →  index.html (single file, all data inlined)
manifest.json       →  updated in data/db_recon/ root
```
