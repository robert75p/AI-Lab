#!/usr/bin/env python3
"""
db2_extract.py
==============
Extracts a lightweight reconciliation fingerprint from a DB2 table on BOTH the
source and target databases and writes it to an output folder.

For each table it pulls exactly:
    - the key column(s)
    - the traceability anchor column(s)   (ORDER_ID / FILL_ID / CONTRACT_ID ...)
    - ROW_HASH  -- a SHA256 over all *comparable* columns, normalized in-DB2

Because source and target run the SAME DB2 version, the hash is computed
in-database on each side with identical SQL, so the hashes are directly
comparable. Only three columns per row ever leave the database.

Output files (into --output-dir):
    <TABLE>__source.csv        key + anchor + ROW_HASH  (source DB)
    <TABLE>__target.csv        key + anchor + ROW_HASH  (target DB)
    _extract_manifest.json     row counts, timestamps, and the exact SQL used

Usage:
    # Preview the generated SQL without connecting (great for review/sign-off):
    python db2_extract.py --config db2_recon_config.yaml --dry-run

    # Real extract into a dated folder:
    python db2_extract.py --config db2_recon_config.yaml \
        --output-dir ./recon/$(date +%F)

Credentials are read from environment variables named in the config
(user_env / password_env) -- never stored in the YAML.
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. pip install pyyaml")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# SQL generation (pure functions -- unit-testable without a database)
# --------------------------------------------------------------------------- #

NUMERIC_TYPES = {
    "SMALLINT", "INTEGER", "BIGINT", "DECIMAL", "NUMERIC",
    "DECFLOAT", "DOUBLE", "REAL", "FLOAT",
}
DATE_TYPES = {"DATE"}
TIMESTAMP_TYPES = {"TIMESTAMP", "TIMESTMP"}
TIME_TYPES = {"TIME"}
STRING_TYPES = {"CHARACTER", "CHAR", "VARCHAR", "VARCHARACTER", "CLOB",
                "GRAPHIC", "VARGRAPHIC", "DBCLOB", "LONG VARCHAR"}
# Types we refuse to hash silently -- they need explicit handling / exclusion.
UNSUPPORTED_TYPES = {"BLOB", "XML", "BINARY", "VARBINARY"}


def normalize_expr(colname: str, typename: str, decimal_cast: str) -> str:
    """Return a DB2 SQL expression that renders `colname` as a canonical,
    NULL-safe string so that logically-equal values hash identically."""
    t = typename.strip().upper()

    if t in NUMERIC_TYPES:
        # Fixed scale so 300.1 and 300.10 canonicalize identically on both sides.
        return f"COALESCE(RTRIM(CAST(CAST({colname} AS {decimal_cast}) AS VARCHAR(42))), '<NULL>')"
    if t in DATE_TYPES:
        return f"COALESCE(VARCHAR_FORMAT({colname}, 'YYYY-MM-DD'), '<NULL>')"
    if t in TIMESTAMP_TYPES:
        return f"COALESCE(VARCHAR_FORMAT({colname}, 'YYYY-MM-DD HH24:MI:SS.FF6'), '<NULL>')"
    if t in TIME_TYPES:
        return f"COALESCE(CHAR({colname}), '<NULL>')"
    if t in STRING_TYPES:
        # RTRIM strips DB2's CHAR(n) space padding; sentinel for NULLs.
        return f"COALESCE(RTRIM(CAST({colname} AS VARCHAR(32000))), '<NULL>')"
    # Fallback: best-effort string cast, but flag it.
    return f"COALESCE(RTRIM(CAST({colname} AS VARCHAR(32000))), '<NULL>')"


def build_hash_sql(schema, table, columns, key_columns, anchor_columns,
                   exclude_columns, algorithm=2, delimiter="|~|",
                   decimal_cast="DECIMAL(31,8)"):
    """
    columns: ordered list of (colname, typename) from SYSCAT.COLUMNS (by COLNO).
    Returns (sql_string, comparable_columns, warnings).
    """
    warnings = []
    key_set = {c.upper() for c in key_columns}
    excl_set = {c.upper() for c in exclude_columns}

    comparable = []
    for name, tname in columns:
        u = name.upper()
        if u in key_set or u in excl_set:
            continue
        if tname.strip().upper() in UNSUPPORTED_TYPES:
            warnings.append(
                f"{table}.{name} is {tname} -- excluded from hash automatically; "
                f"add it to exclude_columns explicitly or handle separately."
            )
            continue
        comparable.append((name, tname))

    if not comparable:
        warnings.append(f"{table} has NO comparable columns -- hash will be constant.")

    # Deterministic, identical on both sides (same schema/version => same COLNO order).
    parts = [normalize_expr(n, t, decimal_cast) for n, t in comparable]
    concat = f" ||\n            '{delimiter}' || ".join(parts) if parts else "''"

    # Output columns: key columns + any anchor columns not already keys, in a stable order.
    out_cols = list(key_columns)
    for a in anchor_columns:
        if a.upper() not in {c.upper() for c in out_cols}:
            out_cols.append(a)
    select_cols = ",\n    ".join(out_cols)

    sql = (
        f"SELECT\n    {select_cols},\n"
        f"    HEX(HASH(\n            {concat}\n"
        f"    , {algorithm})) AS ROW_HASH\n"
        f"FROM {schema}.{table}"
    )
    return sql, [c[0] for c in comparable], warnings


def build_count_sql(schema, table):
    return f"SELECT COUNT(*) AS ROW_COUNT FROM {schema}.{table}"


def quote_value(val, typename):
    """Render a CSV-sourced (string) value as a DB2 SQL literal, choosing
    quoting based on the column's declared type."""
    t = (typename or "").strip().upper()
    if val is None or val == "":
        return "NULL"
    if t in NUMERIC_TYPES:
        # emit unquoted if it really is numeric, else fall back to quoted
        try:
            float(val)
            return str(val)
        except ValueError:
            pass
    return "'" + str(val).replace("'", "''") + "'"


def build_detail_sql(schema, table, key_columns, key_type_map, key_rows):
    """Full-row pull for a specific set of key tuples.
       key_rows: list of tuples of string values aligned to key_columns."""
    if not key_rows:
        return None
    if len(key_columns) == 1:
        col = key_columns[0]
        vals = ", ".join(quote_value(r[0], key_type_map.get(col, "")) for r in key_rows)
        pred = f"{col} IN ({vals})"
    else:
        cols = "(" + ", ".join(key_columns) + ")"
        tuples = ", ".join(
            "(" + ", ".join(quote_value(v, key_type_map.get(c, ""))
                            for v, c in zip(r, key_columns)) + ")"
            for r in key_rows
        )
        pred = f"{cols} IN ({tuples})"
    return f"SELECT * FROM {schema}.{table} WHERE {pred} ORDER BY {', '.join(key_columns)}"


def build_entity_sql(schema, table, anchor_col, anchor_val, anchor_type):
    return (f"SELECT * FROM {schema}.{table} "
            f"WHERE {anchor_col} = {quote_value(anchor_val, anchor_type)}")


def catalog_sql(schema, table):
    """SQL to fetch ordered (colname, typename) for a table."""
    return (
        "SELECT COLNAME, TYPENAME FROM SYSCAT.COLUMNS "
        f"WHERE TABSCHEMA = '{schema}' AND TABNAME = '{table}' "
        "ORDER BY COLNO"
    )


# --------------------------------------------------------------------------- #
# DB2 connectivity (lazy import so --dry-run works without the driver)
# --------------------------------------------------------------------------- #

def connect(conn_cfg, driver_cfg):
    import jaydebeapi  # lazy: only needed for real extracts
    user = os.environ.get(conn_cfg["user_env"])
    pwd = os.environ.get(conn_cfg["password_env"])
    if not user or not pwd:
        raise RuntimeError(
            f"Set env vars {conn_cfg['user_env']} and {conn_cfg['password_env']} "
            f"for the {conn_cfg.get('label','?')} connection."
        )
    return jaydebeapi.connect(
        driver_cfg["class"], conn_cfg["jdbc_url"], [user, pwd], driver_cfg["jar"]
    )


def fetch_columns(cur, schema, table):
    cur.execute(catalog_sql(schema, table))
    return [(r[0].strip(), r[1].strip()) for r in cur.fetchall()]


def run_query_to_csv(cur, sql, out_path):
    cur.execute(sql)
    col_names = [d[0] for d in cur.description]
    n = 0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(col_names)
        while True:
            rows = cur.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                w.writerow(["" if v is None else v for v in row])
                n += 1
    return n


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def extract_side(cur, side_label, schema, table_cfg, hash_cfg, out_dir, dry_run):
    """Returns (sql, warnings, row_count|None)."""
    name = table_cfg["name"]
    if dry_run:
        # In dry-run we can't read the catalog, so use a placeholder column list
        # only if the config supplies one; otherwise emit a template note.
        columns = [(c, t) for c, t in table_cfg.get("_columns_preview", [])]
        if not columns:
            columns = [("<all comparable columns discovered from SYSCAT.COLUMNS>", "VARCHAR")]
    else:
        columns = fetch_columns(cur, schema, table)  # noqa: F821 (see caller)

    sql, comparable, warnings = build_hash_sql(
        schema, name, columns,
        table_cfg["key_columns"], table_cfg.get("anchor_columns", table_cfg["key_columns"]),
        table_cfg.get("exclude_columns", []),
        algorithm=hash_cfg.get("algorithm", 2),
        delimiter=hash_cfg.get("delimiter", "|~|"),
        decimal_cast=hash_cfg.get("decimal_cast", "DECIMAL(31,8)"),
    )

    if dry_run:
        return sql, warnings, None

    out_path = os.path.join(out_dir, f"{name}__{side_label}.csv")
    n = run_query_to_csv(cur, sql, out_path)
    return sql, warnings, n


# --------------------------------------------------------------------------- #
# Detail mode: re-pull FULL rows for broken keys and produce a cell-level diff
# --------------------------------------------------------------------------- #

BREAK_FILE_SUFFIXES = ("__hash_mismatch.csv", "__only_in_source.csv", "__only_in_target.csv")


def read_break_keys(folder, table, key_columns):
    """Collect the distinct broken key tuples for a table from the break files
    that recon_compare.py wrote into `folder`. Returns list of tuples (strings)."""
    import pandas as pd
    key_upper = [k.upper() for k in key_columns]
    seen = set()
    rows = []
    for suffix in BREAK_FILE_SUFFIXES:
        path = os.path.join(folder, f"{table}{suffix}")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
        df.columns = [c.strip().upper() for c in df.columns]
        if not all(k in df.columns for k in key_upper):
            continue
        for _, r in df[key_upper].iterrows():
            t = tuple(r[k] for k in key_upper)
            if t not in seen:
                seen.add(t)
                rows.append(t)
    return rows


def cell_diff(table, key_columns, exclude_columns, src_df, tgt_df, tolerance=None):
    """Long-format cell-level diff of two full-row DataFrames on matched keys.
    Returns (diff_df, summary_dict)."""
    import pandas as pd

    key_columns = [k.upper() for k in key_columns]
    excl = {c.upper() for c in exclude_columns}
    src_df = src_df.rename(columns=str.upper)
    tgt_df = tgt_df.rename(columns=str.upper)

    compare_cols = [c for c in src_df.columns
                    if c in tgt_df.columns and c not in key_columns and c not in excl]

    merged = src_df.merge(tgt_df, on=key_columns, how="outer",
                          suffixes=("_SRC", "_TGT"), indicator=True)

    def eq(a, b):
        a_na = a is None or (isinstance(a, float) and pd.isna(a)) or a == ""
        b_na = b is None or (isinstance(b, float) and pd.isna(b)) or b == ""
        if a_na and b_na:
            return True
        if a_na != b_na:
            return False
        a_s, b_s = str(a).strip(), str(b).strip()
        if tolerance is not None:
            try:
                return abs(float(a_s) - float(b_s)) <= tolerance
            except ValueError:
                pass
        return a_s == b_s

    diff_rows = []
    for _, row in merged.iterrows():
        keyvals = {k: row[k] for k in key_columns}
        if row["_merge"] == "left_only":
            diff_rows.append({**keyvals, "column": "<ROW>", "value_source": "PRESENT",
                              "value_target": "MISSING"})
            continue
        if row["_merge"] == "right_only":
            diff_rows.append({**keyvals, "column": "<ROW>", "value_source": "MISSING",
                              "value_target": "PRESENT"})
            continue
        for col in compare_cols:
            va = row.get(f"{col}_SRC", row.get(col))
            vb = row.get(f"{col}_TGT", row.get(col))
            if not eq(va, vb):
                diff_rows.append({**keyvals, "column": col,
                                  "value_source": va, "value_target": vb})

    diff_df = pd.DataFrame(diff_rows)
    summary = {
        "matched_keys": int((merged["_merge"] == "both").sum()),
        "only_in_source": int((merged["_merge"] == "left_only").sum()),
        "only_in_target": int((merged["_merge"] == "right_only").sum()),
        "cell_diffs": int(len([d for d in diff_rows if d["column"] != "<ROW>"])),
        "columns_compared": len(compare_cols),
    }
    return diff_df, summary


def _pull_detail(cur, sql, ):
    """Run a SELECT * and return a DataFrame."""
    import pandas as pd
    cur.execute(sql)
    cols = [d[0].upper() for d in cur.description]
    data = cur.fetchall()
    return pd.DataFrame(
        [["" if v is None else v for v in row] for row in data], columns=cols
    ).astype(str)


def run_detail_keys(cfg, folder, only_table, tolerance):
    """For each configured table (or just --only-table), read broken keys from
    the folder's break files, re-pull full rows from both DB2s, write detail
    CSVs and a cell-level diff back into the folder."""
    import pandas as pd
    driver_cfg = cfg["driver"]
    tables = cfg["tables"]
    if only_table:
        tables = [t for t in tables if t["name"] == only_table]

    conns, curs = {}, {}
    produced = []
    try:
        for side in ("source", "target"):
            c = dict(cfg["connections"][side]); c["label"] = side
            conns[side] = connect(c, driver_cfg)
            curs[side] = conns[side].cursor()

        for t in tables:
            name = t["name"]
            keys = t["key_columns"]
            break_keys = read_break_keys(folder, name, keys)
            if not break_keys:
                continue

            # need key column types for correct literal quoting
            src_schema = cfg["connections"]["source"]["schema"]
            tgt_schema = cfg["connections"]["target"]["schema"]
            col_types = {c.upper(): tp for c, tp in fetch_columns(curs["source"], src_schema, name)}
            key_type_map = {k: col_types.get(k.upper(), "") for k in keys}

            frames = {}
            for side, schema in (("source", src_schema), ("target", tgt_schema)):
                # batch the IN-list to stay well under DB2 statement limits
                dfs = []
                for i in range(0, len(break_keys), 500):
                    chunk = break_keys[i:i + 500]
                    sql = build_detail_sql(schema, name, keys, key_type_map, chunk)
                    dfs.append(_pull_detail(curs[side], sql))
                df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
                df.to_csv(os.path.join(folder, f"{name}__detail_{side}.csv"), index=False)
                frames[side] = df

            diff_df, summary = cell_diff(
                name, keys, t.get("exclude_columns", []),
                frames["source"], frames["target"], tolerance,
            )
            if not diff_df.empty:
                # attach anchors for traceability where present
                diff_df.to_csv(os.path.join(folder, f"{name}__detail_diff.csv"), index=False)
            with open(os.path.join(folder, f"{name}__detail_summary.json"), "w") as f:
                json.dump({"table": name, "broken_keys_investigated": len(break_keys),
                           **summary}, f, indent=2)
            produced.append((name, len(break_keys), summary["cell_diffs"]))
            print(f"  {name}: {len(break_keys)} broken key(s) -> "
                  f"{summary['cell_diffs']} cell diff(s), "
                  f"{summary['only_in_source']} src-only, {summary['only_in_target']} tgt-only")
    finally:
        for cur in curs.values():
            try: cur.close()
            except Exception: pass
        for cn in conns.values():
            try: cn.close()
            except Exception: pass

    if not produced:
        print("No break files with keys found in folder -- nothing to detail.")
    else:
        print(f"\nDetail written -> {folder} ("
              f"{sum(p[2] for p in produced)} total cell diffs across "
              f"{len(produced)} table(s))")


def run_detail_entity(cfg, folder, anchor_spec, tolerance):
    """Pull full rows for a single business entity (e.g. ORDER_ID=100234) across
    every table whose anchor_columns include that anchor, from both DB2s, and
    cell-diff each. Great for investigating one broken order/fill/contract."""
    import pandas as pd
    if "=" not in anchor_spec:
        print("--detail-entity must look like ANCHOR=VALUE, e.g. ORDER_ID=100234")
        sys.exit(1)
    anchor_col, anchor_val = anchor_spec.split("=", 1)
    anchor_col = anchor_col.strip().upper()
    anchor_val = anchor_val.strip()

    driver_cfg = cfg["driver"]
    conns, curs = {}, {}
    os.makedirs(folder, exist_ok=True)
    try:
        for side in ("source", "target"):
            c = dict(cfg["connections"][side]); c["label"] = side
            conns[side] = connect(c, driver_cfg)
            curs[side] = conns[side].cursor()

        hit = 0
        for t in cfg["tables"]:
            anchors = [a.upper() for a in t.get("anchor_columns", t["key_columns"])]
            if anchor_col not in anchors:
                continue
            name = t["name"]
            src_schema = cfg["connections"]["source"]["schema"]
            tgt_schema = cfg["connections"]["target"]["schema"]
            col_types = {c.upper(): tp for c, tp in fetch_columns(curs["source"], src_schema, name)}
            atype = col_types.get(anchor_col, "")

            frames = {}
            for side, schema in (("source", src_schema), ("target", tgt_schema)):
                sql = build_entity_sql(schema, name, anchor_col, anchor_val, atype)
                df = _pull_detail(curs[side], sql)
                df.to_csv(os.path.join(folder, f"{name}__entity_{side}.csv"), index=False)
                frames[side] = df

            diff_df, summary = cell_diff(
                name, t["key_columns"], t.get("exclude_columns", []),
                frames["source"], frames["target"], tolerance,
            )
            if not diff_df.empty:
                diff_df.to_csv(os.path.join(folder, f"{name}__entity_diff.csv"), index=False)
            status = "clean" if (summary["cell_diffs"] == 0 and summary["only_in_source"] == 0
                                 and summary["only_in_target"] == 0) else "DIFFERS"
            print(f"  {name}: src rows={len(frames['source'])} tgt rows={len(frames['target'])} "
                  f"-> {status} ({summary['cell_diffs']} cell diffs)")
            hit += 1
        if hit == 0:
            print(f"No configured table carries anchor '{anchor_col}'.")
        else:
            print(f"\nEntity detail for {anchor_col}={anchor_val} written -> {folder}")
    finally:
        for cur in curs.values():
            try: cur.close()
            except Exception: pass
        for cn in conns.values():
            try: cn.close()
            except Exception: pass


def main():
    ap = argparse.ArgumentParser(description="Extract key+anchor+row-hash from source and target DB2")
    ap.add_argument("--config", required=True)
    ap.add_argument("--output-dir", default="./recon_output")
    ap.add_argument("--dry-run", action="store_true", help="Print generated SQL; do not connect")
    ap.add_argument("--only-table", help="Restrict to one table by name")
    ap.add_argument("--folder", help="Folder holding break files / where detail output goes (detail modes)")
    ap.add_argument("--detail-keys", action="store_true",
                    help="Re-pull FULL rows for broken keys found in --folder's break files, "
                         "and write a cell-level diff back into --folder")
    ap.add_argument("--detail-entity",
                    help="Investigate one entity across all tables carrying that anchor, "
                         "e.g. --detail-entity ORDER_ID=100234")
    ap.add_argument("--tolerance", type=float, default=None,
                    help="Absolute numeric tolerance for cell-level diff equality")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    # ---- DETAIL MODES ----------------------------------------------------------
    if args.detail_keys or args.detail_entity:
        if not args.folder:
            print("--folder is required for detail modes (the dated recon folder).")
            sys.exit(1)
        if args.detail_entity:
            run_detail_entity(cfg, args.folder, args.detail_entity, args.tolerance)
        else:
            run_detail_keys(cfg, args.folder, args.only_table, args.tolerance)
        return

    hash_cfg = cfg.get("hash", {})
    driver_cfg = cfg.get("driver", {})
    tables = cfg["tables"]
    if args.only_table:
        tables = [t for t in tables if t["name"] == args.only_table]
        if not tables:
            print(f"No table named {args.only_table} in config"); sys.exit(1)

    manifest = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "output_dir": os.path.abspath(args.output_dir),
        "tables": {},
    }
    all_warnings = []

    # ---- DRY RUN: just show SQL ------------------------------------------------
    if args.dry_run:
        for t in tables:
            for side in ("source", "target"):
                schema = cfg["connections"][side]["schema"]
                sql, warnings, _ = extract_side(None, side, schema, t, hash_cfg, args.output_dir, True)
                print(f"\n{'='*70}\n-- {t['name']}  [{side}]  ({schema})\n{'='*70}\n{sql};")
                all_warnings += warnings
        if all_warnings:
            print("\n-- WARNINGS --")
            for w in sorted(set(all_warnings)):
                print(f"   * {w}")
        return

    # ---- REAL EXTRACT ----------------------------------------------------------
    os.makedirs(args.output_dir, exist_ok=True)
    conns = {}
    curs = {}
    try:
        for side in ("source", "target"):
            c = cfg["connections"][side]
            c["label"] = side
            conns[side] = connect(c, driver_cfg)
            curs[side] = conns[side].cursor()

        for t in tables:
            entry = {}
            # Optional integrity check: identical column sets on both sides.
            src_cols = fetch_columns(curs["source"], cfg["connections"]["source"]["schema"], t["name"])
            tgt_cols = fetch_columns(curs["target"], cfg["connections"]["target"]["schema"], t["name"])
            if [c[0].upper() for c in src_cols] != [c[0].upper() for c in tgt_cols]:
                all_warnings.append(f"{t['name']}: SCHEMA DRIFT -- column sets differ between source and target!")

            for side in ("source", "target"):
                schema = cfg["connections"][side]["schema"]
                # reuse the fetched columns to avoid a second catalog query
                cols = src_cols if side == "source" else tgt_cols
                sql, warnings, _ = build_and_run(curs[side], schema, t, hash_cfg, cols, side, args.output_dir)
                entry[f"{side}_sql"] = sql
                entry[f"{side}_rows"] = _
                all_warnings += warnings
            manifest["tables"][t["name"]] = entry
            print(f"  extracted {t['name']}: source={entry['source_rows']} target={entry['target_rows']}")

        with open(os.path.join(args.output_dir, "_extract_manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        if all_warnings:
            print("\nWARNINGS:")
            for w in sorted(set(all_warnings)):
                print(f"  * {w}")
        print(f"\nExtract complete -> {args.output_dir}")
    finally:
        for cur in curs.values():
            try: cur.close()
            except Exception: pass
        for cn in conns.values():
            try: cn.close()
            except Exception: pass


def build_and_run(cur, schema, table_cfg, hash_cfg, columns, side, out_dir):
    sql, comparable, warnings = build_hash_sql(
        schema, table_cfg["name"], columns,
        table_cfg["key_columns"], table_cfg.get("anchor_columns", table_cfg["key_columns"]),
        table_cfg.get("exclude_columns", []),
        algorithm=hash_cfg.get("algorithm", 2),
        delimiter=hash_cfg.get("delimiter", "|~|"),
        decimal_cast=hash_cfg.get("decimal_cast", "DECIMAL(31,8)"),
    )
    out_path = os.path.join(out_dir, f"{table_cfg['name']}__{side}.csv")
    n = run_query_to_csv(cur, sql, out_path)
    return sql, warnings, n


if __name__ == "__main__":
    main()
