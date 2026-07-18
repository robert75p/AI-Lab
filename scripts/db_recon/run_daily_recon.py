#!/usr/bin/env python3
"""
run_daily_recon.py
==================
One-command daily wrapper: extract from both DB2 databases into a dated folder,
then compare and write metrics back into that same folder.

Default output location is resolved from output.data_dir in the config YAML
(relative to the config file), so a bare invocation writes to the project's
data/db_recon/<YYYY-MM-DD>/ folder. Override with --base-dir if needed.

    python run_daily_recon.py --config db2_recon_config.yaml
    python run_daily_recon.py --config db2_recon_config.yaml --base-dir /tmp/recon

Creates <base-dir>/YYYY-MM-DD/ , runs db2_extract.py then recon_compare.py.
Propagates recon_compare.py's exit code (2 = at least one BREAK) so a scheduler
(cron / Airflow / Control-M) can alert on failure.
"""
import argparse
import os
import subprocess
import sys
from datetime import date

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required.  pip install pyyaml")
    sys.exit(1)


def resolve_base_dir(args_base_dir, config_path, cfg):
    """Return the base output directory as an absolute path.

    Priority:
      1. Explicit --base-dir argument
      2. output.data_dir from the config YAML (relative to the config file)
      3. Hard fallback: <repo_root>/data/db_recon
    """
    if args_base_dir is not None:
        return os.path.abspath(args_base_dir)

    config_dir = os.path.dirname(os.path.abspath(config_path))
    data_dir = cfg.get("output", {}).get("data_dir")
    if data_dir:
        return os.path.normpath(os.path.join(config_dir, data_dir))

    # Fallback: two levels up from this script, then data/db_recon
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "data", "db_recon"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--base-dir", default=None,
                    help="Override output base dir (default: output.data_dir from config)")
    ap.add_argument("--date", default=date.today().isoformat(),
                    help="Folder date label (default: today)")
    ap.add_argument("--print-path", action="store_true",
                    help="Print the resolved dated output folder and exit (no DB connection)")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    here = os.path.dirname(os.path.abspath(__file__))
    base_dir = resolve_base_dir(args.base_dir, args.config, cfg)
    folder = os.path.join(base_dir, args.date)

    if args.print_path:
        print(os.path.abspath(folder))
        sys.exit(0)

    os.makedirs(folder, exist_ok=True)
    print(f"Output folder: {os.path.abspath(folder)}")

    print(f"[1/2] Extracting both DB2 databases -> {folder}")
    r = subprocess.run([sys.executable, os.path.join(here, "db2_extract.py"),
                        "--config", args.config, "--output-dir", folder])
    if r.returncode != 0:
        print("Extract failed; aborting compare."); sys.exit(r.returncode)

    print(f"\n[2/2] Comparing extracts in {folder}")
    r = subprocess.run([sys.executable, os.path.join(here, "recon_compare.py"),
                        "--config", args.config, "--folder", folder])
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
