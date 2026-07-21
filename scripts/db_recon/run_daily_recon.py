#!/usr/bin/env python3
"""
run_daily_recon.py
==================
Orchestrator for the DB2 reconciliation pipeline.

Pipeline:
  1. db2_extract.py      → <folder>/<TABLE>__source.csv + __target.csv
  2. recon_fast.py       → per-table HTML, payload.json, metrics.json, breaks.csv
  3. recon_entities.py   → entity & linkage checks (ORDER / ORDER_FILL / FILL)
  4. recon_bundle.py     → single index.html
  5. manifest.json       → updated in data_dir root (read by the AI-Tools tab)

Exit code:  0 = all tables PASS
            2 = at least one table or entity check BREAK

Usage:
    python run_daily_recon.py --config db2_recon_config.yaml
    python run_daily_recon.py --config db2_recon_config.yaml --dry-run
    python run_daily_recon.py --config db2_recon_config.yaml --print-path
    python run_daily_recon.py --config db2_recon_config.yaml --skip-extract
    python run_daily_recon.py --config db2_recon_config.yaml --only-table FILL
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required.  pip install pyyaml")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #

def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_folder(args_base_dir, config_path, cfg):
    """Dated output folder: <data_dir>/<YYYY-MM-DD>/"""
    if args_base_dir is not None:
        base = os.path.abspath(args_base_dir)
    else:
        config_dir = os.path.dirname(os.path.abspath(config_path))
        data_dir = cfg.get("output", {}).get("data_dir")
        if data_dir:
            base = os.path.normpath(os.path.join(config_dir, data_dir))
        else:
            base = os.path.normpath(os.path.join(HERE, "..", "..", "data", "db_recon"))
    return os.path.join(base, datetime.now(timezone.utc).strftime("%Y-%m-%d"))


def data_dir_root(config_path, cfg):
    """Parent of the dated folders — where manifest.json lives."""
    config_dir = os.path.dirname(os.path.abspath(config_path))
    data_dir = cfg.get("output", {}).get("data_dir")
    if data_dir:
        return os.path.normpath(os.path.join(config_dir, data_dir))
    return os.path.normpath(os.path.join(HERE, "..", "..", "data", "db_recon"))


def flow_args(table_cfg, cfg):
    """Return extra CLI args for recon_fast.py if a flow template is configured."""
    tname = table_cfg.get("flow")
    if not tname:
        return []
    templates = cfg.get("flow_templates", {})
    tmpl = templates.get(tname)
    if not tmpl:
        return []
    return [
        "--flow-label",    tmpl["label"],
        "--flow-template", tmpl["template"],
        "--flow-anchor",   tmpl["anchor"],
    ]


# --------------------------------------------------------------------------- #
# Sub-process helpers
# --------------------------------------------------------------------------- #

PYTHON = sys.executable
SCRIPTS = HERE  # all scripts live next to this file


def run(cmd, description):
    """Run a command, stream output, raise on non-zero exit."""
    print(f"\n[RUN] {description}")
    print(f"      {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    if result.returncode not in (0, 2):
        # exit 2 means BREAK (expected); anything else is a real error
        print(f"ERROR: {description} exited {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.returncode


# --------------------------------------------------------------------------- #
# Pipeline stages
# --------------------------------------------------------------------------- #

def stage_extract(args, cfg, folder):
    """Call db2_extract.py for all (or one) table."""
    cmd = [
        PYTHON, os.path.join(SCRIPTS, "db2_extract.py"),
        "--config", args.config,
        "--output-dir", folder,
    ]
    if args.only_table:
        cmd += ["--only-table", args.only_table]
    run(cmd, "DB2 extract")


def stage_dry_run(args, cfg):
    """Print SQL for all tables; no DB connection."""
    cmd = [
        PYTHON, os.path.join(SCRIPTS, "db2_extract.py"),
        "--config", args.config,
        "--dry-run",
    ]
    if args.only_table:
        cmd += ["--only-table", args.only_table]
    run(cmd, "dry-run SQL preview")


def stage_compare(args, cfg, folder):
    """Run recon_fast.py for each table that has extracted CSVs.
    Break status is determined by reading <TABLE>_metrics.json after each run
    because recon_fast.py exits 0 regardless of outcome."""
    tables = cfg["tables"]
    if args.only_table:
        tables = [t for t in tables if t["name"] == args.only_table]

    any_break = False
    for t in tables:
        name = t["name"]
        src = os.path.join(folder, f"{name}__source.csv")
        tgt = os.path.join(folder, f"{name}__target.csv")
        if not os.path.exists(src) or not os.path.exists(tgt):
            print(f"  [SKIP] {name}: CSV files not found in {folder}")
            continue

        keys = ",".join(t["key_columns"])
        cmd = [
            PYTHON, os.path.join(SCRIPTS, "recon_fast.py"),
            "--source",  src,
            "--target",  tgt,
            "--keys",    keys,
            "--table",   name,
            "--out-dir", folder,
            "--buckets", str(t.get("n_buckets", 256)),
            "--groups",  str(t.get("n_groups", 8)),
        ] + flow_args(t, cfg)

        run(cmd, f"compare {name}")

        # recon_fast.py exits 0 always — read the metrics to get the status
        metrics_path = os.path.join(folder, f"{name}_metrics.json")
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path) as f:
                    m = json.load(f)
                status = m.get("status", "?")
                print(f"  [{status:5}] {name}")
                if status == "BREAK":
                    any_break = True
            except (json.JSONDecodeError, KeyError):
                print(f"  [WARN ] {name}: could not read metrics")

    return any_break


def stage_entity_checks(args, cfg, folder):
    """Run recon_entities.py if ORDER, ORDER_FILL, and FILL CSVs are present.
    Break status is read from ENTITY_metrics.json."""
    needed = {
        "order_source":     os.path.join(folder, "ORDER__source.csv"),
        "order_target":     os.path.join(folder, "ORDER__target.csv"),
        "orderfill_source": os.path.join(folder, "ORDER_FILL__source.csv"),
        "orderfill_target": os.path.join(folder, "ORDER_FILL__target.csv"),
        "fill_source":      os.path.join(folder, "FILL__source.csv"),
        "fill_target":      os.path.join(folder, "FILL__target.csv"),
    }
    missing = [k for k, v in needed.items() if not os.path.exists(v)]
    if missing:
        print(f"\n[SKIP] Entity checks: missing CSVs: {', '.join(missing)}")
        return False

    cmd = [
        PYTHON, os.path.join(SCRIPTS, "recon_entities.py"),
        "--order-source",     needed["order_source"],
        "--order-target",     needed["order_target"],
        "--orderfill-source", needed["orderfill_source"],
        "--orderfill-target", needed["orderfill_target"],
        "--fill-source",      needed["fill_source"],
        "--fill-target",      needed["fill_target"],
        "--out-dir",          folder,
    ]
    run(cmd, "entity & linkage checks")

    # Read result from ENTITY_metrics.json
    ep = os.path.join(folder, "ENTITY_metrics.json")
    if os.path.exists(ep):
        try:
            with open(ep) as f:
                m = json.load(f)
            return m.get("status") == "BREAK"
        except (json.JSONDecodeError, KeyError):
            pass
    return False


def stage_bundle(args, cfg, folder):
    """Bundle all payloads into a single index.html."""
    date_label = os.path.basename(folder)
    title = args.title or f"DB2 migration recon — {date_label}"
    cmd = [
        PYTHON, os.path.join(SCRIPTS, "recon_bundle.py"),
        "--folder", folder,
        "--title",  title,
    ]
    run(cmd, "bundle report")


def update_manifest(config_path, cfg, folder, any_break):
    """Write / update data_dir/manifest.json with this run's summary."""
    root = data_dir_root(config_path, cfg)
    manifest_path = os.path.join(root, "manifest.json")

    # Load existing manifest or start fresh
    runs = []
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                runs = json.load(f).get("runs", [])
        except (json.JSONDecodeError, KeyError):
            runs = []

    date_label = os.path.basename(folder)
    report_rel = os.path.join("data", "db_recon", date_label, "index.html").replace("\\", "/")

    # Collect per-table status from metrics files
    table_summary = {}
    for fname in os.listdir(folder):
        if fname.endswith("_metrics.json") and not fname.startswith("ENTITY"):
            table = fname.replace("_metrics.json", "")
            try:
                with open(os.path.join(folder, fname)) as f:
                    m = json.load(f)
                table_summary[table] = {
                    "status":        m.get("status", "?"),
                    "rows_source":   m.get("rows_source", 0),
                    "rows_with_diffs": m.get("rows_with_diffs", 0),
                    "cell_diffs":    m.get("cell_diffs", 0),
                    "match_rate_pct": m.get("match_rate_pct", 0),
                }
            except Exception:
                pass

    # Entity status
    entity_status = None
    ep = os.path.join(folder, "ENTITY_metrics.json")
    if os.path.exists(ep):
        try:
            with open(ep) as f:
                entity_status = json.load(f).get("status")
        except Exception:
            pass

    run_entry = {
        "date":   date_label,
        "status": "BREAK" if any_break else "PASS",
        "report": report_rel,
        "tables": table_summary,
        "entity": entity_status,
    }

    # Replace entry for this date if it already exists
    runs = [r for r in runs if r.get("date") != date_label]
    runs.insert(0, run_entry)
    runs = runs[:60]  # keep last 60 runs

    manifest = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "runs":    runs,
    }
    os.makedirs(root, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n[OK]  manifest updated: {manifest_path}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="DB2 reconciliation pipeline orchestrator")
    ap.add_argument("--config",       required=True, help="Path to db2_recon_config.yaml")
    ap.add_argument("--base-dir",     default=None,  help="Override output base directory")
    ap.add_argument("--title",        default=None,  help="Override report title")
    ap.add_argument("--only-table",                  help="Restrict to one table by name")
    ap.add_argument("--dry-run",      action="store_true",
                    help="Print generated SQL for all tables; do not connect to DB2")
    ap.add_argument("--print-path",   action="store_true",
                    help="Print the resolved output folder and exit")
    ap.add_argument("--skip-extract", action="store_true",
                    help="Skip DB2 extraction; compare existing CSVs in today's folder")
    args = ap.parse_args()

    cfg = load_config(args.config)
    folder = resolve_folder(args.base_dir, args.config, cfg)

    if args.print_path:
        print(os.path.abspath(folder))
        return

    if args.dry_run:
        stage_dry_run(args, cfg)
        return

    print(f"\n{'='*60}")
    print(f"  DB2 Reconciliation Pipeline")
    print(f"  Output folder: {os.path.abspath(folder)}")
    print(f"{'='*60}")
    os.makedirs(folder, exist_ok=True)

    any_break = False

    if not args.skip_extract:
        stage_extract(args, cfg, folder)

    any_break |= stage_compare(args, cfg, folder)

    if not args.only_table:
        any_break |= stage_entity_checks(args, cfg, folder)
        stage_bundle(args, cfg, folder)
        update_manifest(args.config, cfg, folder, any_break)

    print(f"\n{'='*60}")
    print(f"  Overall status: {'BREAK' if any_break else 'PASS'}")
    print(f"  Report:         {os.path.join(os.path.abspath(folder), 'index.html')}")
    print(f"{'='*60}\n")

    sys.exit(2 if any_break else 0)


if __name__ == "__main__":
    main()
