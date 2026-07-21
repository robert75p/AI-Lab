#!/usr/bin/env python3
"""
recon_entities.py
=================
Cross-table entity reconciliation for the ORDER / ORDER_FILL / FILL domain.

The per-table reports (recon_fast.py) compare rows that share a full key. This
tool answers the questions that sit ABOVE the row level:

  PRESENCE  -- does the business entity exist on both sides at all?
    1. ORDER      present in source but not target, and vice versa (by ORDER_ID)
    2. ORDER_FILL present in source but not target (by ORDER_ID + FILL_ID)
    3. FILL       present in source but not target (by FILL_ID)

  LINKAGE   -- did the migration preserve the relationships between tables?
    4. ORDERs with no ORDER_FILL row -- computed per side, then drifted
    5. FILLs  with no ORDER_FILL row -- computed per side, then drifted
    6. (reverse) ORDER_FILL rows whose ORDER / FILL parent is absent

Presence is checked on IDENTITY (ORDER_ID / FILL_ID), deliberately ignoring the
version columns: an entity that exists on both sides at different versions is a
version drift, which the per-table report already covers. Here we only want to
know whether the entity is missing outright.

Usage:
    python recon_entities.py --config entities.yaml --out-dir ./recon/2026-07-16
    python recon_entities.py --out-dir ./recon/2026-07-16 \
        --order-source o_src.csv       --order-target o_tgt.csv \
        --orderfill-source of_src.csv  --orderfill-target of_tgt.csv \
        --fill-source f_src.csv        --fill-target f_tgt.csv

Outputs:
    ENTITY_recon.html      interactive report, one panel per check
    ENTITY_breaks.csv      long-format list of every entity-level break
    ENTITY_metrics.json    counts for every check
"""

import argparse
import json
import os
from datetime import datetime, timezone

import pandas as pd

MAX_LISTED = 500          # cap per check embedded in the HTML


def load(path, cols):
    want = {c.upper() for c in cols}
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     usecols=lambda c: c.strip().upper() in want)
    df.columns = [c.strip().upper() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def idset(df, cols):
    if len(cols) == 1:
        return set(df[cols[0]])
    return set(map(tuple, df[list(cols)].values))


def presence_check(name, entity, cols, src, tgt):
    a, b = idset(src, cols), idset(tgt, cols)
    only_a, only_b = sorted(a - b), sorted(b - a)
    return {
        "check": name, "kind": "presence", "entity": entity,
        "identity": list(cols),
        "count_source": len(a), "count_target": len(b),
        "only_in_source": len(only_a), "only_in_target": len(only_b),
        "sample_only_source": [list(x) if isinstance(x, tuple) else [x] for x in only_a[:MAX_LISTED]],
        "sample_only_target": [list(x) if isinstance(x, tuple) else [x] for x in only_b[:MAX_LISTED]],
        "status": "PASS" if not only_a and not only_b else "BREAK",
    }


def linkage_check(name, entity, parent_src, parent_tgt, child_src, child_tgt, col):
    """Entities present in the parent table but never referenced by the child.
    Computed independently per side, then drifted against each other."""
    ps, pt = set(parent_src[col]), set(parent_tgt[col])
    cs, ct = set(child_src[col]), set(child_tgt[col])
    miss_s, miss_t = ps - cs, pt - ct
    only_a, only_b = sorted(miss_s - miss_t), sorted(miss_t - miss_s)
    return {
        "check": name, "kind": "linkage", "entity": entity, "identity": [col],
        "unlinked_source": len(miss_s), "unlinked_target": len(miss_t),
        "only_in_source": len(only_a), "only_in_target": len(only_b),
        "sample_only_source": [[x] for x in only_a[:MAX_LISTED]],
        "sample_only_target": [[x] for x in only_b[:MAX_LISTED]],
        # drift is the break; a matching count on both sides is expected
        "status": "PASS" if not only_a and not only_b else "BREAK",
    }


def orphan_ref_check(name, entity, child_src, child_tgt, parent_src, parent_tgt, col):
    """Child rows whose parent is absent -- reported per side and drifted.
    In partial extracts this is usually a sampling artifact, so it is reported
    as INFO unless the two sides disagree."""
    cs, ct = set(child_src[col]), set(child_tgt[col])
    ps, pt = set(parent_src[col]), set(parent_tgt[col])
    orph_s, orph_t = cs - ps, ct - pt
    only_a, only_b = sorted(orph_s - orph_t), sorted(orph_t - orph_s)
    drift = bool(only_a or only_b)
    return {
        "check": name, "kind": "orphan_ref", "entity": entity, "identity": [col],
        "orphans_source": len(orph_s), "orphans_target": len(orph_t),
        "only_in_source": len(only_a), "only_in_target": len(only_b),
        "sample_only_source": [[x] for x in only_a[:MAX_LISTED]],
        "sample_only_target": [[x] for x in only_b[:MAX_LISTED]],
        "status": "BREAK" if drift else ("INFO" if orph_s or orph_t else "PASS"),
        "note": ("Both sides agree, so this reflects the extract scope "
                 "(partial samples), not a migration break." if not drift else
                 "The two sides disagree -- the migration changed which rows "
                 "reference a missing parent."),
    }


def run(paths, out_dir):
    O_ID, O_V = "ORDER_ID", "ORDER_VID"
    F_ID, F_V = "FILL_ID", "FILL_VID"

    Os = load(paths["order_source"], [O_ID, O_V])
    Ot = load(paths["order_target"], [O_ID, O_V])
    Fs = load(paths["fill_source"], [F_ID, F_V])
    Ft = load(paths["fill_target"], [F_ID, F_V])
    OFs = load(paths["orderfill_source"], [O_ID, O_V, F_ID, F_V])
    OFt = load(paths["orderfill_target"], [O_ID, O_V, F_ID, F_V])

    checks = [
        presence_check("1. ORDER present on both sides", "ORDER", [O_ID], Os, Ot),
        presence_check("2. ORDER_FILL present on both sides", "ORDER_FILL",
                       [O_ID, F_ID], OFs, OFt),
        presence_check("3. FILL present on both sides", "FILL", [F_ID], Fs, Ft),
        linkage_check("4. ORDER with no ORDER_FILL row", "ORDER",
                      Os, Ot, OFs, OFt, O_ID),
        linkage_check("5. FILL with no ORDER_FILL row", "FILL",
                      Fs, Ft, OFs, OFt, F_ID),
        orphan_ref_check("6a. ORDER_FILL referencing an absent ORDER", "ORDER_FILL",
                         OFs, OFt, Os, Ot, O_ID),
        orphan_ref_check("6b. ORDER_FILL referencing an absent FILL", "ORDER_FILL",
                         OFs, OFt, Fs, Ft, F_ID),
    ]

    rows = []
    for c in checks:
        for side, key in (("SOURCE", "sample_only_source"), ("TARGET", "sample_only_target")):
            for v in c[key]:
                rows.append({"check": c["check"], "entity": c["entity"],
                             "identity": "+".join(c["identity"]),
                             "value": "|".join(v), "only_in": side})

    os.makedirs(out_dir, exist_ok=True)
    if rows:
        pd.DataFrame(rows).to_csv(os.path.join(out_dir, "ENTITY_breaks.csv"), index=False)

    metrics = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "status": "BREAK" if any(c["status"] == "BREAK" for c in checks) else "PASS",
        "row_counts": {"ORDER": [len(Os), len(Ot)], "ORDER_FILL": [len(OFs), len(OFt)],
                       "FILL": [len(Fs), len(Ft)]},
    }
    with open(os.path.join(out_dir, "ENTITY_metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)

    html = build_html(checks, metrics)
    hp = os.path.join(out_dir, "ENTITY_recon.html")
    with open(hp, "w") as f:
        f.write(html)
    return checks, metrics, hp


# --------------------------------------------------------------------------- #

HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Entity reconciliation - __DATE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--ink:#0F1319;--panel:#161B23;--panel2:#1C222C;--rule:#28303B;--tx:#C9D2DC;
--tx-dim:#75818F;--tx-bright:#EAF0F6;--src:#4FA3D1;--tgt:#E0A140;--break:#E5484D;
--ok:#3DAE7C;--src-bg:rgba(79,163,209,.13);--tgt-bg:rgba(224,161,64,.13)}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--tx);font-family:'IBM Plex Sans',system-ui,sans-serif;
font-size:14px;line-height:1.5}
code,.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
header{border-bottom:1px solid var(--rule);padding:20px 24px;background:var(--panel)}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;
text-transform:uppercase;color:var(--tx-dim)}
h1{margin:6px 0 2px;font-size:22px;font-weight:600;color:var(--tx-bright)}
.sub{color:var(--tx-dim);font-size:13px}
.badge{display:inline-block;padding:3px 10px;border-radius:3px;font-family:'IBM Plex Mono',monospace;
font-size:11px;font-weight:600;letter-spacing:.08em;margin-left:10px;vertical-align:middle}
.badge.break{background:rgba(229,72,77,.15);color:var(--break);border:1px solid rgba(229,72,77,.4)}
.badge.pass{background:rgba(61,174,124,.15);color:var(--ok);border:1px solid rgba(61,174,124,.4)}
.back{color:var(--src);text-decoration:none;font-size:12px;font-family:'IBM Plex Mono',monospace}
.back:hover{text-decoration:underline}
main{padding:22px 26px;max-width:1180px}
.searchbox{margin-bottom:20px}
input[type=search]{width:100%;max-width:440px;padding:9px 11px;background:var(--panel);
border:1px solid var(--rule);border-radius:3px;color:var(--tx-bright);
font-family:'IBM Plex Mono',monospace;font-size:12px}
input[type=search]:focus{outline:2px solid var(--src);outline-offset:-1px}
.check{background:var(--panel);border:1px solid var(--rule);border-radius:4px;margin-bottom:16px}
.chead{display:flex;align-items:center;gap:12px;padding:14px 16px;cursor:pointer;flex-wrap:wrap}
.chead:hover{background:var(--panel2)}
.chead:focus-visible{outline:2px solid var(--src);outline-offset:-2px}
.ctitle{font-size:14.5px;color:var(--tx-bright);font-weight:500;flex:1;min-width:220px}
.pill{font-family:'IBM Plex Mono',monospace;font-size:10px;padding:3px 9px;border-radius:3px;
letter-spacing:.07em;white-space:nowrap}
.pill.pass{background:rgba(61,174,124,.13);color:var(--ok)}
.pill.brk{background:rgba(229,72,77,.15);color:var(--break)}
.pill.info{background:var(--panel2);color:var(--tx-dim);border:1px solid var(--rule)}
.pill.s{background:var(--src-bg);color:var(--src)}
.pill.t{background:var(--tgt-bg);color:var(--tgt)}
.cbody{border-top:1px solid var(--rule);padding:14px 16px;display:none}
.check.open .cbody{display:block}
.chev{color:var(--tx-dim);font-family:monospace;font-size:11px}
.note{font-size:12.5px;color:var(--tx-dim);margin-bottom:12px;line-height:1.55}
.counts{display:flex;gap:22px;flex-wrap:wrap;margin-bottom:14px;font-family:'IBM Plex Mono',monospace;font-size:12px}
.counts div span{color:var(--tx-dim);font-size:10px;letter-spacing:.08em;text-transform:uppercase;display:block}
.counts div b{color:var(--tx-bright);font-weight:600;font-size:16px}
.lists{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.lists{grid-template-columns:1fr}}
.lst h3{margin:0 0 8px;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.1em;
text-transform:uppercase;font-weight:500}
.lst.s h3{color:var(--src)} .lst.t h3{color:var(--tgt)}
.vals{max-height:260px;overflow-y:auto;border:1px solid var(--rule);border-radius:3px;background:var(--ink)}
.vals div{padding:5px 10px;border-bottom:1px solid var(--rule);font-family:'IBM Plex Mono',monospace;
font-size:11.5px;word-break:break-all}
.vals.s div{color:var(--src)} .vals.t div{color:var(--tgt)}
.vals .none{color:var(--tx-dim)}
mark{background:rgba(229,72,77,.35);color:var(--tx-bright);border-radius:2px}
</style></head><body>
<header>
<a class="back" href="index.html">&larr; all reports</a>
<div class="eyebrow" style="margin-top:8px">DB2 migration reconciliation</div>
<h1>Entity &amp; linkage checks<span class="badge __SC__">__STATUS__</span></h1>
<div class="sub">__DATE__ &nbsp;·&nbsp; ORDER, ORDER_FILL, FILL &nbsp;·&nbsp; presence compared on identity, ignoring version</div>
</header>
<main>
<div class="searchbox"><input type="search" id="q" placeholder="Search an ORDER_ID or FILL_ID across all checks..." autocomplete="off"></div>
<div id="checks"></div>
</main>
<script>
const CHECKS=__CHECKS__;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function hl(t,q){if(!q)return esc(t);const s=String(t),i=s.toLowerCase().indexOf(q.toLowerCase());
if(i<0)return esc(s);return esc(s.slice(0,i))+'<mark>'+esc(s.slice(i,i+q.length))+'</mark>'+esc(s.slice(i+q.length));}
let open=new Set();
function counts(c){
  if(c.kind==='presence')return [['in source',c.count_source],['in target',c.count_target],
    ['only in source',c.only_in_source],['only in target',c.only_in_target]];
  if(c.kind==='linkage')return [['unlinked in source',c.unlinked_source],['unlinked in target',c.unlinked_target],
    ['drift: only source',c.only_in_source],['drift: only target',c.only_in_target]];
  return [['orphan refs source',c.orphans_source],['orphan refs target',c.orphans_target],
    ['drift: only source',c.only_in_source],['drift: only target',c.only_in_target]];
}
function render(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  document.getElementById('checks').innerHTML=CHECKS.map((c,i)=>{
    const ss=c.sample_only_source.filter(v=>!q||v.join('|').toLowerCase().includes(q));
    const st=c.sample_only_target.filter(v=>!q||v.join('|').toLowerCase().includes(q));
    const hit=!q||ss.length||st.length;
    const pc=c.status==='PASS'?'pass':c.status==='INFO'?'info':'brk';
    const isOpen=open.has(i)||(q&&hit&&(ss.length||st.length));
    return '<div class="check'+(isOpen?' open':'')+'" style="'+(q&&!hit?'opacity:.35':'')+'">'+
      '<div class="chead" tabindex="0" data-i="'+i+'">'+
       '<span class="chev">'+(isOpen?'&#9660;':'&#9654;')+'</span>'+
       '<span class="ctitle">'+esc(c.check)+'</span>'+
       '<span class="pill '+pc+'">'+c.status+'</span>'+
       (c.only_in_source?'<span class="pill s">'+c.only_in_source+' src only</span>':'')+
       (c.only_in_target?'<span class="pill t">'+c.only_in_target+' tgt only</span>':'')+
      '</div>'+
      '<div class="cbody">'+
       (c.note?'<div class="note">'+esc(c.note)+'</div>':'')+
       '<div class="counts">'+counts(c).map(([l,v])=>
         '<div><span>'+l+'</span><b>'+v.toLocaleString()+'</b></div>').join('')+'</div>'+
       '<div class="lists">'+
        '<div class="lst s"><h3>only in source ('+c.only_in_source+')</h3><div class="vals s">'+
          (ss.length?ss.map(v=>'<div>'+hl(v.join(' / '),q)+'</div>').join('')
           :'<div class="none">none</div>')+'</div></div>'+
        '<div class="lst t"><h3>only in target ('+c.only_in_target+')</h3><div class="vals t">'+
          (st.length?st.map(v=>'<div>'+hl(v.join(' / '),q)+'</div>').join('')
           :'<div class="none">none</div>')+'</div></div>'+
       '</div></div></div>';
  }).join('');
  document.querySelectorAll('.chead').forEach(el=>{
    const i=+el.dataset.i;
    el.onclick=()=>{open.has(i)?open.delete(i):open.add(i);render();};
    el.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();el.click();}};
  });
}
document.getElementById('q').addEventListener('input',render);
render();
</script></body></html>"""


def build_html(checks, metrics):
    return (HTML
            .replace("__DATE__", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            .replace("__STATUS__", metrics["status"])
            .replace("__SC__", "break" if metrics["status"] == "BREAK" else "pass")
            .replace("__CHECKS__", json.dumps(checks)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order-source", required=True)
    ap.add_argument("--order-target", required=True)
    ap.add_argument("--orderfill-source", required=True)
    ap.add_argument("--orderfill-target", required=True)
    ap.add_argument("--fill-source", required=True)
    ap.add_argument("--fill-target", required=True)
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()

    checks, metrics, hp = run({
        "order_source": a.order_source, "order_target": a.order_target,
        "orderfill_source": a.orderfill_source, "orderfill_target": a.orderfill_target,
        "fill_source": a.fill_source, "fill_target": a.fill_target,
    }, a.out_dir)

    for c in checks:
        print(f"  [{c['status']:5}] {c['check']}: "
              f"only_src={c['only_in_source']} only_tgt={c['only_in_target']}")
    print(f"\nOverall: {metrics['status']}\nReport: {hp}")


if __name__ == "__main__":
    main()
