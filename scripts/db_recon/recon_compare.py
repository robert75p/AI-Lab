#!/usr/bin/env python3
"""
recon_compare.py
================
Updated reconciliation tool. Reads the key+anchor+ROW_HASH extracts produced by
db2_extract.py from a folder, compares source vs target for every table, and
writes the break files and comparison metrics back into the SAME folder.

Input files expected in --folder (produced by db2_extract.py):
    <TABLE>__source.csv     key + anchor + ROW_HASH
    <TABLE>__target.csv     key + anchor + ROW_HASH

Config (--config) tells the tool the key columns and anchor columns per table
(the same YAML used by db2_extract.py).

Outputs written back into --folder, per table:
    <TABLE>__only_in_source.csv    keys+anchors present only in source
    <TABLE>__only_in_target.csv    keys+anchors present only in target
    <TABLE>__hash_mismatch.csv     matched keys whose ROW_HASH differs (carries anchor)
    <TABLE>__duplicate_keys.csv    duplicate keys detected in either side (if any)
    <TABLE>__metrics.json          per-table comparison metrics

Folder-level rollups:
    recon_summary.csv              one row per table (append-friendly for trending)
    recon_summary.json            full run snapshot

Exit codes:
    0  all tables PASS
    2  at least one table BREAK  (wire this to your scheduler's alerting)

Usage:
    python recon_compare.py --config db2_recon_config.yaml --folder ./recon/2026-07-01
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. pip install pyyaml")
    sys.exit(1)


def load_extract(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    df.columns = [c.strip().upper() for c in df.columns]
    return df


def compare_table(table_name, key_columns, anchor_columns, folder):
    key_columns = [k.upper() for k in key_columns]
    anchor_columns = [a.upper() for a in anchor_columns]

    src_path = os.path.join(folder, f"{table_name}__source.csv")
    tgt_path = os.path.join(folder, f"{table_name}__target.csv")
    if not os.path.exists(src_path) or not os.path.exists(tgt_path):
        return {"table": table_name, "status": "MISSING_EXTRACT",
                "source_present": os.path.exists(src_path),
                "target_present": os.path.exists(tgt_path)}

    src = load_extract(src_path)
    tgt = load_extract(tgt_path)

    for k in key_columns + ["ROW_HASH"]:
        if k not in src.columns or k not in tgt.columns:
            return {"table": table_name, "status": "BAD_EXTRACT",
                    "detail": f"missing column {k}"}

    ts = datetime.now(timezone.utc).isoformat()
    m = {
        "table": table_name,
        "run_timestamp": ts,
        "keys": key_columns,
        "anchors": anchor_columns,
        "rows_source": len(src),
        "rows_target": len(tgt),
    }

    # ---- duplicate keys (a data-quality break in its own right) --------------
    dup_src = src[src.duplicated(subset=key_columns, keep=False)]
    dup_tgt = tgt[tgt.duplicated(subset=key_columns, keep=False)]
    m["duplicate_keys_source"] = int(dup_src[key_columns].drop_duplicates().shape[0])
    m["duplicate_keys_target"] = int(dup_tgt[key_columns].drop_duplicates().shape[0])
    if not dup_src.empty or not dup_tgt.empty:
        dup = pd.concat([dup_src.assign(_side="source"), dup_tgt.assign(_side="target")])
        dup.to_csv(os.path.join(folder, f"{table_name}__duplicate_keys.csv"), index=False)

    src_u = src.drop_duplicates(subset=key_columns, keep="first")
    tgt_u = tgt.drop_duplicates(subset=key_columns, keep="first")

    merged = src_u.merge(
        tgt_u, on=key_columns, how="outer",
        suffixes=("_SRC", "_TGT"), indicator=True,
    )

    only_src = merged[merged["_merge"] == "left_only"]
    only_tgt = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"].copy()

    m["matched_keys"] = int(len(both))
    m["only_in_source"] = int(len(only_src))
    m["only_in_target"] = int(len(only_tgt)) 

    # anchor columns: which suffix to surface for each side's break file
    def anchor_cols(suffix):
        cols = []
        for a in anchor_columns:
            if a in key_columns:
                cols.append(a)                      # unsuffixed (merge key)
            elif f"{a}_{suffix}" in merged.columns:
                cols.append(f"{a}_{suffix}")
        return cols

    if not only_src.empty:
        cols = key_columns + [c for c in anchor_cols("SRC") if c not in key_columns] + \
               (["ROW_HASH_SRC"] if "ROW_HASH_SRC" in only_src.columns else [])
        only_src[cols].to_csv(os.path.join(folder, f"{table_name}__only_in_source.csv"), index=False)
    if not only_tgt.empty:
        cols = key_columns + [c for c in anchor_cols("TGT") if c not in key_columns] + \
               (["ROW_HASH_TGT"] if "ROW_HASH_TGT" in only_tgt.columns else [])
        only_tgt[cols].to_csv(os.path.join(folder, f"{table_name}__only_in_target.csv"), index=False)

    # ---- hash comparison on matched keys -------------------------------------
    h_src = "ROW_HASH_SRC" if "ROW_HASH_SRC" in both.columns else "ROW_HASH"
    h_tgt = "ROW_HASH_TGT" if "ROW_HASH_TGT" in both.columns else "ROW_HASH"
    both["_match"] = both[h_src] == both[h_tgt]
    mismatches = both[~both["_match"]]

    m["hash_matches"] = int(both["_match"].sum())
    m["hash_mismatches"] = int(len(mismatches))
    denom = max(m["matched_keys"], 1)
    m["match_rate_pct"] = round(100.0 * m["hash_matches"] / denom, 4)

    if not mismatches.empty:
        cols = key_columns + [c for c in anchor_cols("SRC") if c not in key_columns] + [h_src, h_tgt]
        out = mismatches[cols].rename(columns={h_src: "ROW_HASH_SOURCE", h_tgt: "ROW_HASH_TARGET"})
        out.to_csv(os.path.join(folder, f"{table_name}__hash_mismatch.csv"), index=False)

    m["status"] = "PASS" if (
        m["only_in_source"] == 0 and m["only_in_target"] == 0
        and m["hash_mismatches"] == 0
        and m["duplicate_keys_source"] == 0 and m["duplicate_keys_target"] == 0
    ) else "BREAK"

    with open(os.path.join(folder, f"{table_name}__metrics.json"), "w") as f:
        json.dump(m, f, indent=2)
    return m


def print_metrics(m):
    if m.get("status") in ("MISSING_EXTRACT", "BAD_EXTRACT"):
        print(f"\n{m['table']}: {m['status']} {m.get('detail','')}")
        return
    print(f"\n{'='*58}\n{m['table']}  ->  {m['status']}\n{'='*58}")
    print(f"  rows source/target    : {m['rows_source']} / {m['rows_target']}")
    print(f"  duplicate keys s/t    : {m['duplicate_keys_source']} / {m['duplicate_keys_target']}")
    print(f"  matched keys          : {m['matched_keys']}")
    print(f"  only in source        : {m['only_in_source']}")
    print(f"  only in target        : {m['only_in_target']}")
    print(f"  hash matches          : {m['hash_matches']}")
    print(f"  hash mismatches       : {m['hash_mismatches']}")
    print(f"  match rate            : {m['match_rate_pct']}%")


def main():
    ap = argparse.ArgumentParser(description="Compare DB2 recon extracts in a folder")
    ap.add_argument("--config", required=True)
    ap.add_argument("--folder", required=True, help="Folder containing <TABLE>__source/target.csv")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    results = []
    for t in cfg["tables"]:
        m = compare_table(
            t["name"], t["key_columns"],
            t.get("anchor_columns", t["key_columns"]), args.folder,
        )
        print_metrics(m)
        results.append(m)

    # ---- rollups -------------------------------------------------------------
    good = [r for r in results if "status" in r and r["status"] in ("PASS", "BREAK")]
    if good:
        df = pd.DataFrame(good)
        keep = ["run_timestamp", "table", "status", "rows_source", "rows_target",
                "duplicate_keys_source", "duplicate_keys_target", "matched_keys",
                "only_in_source", "only_in_target", "hash_matches",
                "hash_mismatches", "match_rate_pct"]
        df = df[[c for c in keep if c in df.columns]]
        summary_path = os.path.join(args.folder, "recon_summary.csv")
        df.to_csv(summary_path, index=False)
        with open(os.path.join(args.folder, "recon_summary.json"), "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSummary written -> {summary_path}")

    breaks = [r for r in results if r.get("status") not in ("PASS", None)]
    if breaks:
        print(f"\n{len(breaks)} table(s) with issues: {[r['table'] for r in breaks]}")
        sys.exit(2)
    print("\nAll tables PASS.")


if __name__ == "__main__":
    main()
