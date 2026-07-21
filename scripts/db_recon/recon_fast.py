#!/usr/bin/env python3
"""
recon_fast.py
=============
Efficient reconciliation for wide, large DB2 tables + interactive HTML report.

Three efficiency techniques layered on top of the row-hash approach:

  1. BUCKET (Merkle-style) HASHING
     Rows are assigned to N buckets by a stable hash of the key. Each bucket's
     row-hashes are folded into one bucket hash. Compare bucket hashes first;
     only descend into buckets that differ. On 10M rows with a handful of
     breaks you compare ~1024 bucket hashes instead of 10M row hashes, and only
     pull row detail for the 1-2 buckets that actually differ.

  2. COLUMN-GROUP HASHING
     Instead of one hash per row, hash G groups of columns. When a row breaks,
     the group hashes say WHICH group differs, so the detail pull fetches a
     slice of columns rather than all 247.

  3. COLUMN PRUNING
     Columns empty on both sides (170 of 247 in the sample data) carry no
     signal. Profile once, exclude them from the hash, and record the decision.

Usage (CSV mode -- works on extract files):
    python recon_fast.py --source src.csv --target tgt.csv \
        --keys FILL_ID,FILL_VID --table FILL --out-dir ./recon/2026-07-16

Produces in --out-dir:
    <TABLE>_recon.html      self-contained interactive report (search + colour diff)
    <TABLE>_breaks.csv      long-format break list
    <TABLE>_metrics.json    metrics incl. bucket/column-group statistics
"""

import argparse
import hashlib
import re
import json
import os
from datetime import datetime, timezone

import pandas as pd


# --------------------------------------------------------------------------- #
# Hashing primitives
# --------------------------------------------------------------------------- #

def norm(v):
    """Canonical string for a value: NULL-safe, whitespace-stable."""
    if v is None:
        return "<NULL>"
    s = str(v).strip()
    return s if s != "" else "<NULL>"


def row_hash(values):
    return hashlib.sha256("|~|".join(norm(v) for v in values).encode()).hexdigest()


def bucket_of(key_tuple, n_buckets):
    h = hashlib.sha256("|".join(norm(k) for k in key_tuple).encode()).hexdigest()
    return int(h[:8], 16) % n_buckets


def fold(hashes):
    """Order-independent fold of many hashes into one (XOR of digests)."""
    acc = 0
    for h in hashes:
        acc ^= int(h[:32], 16)
    return f"{acc:032x}"


def column_groups(columns, n_groups):
    """Stable, contiguous split of columns into n_groups."""
    if n_groups <= 1:
        return {0: list(columns)}
    size = max(1, (len(columns) + n_groups - 1) // n_groups)
    return {i // size: columns[i:i + size] for i in range(0, len(columns), size)}


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

# Derived display column, defined by a template. Placeholders in {BRACES} are
# column names; any column missing from the table renders as '-' and is
# reported in stats["flow_missing_columns"] rather than failing silently.
DEFAULT_FLOW_TEMPLATE = "{APP_CODE}-> {DEST_ID} -> {SUB_DEST_ID} -> {MIC_CODE}"
DEFAULT_FLOW_LABEL = "FLOW"
FLOW_ANCHOR = "MIC_CODE"          # derived column is inserted before this one

# Candidate context columns, shown in the report whether or not they differ.
# Only those actually present in the table are used, so one tool serves every
# table in the migration.
CONTEXT_CANDIDATES = [
    "FLOW", "ORDER_FLOW", "ORIGINATING_REGION", "TRANS_TYPE_CODE", "APP_CODE",
    "ORDER_MGMT_SYS_CODE", "EXEC_ID", "PROCESS_OWNER_ID", "TRADE_MSG_ID",
    "TRADE_MSG_VID", "SIDE", "ORDER_QTY", "CUM_QTY", "AVG_PRICE", "LEAVES_QTY",
    "RESIDUAL_QTY", "TIME_IN_FORCE", "FILL_ACTION_TYPE_CODE", "TRANSACT_TIME",
]

# Key columns matching these suffixes are treated as VERSION components; the
# remaining key columns form the row's stable IDENTITY. Used to pair up an
# orphan on each side that is really one record at two different versions.
VERSION_SUFFIXES = ("_VID", "_VERSION", "_VER")


def split_identity(keys):
    ident = [k for k in keys if not k.upper().endswith(VERSION_SUFFIXES)]
    vers = [k for k in keys if k.upper().endswith(VERSION_SUFFIXES)]
    return (ident, vers) if ident and vers else (list(keys), [])


def resolve_context(columns, requested=None):
    cols = {c.upper() for c in columns}
    cand = requested if requested else CONTEXT_CANDIDATES
    return [c for c in cand if c in cols]


def add_flow(df, template=DEFAULT_FLOW_TEMPLATE, label=DEFAULT_FLOW_LABEL,
             anchor=FLOW_ANCHOR):
    """Insert a derived display column built from `template`.
    Placeholders like {APP_CODE} are substituted per row; empty or absent
    columns render as '-' so the chain stays readable and gaps are visible.
    Returns (df, ok, missing_columns)."""
    import re as _re
    parts = _re.findall(r"\{([A-Za-z0-9_]+)\}", template)
    if not parts:
        return df, False, []
    missing = [c for c in parts if c not in df.columns]
    present = [c for c in parts if c in df.columns]
    # A partially-built routing chain (e.g. "RAZER-> - -> - -> -") is noise, not
    # information -- only build the derived column when every part exists.
    if missing:
        return df, False, missing

    def seg(v):
        s = "" if v is None else str(v).strip()
        return s if s else "-"

    series = {c: df[c].map(seg) for c in present}
    out = template
    # build by successive replacement on a string Series
    acc = None
    literal = _re.split(r"\{[A-Za-z0-9_]+\}", template)
    order = _re.findall(r"\{([A-Za-z0-9_]+)\}", template)
    acc = pd.Series([literal[0]] * len(df), index=df.index)
    for i, col in enumerate(order):
        acc = acc + (series[col] if col in series else "-")
        acc = acc + literal[i + 1]

    cols = list(df.columns)
    if label in cols:
        df = df.drop(columns=[label]); cols = list(df.columns)
    pos = cols.index(anchor) if anchor in cols else 0
    df = df.copy()
    df.insert(pos, label, acc)
    return df, True, missing


def compare(src, tgt, keys, n_buckets=256, n_groups=8, prune_empty=True,
            context_request=None, pair_orphans=True,
            flow_template=DEFAULT_FLOW_TEMPLATE, flow_label=DEFAULT_FLOW_LABEL,
            flow_anchor=FLOW_ANCHOR):
    stats = {}
    src.columns = [c.strip().upper() for c in src.columns]
    tgt.columns = [c.strip().upper() for c in tgt.columns]
    keys = [k.strip().upper() for k in keys]

    src, ok_s, miss_s = add_flow(src, flow_template, flow_label, flow_anchor)
    tgt, ok_t, _ = add_flow(tgt, flow_template, flow_label, flow_anchor)
    stats["flow_column_added"] = bool(ok_s and ok_t)
    stats["flow_label"] = flow_label if (ok_s and ok_t) else None
    stats["flow_template"] = flow_template if (ok_s and ok_t) else None
    stats["flow_missing_columns"] = miss_s

    all_cols = [c for c in src.columns if c in tgt.columns]
    # The derived column comes purely from columns already compared, so it is
    # DISPLAYED but excluded from the hash -- otherwise one underlying break
    # would be counted twice.
    compare_cols = [c for c in all_cols if c not in keys and c != flow_label]

    # --- 3. column pruning -------------------------------------------------
    pruned = []
    if prune_empty:
        for c in list(compare_cols):
            if (src[c].map(norm) == "<NULL>").all() and (tgt[c].map(norm) == "<NULL>").all():
                pruned.append(c)
        compare_cols = [c for c in compare_cols if c not in pruned]
    stats["columns_total"] = len(all_cols)
    stats["columns_pruned_empty"] = len(pruned)
    stats["columns_compared"] = len(compare_cols)

    groups = column_groups(compare_cols, n_groups)
    stats["column_groups"] = len(groups)
    ctx_cols = resolve_context(src.columns, context_request)
    stats["context_columns"] = ctx_cols
    identity, version_cols = split_identity(keys)
    stats["identity_columns"] = identity
    stats["version_columns"] = version_cols

    # --- build per-row fingerprints ---------------------------------------
    def fingerprint(df):
        idx = {}
        for _, r in df.iterrows():
            kt = tuple(norm(r[k]) for k in keys)
            grp = {gi: row_hash([r[c] for c in cols]) for gi, cols in groups.items()}
            idx[kt] = {"row": row_hash([r[c] for c in compare_cols]), "grp": grp}
        return idx

    fs, ft = fingerprint(src), fingerprint(tgt)

    # --- 1. bucket hashing: find candidate buckets -------------------------
    def bucket_map(fp):
        b = {}
        for kt, v in fp.items():
            b.setdefault(bucket_of(kt, n_buckets), []).append(v["row"])
        return {k: fold(v) for k, v in b.items()}

    bs, bt = bucket_map(fs), bucket_map(ft)
    all_buckets = set(bs) | set(bt)
    dirty = {b for b in all_buckets if bs.get(b) != bt.get(b)}
    stats["buckets_total"] = len(all_buckets)
    stats["buckets_dirty"] = len(dirty)
    stats["buckets_skipped_pct"] = round(
        100.0 * (len(all_buckets) - len(dirty)) / max(len(all_buckets), 1), 2)

    # --- descend only into dirty buckets ----------------------------------
    ks, kt_ = set(fs), set(ft)
    only_src = sorted(k for k in ks - kt_ if bucket_of(k, n_buckets) in dirty)
    only_tgt = sorted(k for k in kt_ - ks if bucket_of(k, n_buckets) in dirty)
    common = ks & kt_
    candidates = [k for k in common if bucket_of(k, n_buckets) in dirty]
    stats["rows_source"] = len(src)
    stats["rows_target"] = len(tgt)
    stats["matched_keys"] = len(common)
    stats["only_in_source"] = len(only_src)
    stats["only_in_target"] = len(only_tgt)
    stats["rows_inspected_after_bucket_filter"] = len(candidates)
    stats["rows_skipped_by_bucket_filter"] = len(common) - len(candidates)

    # --- 2. column-group narrowing then cell diff -------------------------
    src_i = src.set_index([*keys]) if len(keys) > 1 else src.set_index(keys[0])
    tgt_i = tgt.set_index([*keys]) if len(keys) > 1 else tgt.set_index(keys[0])

    breaks = []
    broken_keys = []
    group_hits = {}
    for kt in candidates:
        if fs[kt]["row"] == ft[kt]["row"]:
            continue
        diff_groups = [gi for gi in groups if fs[kt]["grp"][gi] != ft[kt]["grp"][gi]]
        for gi in diff_groups:
            group_hits[gi] = group_hits.get(gi, 0) + 1
        narrowed = [c for gi in diff_groups for c in groups[gi]]

        lookup = kt if len(keys) > 1 else kt[0]
        rs, rt = src_i.loc[lookup], tgt_i.loc[lookup]
        if isinstance(rs, pd.DataFrame):
            rs = rs.iloc[0]
        if isinstance(rt, pd.DataFrame):
            rt = rt.iloc[0]

        row_diffs = []
        for c in narrowed:
            a, b = norm(rs[c]), norm(rt[c])
            if a != b:
                row_diffs.append({"column": c, "source": a, "target": b})
        # FLOW is derived, so check it separately for display purposes.
        if flow_label in src.columns and norm(rs[flow_label]) != norm(rt[flow_label]):
            row_diffs.insert(0, {"column": flow_label, "source": norm(rs[flow_label]),
                                 "target": norm(rt[flow_label]), "derived": True})
        if row_diffs:
            ctx = []
            keymap = dict(zip(keys, kt))
            for c in ctx_cols:
                if c in keymap:                      # key cols live in the index
                    v = keymap[c]
                    ctx.append({"column": c, "source": v, "target": v, "differs": False})
                elif c in src.columns:
                    a, b = norm(rs[c]), norm(rt[c])
                    ctx.append({"column": c, "source": a, "target": b, "differs": a != b})
            broken_keys.append({"key": list(kt), "diffs": row_diffs,
                                "context": ctx, "groups": diff_groups})
            for d in row_diffs:
                breaks.append({**dict(zip(keys, kt)), **d, "type": "VALUE_DIFF"})

    # --- orphan pairing: is a source-only + target-only pair really ONE record
    #     captured at two different versions? Pair on the identity columns. ----
    paired = []
    unpaired_src, unpaired_tgt = list(only_src), list(only_tgt)
    if pair_orphans and version_cols and only_src and only_tgt:
        kpos = {k: i for i, k in enumerate(keys)}
        ipos = [kpos[c] for c in identity]

        def ident_of(kt):
            return tuple(kt[i] for i in ipos)

        tgt_by_ident = {}
        for k in only_tgt:
            tgt_by_ident.setdefault(ident_of(k), []).append(k)

        still_src, matched_tgt = [], set()
        for ks_ in only_src:
            cands = tgt_by_ident.get(ident_of(ks_), [])
            cand = next((c for c in cands if c not in matched_tgt), None)
            if cand is None:
                still_src.append(ks_)
                continue
            matched_tgt.add(cand)
            rs = src_i.loc[ks_ if len(keys) > 1 else ks_[0]]
            rt = tgt_i.loc[cand if len(keys) > 1 else cand[0]]
            if isinstance(rs, pd.DataFrame):
                rs = rs.iloc[0]
            if isinstance(rt, pd.DataFrame):
                rt = rt.iloc[0]
            fdiffs = []
            for c in compare_cols:
                a, b = norm(rs[c]), norm(rt[c])
                if a != b:
                    fdiffs.append({"column": c, "source": a, "target": b})
            vdiff = [{"column": k, "source": ks_[kpos[k]], "target": cand[kpos[k]]}
                     for k in version_cols if ks_[kpos[k]] != cand[kpos[k]]]
            ctx = []
            for c in ctx_cols:
                if c in src.columns:
                    a, b = norm(rs[c]), norm(rt[c])
                    ctx.append({"column": c, "source": a, "target": b, "differs": a != b})
            paired.append({"key": list(ks_), "target_key": list(cand),
                           "version_diffs": vdiff, "diffs": fdiffs, "context": ctx})
        unpaired_src = still_src
        unpaired_tgt = [k for k in only_tgt if k not in matched_tgt]

    stats["version_mismatched_pairs"] = len(paired)
    stats["only_in_source"] = len(unpaired_src)
    stats["only_in_target"] = len(unpaired_tgt)

    for kt in unpaired_src:
        breaks.append({**dict(zip(keys, kt)), "column": "<ROW>",
                       "source": "PRESENT", "target": "MISSING", "type": "ONLY_IN_SOURCE"})
    for kt in unpaired_tgt:
        breaks.append({**dict(zip(keys, kt)), "column": "<ROW>",
                       "source": "PRESENT" if False else "MISSING",
                       "target": "PRESENT", "type": "ONLY_IN_TARGET"})
    for p in paired:
        vd = "; ".join(f"{d['column']} {d['source']}->{d['target']}" for d in p["version_diffs"])
        breaks.append({**dict(zip(keys, p["key"])), "column": "<VERSION>",
                       "source": vd.split("->")[0] if vd else "",
                       "target": vd.split("->")[-1] if vd else "",
                       "type": "VERSION_MISMATCH"})
        for d in p["diffs"]:
            breaks.append({**dict(zip(keys, p["key"])), **d, "type": "VERSION_MISMATCH_FIELD"})

    stats["rows_with_diffs"] = len(broken_keys)
    # derived columns don't count as independent breaks
    stats["cell_diffs"] = sum(
        len([d for d in b["diffs"] if not d.get("derived")]) for b in broken_keys)
    stats["column_group_hits"] = group_hits
    stats["match_rate_pct"] = round(
        100.0 * (stats["matched_keys"] - stats["rows_with_diffs"]) / max(stats["matched_keys"], 1), 4)
    stats["status"] = "PASS" if (not breaks) else "BREAK"

    return (stats, breaks, broken_keys, compare_cols, pruned, groups,
            paired, unpaired_src, unpaired_tgt, ctx_cols)


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TABLE__ reconciliation - __DATE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#0F1319; --panel:#161B23; --panel2:#1C222C; --rule:#28303B;
  --tx:#C9D2DC; --tx-dim:#75818F; --tx-bright:#EAF0F6;
  --src:#4FA3D1; --tgt:#E0A140; --break:#E5484D; --ok:#3DAE7C;
  --src-bg:rgba(79,163,209,.13); --tgt-bg:rgba(224,161,64,.13);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--tx);
  font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:14px;line-height:1.5}
code,.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}

header{border-bottom:1px solid var(--rule);padding:20px 24px;background:var(--panel)}
.back{color:var(--src);text-decoration:none;font-size:12px;font-family:'IBM Plex Mono',monospace}
.back:hover{text-decoration:underline}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--tx-dim)}
h1{margin:6px 0 2px;font-size:22px;font-weight:600;color:var(--tx-bright);letter-spacing:-.01em}
.sub{color:var(--tx-dim);font-size:13px}
.badge{display:inline-block;padding:3px 10px;border-radius:3px;font-family:'IBM Plex Mono',monospace;
  font-size:11px;font-weight:600;letter-spacing:.08em;vertical-align:middle;margin-left:10px}
.badge.break{background:rgba(229,72,77,.15);color:var(--break);border:1px solid rgba(229,72,77,.4)}
.badge.pass{background:rgba(61,174,124,.15);color:var(--ok);border:1px solid rgba(61,174,124,.4)}

.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:1px;background:var(--rule);border-bottom:1px solid var(--rule)}
.metric{background:var(--panel);padding:12px 16px}
.metric .v{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:var(--tx-bright)}
.metric .v.alert{color:var(--break)} .metric .v.good{color:var(--ok)}
.metric .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);margin-top:2px}

.wrap{display:grid;grid-template-columns:340px 1fr;min-height:calc(100vh - 190px)}
@media(max-width:860px){.wrap{grid-template-columns:1fr}}
aside{border-right:1px solid var(--rule);background:var(--panel);overflow-y:auto;max-height:calc(100vh - 190px)}
.searchbox{padding:12px;border-bottom:1px solid var(--rule);position:sticky;top:0;background:var(--panel);z-index:2}
input[type=search]{width:100%;padding:9px 11px;background:var(--ink);border:1px solid var(--rule);
  border-radius:3px;color:var(--tx-bright);font-family:'IBM Plex Mono',monospace;font-size:12px}
input[type=search]:focus{outline:2px solid var(--src);outline-offset:-1px;border-color:transparent}
.hint{font-size:11px;color:var(--tx-dim);margin-top:7px}

.fill{padding:11px 14px;border-bottom:1px solid var(--rule);cursor:pointer;border-left:2px solid transparent}
.fill:hover{background:var(--panel2)}
.fill.active{background:var(--panel2);border-left-color:var(--break)}
.fill:focus-visible{outline:2px solid var(--src);outline-offset:-2px}
.fill .id{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--tx-bright);
  word-break:break-all;line-height:1.35}
.fill .meta{font-size:11px;color:var(--tx-dim);margin-top:4px;display:flex;gap:8px;align-items:center}
.tag{font-family:'IBM Plex Mono',monospace;font-size:9.5px;padding:1px 6px;border-radius:2px;letter-spacing:.05em}
.tag.diff{background:rgba(229,72,77,.15);color:var(--break)}
.tag.src{background:var(--src-bg);color:var(--src)}
.tag.tgt{background:var(--tgt-bg);color:var(--tgt)}

/* signature: column ribbon -- 247 ticks, lit where the row differs */
.ribbon{display:flex;gap:1px;margin-top:7px;height:9px}
.ribbon i{flex:1;background:var(--rule);border-radius:1px}
.ribbon i.hit{background:var(--break);box-shadow:0 0 5px rgba(229,72,77,.8)}

main{padding:22px 26px;overflow-y:auto;max-height:calc(100vh - 190px)}
.empty{color:var(--tx-dim);text-align:center;padding:60px 20px}
.keyhdr{font-family:'IBM Plex Mono',monospace;font-size:15px;color:var(--tx-bright);
  word-break:break-all;margin-bottom:3px}
.keysub{color:var(--tx-dim);font-size:12px;margin-bottom:18px}
.ribbon-lg{display:flex;gap:1px;height:16px;margin:14px 0 20px}
.ribbon-lg i{flex:1;background:var(--rule);border-radius:1px}
.ribbon-lg i.hit{background:var(--break);box-shadow:0 0 7px rgba(229,72,77,.9)}
.ribbon-cap{font-size:11px;color:var(--tx-dim);font-family:'IBM Plex Mono',monospace;margin-bottom:18px}

table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;padding:8px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);
  border-bottom:1px solid var(--rule);font-weight:500}
th.s{color:var(--src)} th.t{color:var(--tgt)}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top;
  font-family:'IBM Plex Mono',monospace;word-break:break-all}
td.col{color:var(--tx-bright);font-weight:500}
td.sv{background:var(--src-bg);color:var(--src)}
td.tv{background:var(--tgt-bg);color:var(--tgt)}
mark{background:rgba(229,72,77,.35);color:var(--tx-bright);padding:0 1px;border-radius:2px}
/* composite key: every key part labelled, source of truth for navigation */
.keychips{display:flex;flex-wrap:wrap;gap:7px;margin:2px 0 6px}
.kc{background:var(--panel2);border:1px solid var(--rule);border-radius:3px;padding:4px 9px;min-width:0}
.kc .kn{font-size:9px;letter-spacing:.09em;text-transform:uppercase;color:var(--tx-dim);display:block}
.kc .kv{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tx-bright);
  word-break:break-all;display:block;margin-top:1px}
.kc.ver{border-color:rgba(224,161,64,.5)}
.kc.ver .kv{color:var(--tgt)}
.kc.vdiff{border-color:var(--break)} .kc.vdiff .kv{color:var(--break)}
.minikeys{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--tx-dim);
  margin-top:3px;line-height:1.45;word-break:break-all}
.minikeys b{color:var(--tx-bright);font-weight:500}
.vnote{background:rgba(224,161,64,.09);border:1px solid rgba(224,161,64,.35);border-radius:4px;
  padding:11px 14px;margin-bottom:18px;font-size:12.5px;color:var(--tx)}
.vnote strong{color:var(--tgt)}
.tag.ver{background:var(--tgt-bg);color:var(--tgt)}
/* context block: what this fill IS, shown regardless of differences */
.ctx{background:var(--panel);border:1px solid var(--rule);border-radius:4px;
  padding:14px 16px;margin-bottom:20px}
.ctx h2{margin:0 0 11px;font-family:'IBM Plex Mono',monospace;font-size:10px;
  letter-spacing:.13em;text-transform:uppercase;color:var(--tx-dim);font-weight:500}
.ctxgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 22px}
.ctxi{min-width:0}
.ctxi .k{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--tx-dim)}
.ctxi .v{font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--tx-bright);
  word-break:break-all;margin-top:2px}
.ctxi.d .v{color:var(--break)}
.ctxi .v .s{color:var(--src)} .ctxi .v .t{color:var(--tgt)}
.ctxi .v .arrow{color:var(--tx-dim);padding:0 5px}
.ctxi.flow{grid-column:1/-1}
.ctxi.flow .v{font-size:13.5px}
.flowline{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--tx-dim);
  margin-top:5px;word-break:break-all}
.flowline .hitseg{color:var(--break)}
.toggle{margin-bottom:14px}
button{background:var(--panel2);border:1px solid var(--rule);color:var(--tx);padding:6px 12px;
  border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:11px;cursor:pointer}
button:hover{border-color:var(--src);color:var(--tx-bright)}
button:focus-visible{outline:2px solid var(--src)}
.legend{display:flex;gap:16px;font-size:11px;color:var(--tx-dim);margin-bottom:16px;flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:6px}
.sw{width:11px;height:11px;border-radius:2px;display:inline-block}
</style></head><body>
<header>
  <a class="back" href="index.html">&larr; all reports</a>
  <div class="eyebrow" style="margin-top:8px">DB2 migration reconciliation</div>
  <h1>__TABLE__<span class="badge __STATUSCLS__">__STATUS__</span></h1>
  <div class="sub">__DATE__ &nbsp;·&nbsp; keys: <code>__KEYS__</code></div>
</header>
<div class="metrics">__METRICS__</div>
<div class="wrap">
  <aside>
    <div class="searchbox">
      <input type="search" id="q" placeholder="Search fill id, column, value..." autocomplete="off">
      <div class="hint" id="count"></div>
    </div>
    <div id="list"></div>
  </aside>
  <main id="detail"><div class="empty">Select a fill from the list to see its differences.</div></main>
</div>
<script>
const DATA = __DATA__;
const COLS = __COLS__;
const KEYS = __KEYNAMES__;
const FLOW_LABEL = __FLOWLABEL__;
let active = null, showAll = false;

const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function hl(text, q){
  if(!q) return esc(text);
  const i = String(text).toLowerCase().indexOf(q.toLowerCase());
  if(i<0) return esc(text);
  const s=String(text);
  return esc(s.slice(0,i))+'<mark>'+esc(s.slice(i,i+q.length))+'</mark>'+esc(s.slice(i+q.length));
}
function ribbon(colIdx, cls){
  const max=120, step=Math.max(1,Math.ceil(COLS.length/max));
  let h='<div class="'+cls+'">';
  for(let i=0;i<COLS.length;i+=step){
    const hit=colIdx.some(x=>x>=i&&x<i+step);
    h+='<i class="'+(hit?'hit':'')+'"></i>';
  }
  return h+'</div>';
}
function keyChips(f,q){
  const tk=f.target_key||null;
  const vd=new Set((f.version_diffs||[]).map(d=>d.column));
  return '<div class="keychips">'+KEYS.map((k,i)=>{
    const sv=f.key[i], tv=tk?tk[i]:null;
    const differs=tk&&tv!==sv;
    const val=differs?'<span class="s">'+hl(sv,q)+'</span><span class="arrow">&rarr;</span>'+
      '<span class="t">'+hl(tv,q)+'</span>':hl(sv,q);
    return '<div class="kc'+(vd.has(k)||differs?' vdiff':'')+'"><span class="kn">'+esc(k)+
      '</span><span class="kv">'+val+'</span></div>';
  }).join('')+'</div>';
}
function miniKeys(f,q){
  return '<div class="minikeys">'+KEYS.map((k,i)=>
    esc(k)+'=<b>'+hl(f.key[i],q)+'</b>').join(' &nbsp;·&nbsp; ')+'</div>';
}
function matches(f,q){
  if(!q) return true; q=q.toLowerCase();
  if(f.key.join(' ').toLowerCase().includes(q)) return true;
  if((f.target_key||[]).join(' ').toLowerCase().includes(q)) return true;
  if(KEYS.some(k=>k.toLowerCase().includes(q))) return true;
  const inRow=d=>d.column.toLowerCase().includes(q)||
    String(d.source).toLowerCase().includes(q)||String(d.target).toLowerCase().includes(q);
  if(f.diffs.some(inRow)) return true;
  return (f.context||[]).some(inRow);
}
function ctxVal(c,q){
  if(!c.differs) return hl(c.source,q);
  return '<span class="s">'+hl(c.source,q)+'</span><span class="arrow">&rarr;</span>'+
         '<span class="t">'+hl(c.target,q)+'</span>';
}
function ctxBlock(f,q){
  const cx=f.context||[]; if(!cx.length) return '';
  const flow=cx.find(c=>c.column===FLOW_LABEL);
  const rest=cx.filter(c=>c.column!==FLOW_LABEL);
  let h='<div class="ctx"><h2>Fill context</h2><div class="ctxgrid">';
  if(flow){
    h+='<div class="ctxi flow'+(flow.differs?' d':'')+'"><div class="k">'+esc(FLOW_LABEL.replace(/_/g,' '))+' &nbsp;'+
       '<span style="text-transform:none;letter-spacing:0">derived</span></div>'+
       '<div class="v">'+ctxVal(flow,q)+'</div></div>';
  }
  h+=rest.map(c=>'<div class="ctxi'+(c.differs?' d':'')+'"><div class="k">'+
      esc(c.column)+'</div><div class="v">'+ctxVal(c,q)+'</div></div>').join('');
  return h+'</div></div>';
}
function render(){
  const q=document.getElementById('q').value.trim();
  const vis=DATA.filter(f=>matches(f,q));
  document.getElementById('count').textContent =
    vis.length+' of '+DATA.length+' fill'+(DATA.length===1?'':'s')+' with breaks';
  document.getElementById('list').innerHTML = vis.length? vis.map(f=>{
    const idx=f.diffs.map(d=>COLS.indexOf(d.column)).filter(i=>i>=0);
    const t=f.type==='ONLY_IN_SOURCE'?'<span class="tag src">SOURCE ONLY</span>'
      :f.type==='ONLY_IN_TARGET'?'<span class="tag tgt">TARGET ONLY</span>'
      :f.type==='VERSION_MISMATCH'?'<span class="tag ver">VERSION MISMATCH</span>'
      :'<span class="tag diff">'+f.diffs.length+' FIELD'+(f.diffs.length===1?'':'S')+'</span>';
    const cx=f.context||[];
    const fl=cx.find(c=>c.column===FLOW_LABEL);
    const sub=(fl?'<div class="flowline'+(fl.differs?' hitseg':'')+'">'+hl(fl.source,q)+'</div>':'');
    return '<div class="fill'+(active===f.id?' active':'')+'" tabindex="0" data-id="'+f.id+'">'+
      miniKeys(f,q)+
      '<div class="meta">'+t+'</div>'+
      sub+ribbon(idx,'ribbon')+'</div>';
  }).join('') : '<div class="empty" style="padding:34px 16px;font-size:12px">No fills match that search.</div>';
  document.querySelectorAll('.fill').forEach(el=>{
    el.onclick=()=>{active=el.dataset.id;render();detail();};
    el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click();}};
  });
}
function detail(){
  const f=DATA.find(x=>x.id===active);
  const d=document.getElementById('detail');
  if(!f){d.innerHTML='<div class="empty">Select a fill from the list to see its differences.</div>';return;}
  const q=document.getElementById('q').value.trim();
  const idx=f.diffs.map(x=>COLS.indexOf(x.column)).filter(i=>i>=0);
  const dm=new Map(f.diffs.map(x=>[x.column,x]));
  const rows=(showAll?COLS:f.diffs.map(x=>x.column)).map(c=>{
    const x=dm.get(c);
    if(!x) return '<tr><td class="col">'+esc(c)+'</td><td colspan="2" style="color:var(--tx-dim)">identical</td></tr>';
    const dv=x.derived?' <span class="tag" style="background:var(--rule);color:var(--tx-dim)">DERIVED</span>':'';
    return '<tr><td class="col">'+hl(x.column,q)+dv+'</td>'+
      '<td class="sv">'+hl(x.source,q)+'</td><td class="tv">'+hl(x.target,q)+'</td></tr>';
  }).join('');
  const vn=f.type==='VERSION_MISMATCH'
    ? '<div class="vnote"><strong>Version mismatch.</strong> Source and target hold the same '+
      'record at different versions, so a key join reports it as one missing plus one extra row. '+
      'Differing key part'+((f.version_diffs||[]).length===1?'':'s')+': '+
      (f.version_diffs||[]).map(d=>'<code>'+esc(d.column)+' '+esc(d.source)+' &rarr; '+
        esc(d.target)+'</code>').join(', ')+'. The field differences below are between those two versions.</div>'
    : '';
  d.innerHTML='<div class="keysub">unique key</div>'+keyChips(f,q)+vn+
    ctxBlock(f,q)+
    '<div class="legend">'+
      '<span><i class="sw" style="background:var(--src)"></i>source</span>'+
      '<span><i class="sw" style="background:var(--tgt)"></i>target</span>'+
      '<span><i class="sw" style="background:var(--break)"></i>differing column</span></div>'+
    ribbon(idx,'ribbon-lg')+
    '<div class="ribbon-cap">'+COLS.length+' compared columns, left to right &mdash; lit marks differ</div>'+
    '<div class="toggle"><button id="tg">'+(showAll?'Show only differences':'Show all columns')+'</button></div>'+
    '<table><thead><tr><th style="width:30%">Column</th><th class="s" style="width:35%">Source</th>'+
    '<th class="t" style="width:35%">Target</th></tr></thead><tbody>'+rows+'</tbody></table>';
  document.getElementById('tg').onclick=()=>{showAll=!showAll;detail();};
}
document.getElementById('q').addEventListener('input',()=>{render();if(active)detail();});
render();
</script></body></html>"""


def build_items(broken_keys, only_src, only_tgt, paired=None):
    paired = paired or []
    items = []
    for i, b in enumerate(broken_keys):
        items.append({"id": f"d{i}", "key": b["key"], "type": "VALUE_DIFF",
                      "diffs": b["diffs"], "context": b.get("context", [])})
    for i, p in enumerate(paired):
        items.append({"id": f"v{i}", "key": p["key"], "target_key": p["target_key"],
                      "type": "VERSION_MISMATCH", "version_diffs": p["version_diffs"],
                      "diffs": p["diffs"], "context": p.get("context", [])})
    for j, k in enumerate(only_src):
        items.append({"id": f"s{j}", "key": list(k), "type": "ONLY_IN_SOURCE",
                      "diffs": [{"column": "<ROW>", "source": "PRESENT", "target": "MISSING"}]})
    for j, k in enumerate(only_tgt):
        items.append({"id": f"t{j}", "key": list(k), "type": "ONLY_IN_TARGET",
                      "diffs": [{"column": "<ROW>", "source": "MISSING", "target": "PRESENT"}]})
    return items


def build_html(table, date_label, keys, stats, broken_keys, only_src, only_tgt,
               compare_cols, paired=None, flow_label="FLOW"):
    items = build_items(broken_keys, only_src, only_tgt, paired)

    def m(v, l, cls=""):
        return f'<div class="metric"><div class="v {cls}">{v}</div><div class="l">{l}</div></div>'

    mets = [
        m(f'{stats["rows_source"]:,}', "rows source"),
        m(f'{stats["rows_target"]:,}', "rows target"),
        m(f'{stats["match_rate_pct"]}%', "match rate",
          "good" if stats["match_rate_pct"] == 100 else "alert"),
        m(stats["rows_with_diffs"], "rows differing", "alert" if stats["rows_with_diffs"] else "good"),
        m(stats["cell_diffs"], "field diffs", "alert" if stats["cell_diffs"] else "good"),
    ]
    if stats.get("version_mismatched_pairs"):
        mets.append(m(stats["version_mismatched_pairs"], "version mismatch", "alert"))
    mets += [
        m(stats["only_in_source"], "source only", "alert" if stats["only_in_source"] else ""),
        m(stats["only_in_target"], "target only", "alert" if stats["only_in_target"] else ""),
        m(f'{stats["buckets_dirty"]}/{stats["buckets_total"]}', "buckets scanned"),
        m(f'{stats["buckets_skipped_pct"]}%', "buckets skipped"),
        m(stats["columns_compared"], "columns compared"),
        m(stats["columns_pruned_empty"], "columns pruned"),
    ]

    return (HTML
            .replace("__TABLE__", table)
            .replace("__DATE__", date_label)
            .replace("__KEYS__", ", ".join(keys))
            .replace("__STATUS__", stats["status"])
            .replace("__STATUSCLS__", "break" if stats["status"] == "BREAK" else "pass")
            .replace("__METRICS__", "".join(mets))
            .replace("__DATA__", json.dumps(items))
            .replace("__COLS__", json.dumps(compare_cols))
            .replace("__KEYNAMES__", json.dumps(keys))
            .replace("__FLOWLABEL__", json.dumps(flow_label)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--keys", required=True)
    ap.add_argument("--table", default="TABLE")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--buckets", type=int, default=256)
    ap.add_argument("--groups", type=int, default=8)
    ap.add_argument("--no-prune", action="store_true")
    ap.add_argument("--context-cols", help="Comma-separated context columns to display "
                                           "(default: auto-detect from the schema)")
    ap.add_argument("--flow-label", default=DEFAULT_FLOW_LABEL,
                    help="Name of the derived routing column (e.g. ORDER_FLOW)")
    ap.add_argument("--flow-template", default=DEFAULT_FLOW_TEMPLATE,
                    help="Template for the derived column, e.g. "
                         "\"{APP_CODE}-> {DEST_ID} -> {SUB_DEST_ID} ->  ({ORDER_MGMT_SYS_CODE})\"")
    ap.add_argument("--flow-anchor", default=FLOW_ANCHOR,
                    help="Insert the derived column immediately before this column")
    ap.add_argument("--no-pair-orphans", action="store_true",
                    help="Disable pairing of source-only/target-only rows that are the "
                         "same record at two different versions")
    args = ap.parse_args()

    keys = [k.strip().upper() for k in args.keys.split(",")]
    ctx_req = ([c.strip().upper() for c in args.context_cols.split(",")]
               if args.context_cols else None)
    src = pd.read_csv(args.source, dtype=str, keep_default_na=False)
    tgt = pd.read_csv(args.target, dtype=str, keep_default_na=False)

    (stats, breaks, broken_keys, compare_cols, pruned, groups,
     paired, only_src, only_tgt, ctx_cols) = compare(
        src, tgt, keys, args.buckets, args.groups, not args.no_prune,
        ctx_req, not args.no_pair_orphans,
        args.flow_template, args.flow_label, args.flow_anchor)

    os.makedirs(args.out_dir, exist_ok=True)
    date_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    html = build_html(args.table, date_label, keys, stats, broken_keys,
                      only_src, only_tgt, compare_cols, paired, args.flow_label)
    hp = os.path.join(args.out_dir, f"{args.table}_recon.html")
    with open(hp, "w") as f:
        f.write(html)

    # payload for the single-file bundler (recon_bundle.py)
    payload = {
        "table": args.table, "keys": keys, "cols": compare_cols,
        "flow_label": args.flow_label if stats.get("flow_column_added") else None,
        "stats": {k: v for k, v in stats.items() if k != "pruned_columns"},
        "items": build_items(broken_keys, only_src, only_tgt, paired),
    }
    with open(os.path.join(args.out_dir, f"{args.table}_payload.json"), "w") as f:
        json.dump(payload, f)

    if breaks:
        pd.DataFrame(breaks).to_csv(
            os.path.join(args.out_dir, f"{args.table}_breaks.csv"), index=False)
    stats["pruned_columns"] = pruned
    with open(os.path.join(args.out_dir, f"{args.table}_metrics.json"), "w") as f:
        json.dump(stats, f, indent=2)

    # Payload for the single-file bundler (recon_bundle.py): everything needed
    # to re-render this table's view without re-reading the source CSVs.
    items = json.loads(re.search(r"const DATA = (\[.*?\]);", html, re.S).group(1))
    payload = {"table": args.table, "keys": keys, "cols": compare_cols,
               "flow_label": stats.get("flow_label"), "items": items,
               "stats": {k: v for k, v in stats.items() if k != "pruned_columns"}}
    with open(os.path.join(args.out_dir, f"{args.table}_payload.json"), "w") as f:
        json.dump(payload, f)

    print(json.dumps({k: v for k, v in stats.items() if k != "pruned_columns"}, indent=2))
    print(f"\nReport: {hp}")


if __name__ == "__main__":
    main()
