#!/usr/bin/env python3
"""
recon_bundle.py
===============
Bundles every report in a dated reconciliation folder into ONE self-contained
index.html -- no sibling files, no server, nothing external except the webfont
(which degrades gracefully offline).

Reads:
    <TABLE>_payload.json   written by recon_fast.py (one per table)
    ENTITY_metrics.json    written by recon_entities.py (optional)

Writes:
    index.html             summary + every table view + entity checks in one file

Usage:
    python recon_bundle.py --folder ./recon/2026-07-16 --title "DB2 recon 16 Jul"
    python recon_bundle.py --folder ./recon/2026-07-16 --clean   # remove the
                           # per-table HTML/payload files once bundled

Exit code 0 if everything passed, 2 if any table or entity check broke.
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone


def collect(folder):
    tables = []
    for p in sorted(glob.glob(os.path.join(folder, "*_payload.json"))):
        with open(p) as f:
            tables.append(json.load(f))
    entity = None
    ep = os.path.join(folder, "ENTITY_metrics.json")
    if os.path.exists(ep):
        with open(ep) as f:
            entity = json.load(f)
    return tables, entity


HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--ink:#0F1319;--panel:#161B23;--panel2:#1C222C;--rule:#28303B;--tx:#C9D2DC;
--tx-dim:#75818F;--tx-bright:#EAF0F6;--src:#4FA3D1;--tgt:#E0A140;--break:#E5484D;--ok:#3DAE7C;
--src-bg:rgba(79,163,209,.13);--tgt-bg:rgba(224,161,64,.13)}
*{box-sizing:border-box}
body{margin:0;background:var(--ink);color:var(--tx);font-family:'IBM Plex Sans',system-ui,sans-serif;
font-size:14px;line-height:1.5}
code,.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
header{border-bottom:1px solid var(--rule);padding:22px 24px 0;background:var(--panel)}
.eyebrow{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;
text-transform:uppercase;color:var(--tx-dim)}
h1{margin:6px 0 3px;font-size:23px;font-weight:600;color:var(--tx-bright);letter-spacing:-.01em}
.sub{color:var(--tx-dim);font-size:13px;margin-bottom:16px}
.badge{display:inline-block;padding:3px 10px;border-radius:3px;font-family:'IBM Plex Mono',monospace;
font-size:11px;font-weight:600;letter-spacing:.08em;margin-left:11px;vertical-align:middle}
.badge.break{background:rgba(229,72,77,.15);color:var(--break);border:1px solid rgba(229,72,77,.4)}
.badge.pass{background:rgba(61,174,124,.15);color:var(--ok);border:1px solid rgba(61,174,124,.4)}
/* tabs */
nav{display:flex;gap:2px;flex-wrap:wrap}
nav button{background:none;border:none;border-bottom:2px solid transparent;color:var(--tx-dim);
padding:9px 14px;font-family:'IBM Plex Mono',monospace;font-size:12px;cursor:pointer;
display:flex;align-items:center;gap:7px}
nav button:hover{color:var(--tx-bright)}
nav button.on{color:var(--tx-bright);border-bottom-color:var(--src)}
nav button:focus-visible{outline:2px solid var(--src);outline-offset:-2px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--ok);display:inline-block}
.dot.brk{background:var(--break)}
.view{display:none} .view.on{display:block}
/* metrics strip */
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:1px;
background:var(--rule);border-bottom:1px solid var(--rule)}
.metric{background:var(--panel);padding:12px 16px}
.metric .v{font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:var(--tx-bright)}
.metric .v.alert{color:var(--break)} .metric .v.good{color:var(--ok)}
.metric .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);margin-top:2px}
/* summary cards */
main.pad{padding:24px 26px;max-width:1220px}
h2{font-size:12px;letter-spacing:.13em;text-transform:uppercase;color:var(--tx-dim);
font-family:'IBM Plex Mono',monospace;font-weight:500;margin:0 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px;margin-bottom:32px}
.card{background:var(--panel);border:1px solid var(--rule);border-left:3px solid var(--rule);
border-radius:4px;padding:15px 17px;cursor:pointer;text-align:left;width:100%;font:inherit;color:inherit}
.card:hover{background:var(--panel2);border-color:var(--src);border-left-color:var(--src)}
.card:focus-visible{outline:2px solid var(--src);outline-offset:2px}
.card.brk{border-left-color:var(--break)} .card.ok{border-left-color:var(--ok)}
.ct{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:11px}
.cn{font-family:'IBM Plex Mono',monospace;font-size:15px;color:var(--tx-bright);font-weight:600}
.rows{display:grid;grid-template-columns:1fr 1fr;gap:7px 14px;font-size:12px}
.rows div{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid var(--rule);padding-bottom:5px}
.rows span{color:var(--tx-dim)}
.rows b{font-family:'IBM Plex Mono',monospace;color:var(--tx-bright);font-weight:500}
.rows b.alert{color:var(--break)} .rows b.good{color:var(--ok)}
.foot{font-size:11px;color:var(--tx-dim);margin-top:10px;font-family:'IBM Plex Mono',monospace}
.pill{font-family:'IBM Plex Mono',monospace;font-size:9.5px;padding:3px 8px;border-radius:3px;letter-spacing:.07em}
.pill.brk{background:rgba(229,72,77,.15);color:var(--break)}
.pill.ok{background:rgba(61,174,124,.13);color:var(--ok)}
.pill.info{background:var(--panel2);color:var(--tx-dim);border:1px solid var(--rule)}
.pill.s{background:var(--src-bg);color:var(--src)}
.pill.t{background:var(--tgt-bg);color:var(--tgt)}
/* table view: rail + detail */
.wrap{display:grid;grid-template-columns:340px 1fr;min-height:60vh}
@media(max-width:860px){.wrap{grid-template-columns:1fr}}
aside{border-right:1px solid var(--rule);background:var(--panel);overflow-y:auto;max-height:72vh}
.searchbox{padding:12px;border-bottom:1px solid var(--rule);position:sticky;top:0;background:var(--panel);z-index:2}
input[type=search]{width:100%;padding:9px 11px;background:var(--ink);border:1px solid var(--rule);
border-radius:3px;color:var(--tx-bright);font-family:'IBM Plex Mono',monospace;font-size:12px}
input[type=search]:focus{outline:2px solid var(--src);outline-offset:-1px;border-color:transparent}
.hint{font-size:11px;color:var(--tx-dim);margin-top:7px}
.fill{padding:11px 14px;border-bottom:1px solid var(--rule);cursor:pointer;border-left:2px solid transparent}
.fill:hover{background:var(--panel2)}
.fill.active{background:var(--panel2);border-left-color:var(--break)}
.fill:focus-visible{outline:2px solid var(--src);outline-offset:-2px}
.meta{font-size:11px;color:var(--tx-dim);margin-top:4px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-family:'IBM Plex Mono',monospace;font-size:9.5px;padding:1px 6px;border-radius:2px;letter-spacing:.05em}
.tag.diff{background:rgba(229,72,77,.15);color:var(--break)}
.tag.src{background:var(--src-bg);color:var(--src)}
.tag.tgt{background:var(--tgt-bg);color:var(--tgt)}
.tag.ver{background:var(--tgt-bg);color:var(--tgt)}
.minikeys{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--tx-dim);
line-height:1.45;word-break:break-all}
.minikeys b{color:var(--tx-bright);font-weight:500}
.flowline{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--tx-dim);
margin-top:5px;word-break:break-all}
.flowline.hitseg{color:var(--break)}
.ribbon{display:flex;gap:1px;margin-top:7px;height:9px}
.ribbon i{flex:1;background:var(--rule);border-radius:1px}
.ribbon i.hit{background:var(--break);box-shadow:0 0 5px rgba(229,72,77,.8)}
main.detail{padding:22px 26px;overflow-y:auto;max-height:72vh}
.empty{color:var(--tx-dim);text-align:center;padding:60px 20px}
.keysub{color:var(--tx-dim);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
font-family:'IBM Plex Mono',monospace;margin-bottom:7px}
.keychips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px}
.kc{background:var(--panel2);border:1px solid var(--rule);border-radius:3px;padding:4px 9px;min-width:0}
.kc .kn{font-size:9px;letter-spacing:.09em;text-transform:uppercase;color:var(--tx-dim);display:block}
.kc .kv{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tx-bright);
word-break:break-all;display:block;margin-top:1px}
.kc.vdiff{border-color:var(--break)} .kc.vdiff .kv{color:var(--break)}
.vnote{background:rgba(224,161,64,.09);border:1px solid rgba(224,161,64,.35);border-radius:4px;
padding:11px 14px;margin-bottom:18px;font-size:12.5px}
.vnote strong{color:var(--tgt)}
.ctx{background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:14px 16px;margin-bottom:20px}
.ctx h3{margin:0 0 11px;font-family:'IBM Plex Mono',monospace;font-size:10px;
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
.ribbon-lg{display:flex;gap:1px;height:16px;margin:14px 0 8px}
.ribbon-lg i{flex:1;background:var(--rule);border-radius:1px}
.ribbon-lg i.hit{background:var(--break);box-shadow:0 0 7px rgba(229,72,77,.9)}
.ribbon-cap{font-size:11px;color:var(--tx-dim);font-family:'IBM Plex Mono',monospace;margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;padding:8px 12px;font-family:'IBM Plex Mono',monospace;font-size:10px;
letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);border-bottom:1px solid var(--rule);font-weight:500}
th.s{color:var(--src)} th.t{color:var(--tgt)}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top;
font-family:'IBM Plex Mono',monospace;word-break:break-all}
td.col{color:var(--tx-bright);font-weight:500}
td.sv{background:var(--src-bg);color:var(--src)}
td.tv{background:var(--tgt-bg);color:var(--tgt)}
mark{background:rgba(229,72,77,.35);color:var(--tx-bright);padding:0 1px;border-radius:2px}
button.mini{background:var(--panel2);border:1px solid var(--rule);color:var(--tx);padding:6px 12px;
border-radius:3px;font-family:'IBM Plex Mono',monospace;font-size:11px;cursor:pointer;margin-bottom:14px}
button.mini:hover{border-color:var(--src);color:var(--tx-bright)}
.legend{display:flex;gap:16px;font-size:11px;color:var(--tx-dim);margin-bottom:14px;flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:6px}
.sw{width:11px;height:11px;border-radius:2px;display:inline-block}
/* entity checks */
.check{background:var(--panel);border:1px solid var(--rule);border-radius:4px;margin-bottom:14px}
.chead{display:flex;align-items:center;gap:12px;padding:14px 16px;cursor:pointer;flex-wrap:wrap}
.chead:hover{background:var(--panel2)}
.chead:focus-visible{outline:2px solid var(--src);outline-offset:-2px}
.ctitle{font-size:14.5px;color:var(--tx-bright);font-weight:500;flex:1;min-width:210px}
.cbody{border-top:1px solid var(--rule);padding:14px 16px;display:none}
.check.open .cbody{display:block}
.chev{color:var(--tx-dim);font-family:monospace;font-size:11px}
.note{font-size:12.5px;color:var(--tx-dim);margin-bottom:12px;line-height:1.55}
.counts{display:flex;gap:22px;flex-wrap:wrap;margin-bottom:14px;font-family:'IBM Plex Mono',monospace;font-size:12px}
.counts div span{color:var(--tx-dim);font-size:10px;letter-spacing:.08em;text-transform:uppercase;display:block}
.counts div b{color:var(--tx-bright);font-weight:600;font-size:16px}
.lists{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:760px){.lists{grid-template-columns:1fr}}
.lst h4{margin:0 0 8px;font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.1em;
text-transform:uppercase;font-weight:500}
.lst.s h4{color:var(--src)} .lst.t h4{color:var(--tgt)}
.vals{max-height:250px;overflow-y:auto;border:1px solid var(--rule);border-radius:3px;background:var(--ink)}
.vals div{padding:5px 10px;border-bottom:1px solid var(--rule);font-family:'IBM Plex Mono',monospace;
font-size:11.5px;word-break:break-all}
.vals.s div{color:var(--src)} .vals.t div{color:var(--tgt)}
.vals .none{color:var(--tx-dim)}
</style></head><body>
<header>
<div class="eyebrow">DB2 migration reconciliation</div>
<h1>__TITLE__<span class="badge __SC__">__STATUS__</span></h1>
<div class="sub">__DATE__ &nbsp;·&nbsp; __NTAB__ tables &nbsp;·&nbsp; single-file report</div>
<nav id="nav"></nav>
</header>
<div id="app"></div>
<script>
const TABLES=__TABLES__, ENTITY=__ENTITY__, TOTALS=__TOTALS__;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function hl(t,q){if(!q)return esc(t);const s=String(t),i=s.toLowerCase().indexOf(q.toLowerCase());
if(i<0)return esc(s);return esc(s.slice(0,i))+'<mark>'+esc(s.slice(i,i+q.length))+'</mark>'+esc(s.slice(i+q.length));}
let view='summary';
const st={};  // per-table ui state
TABLES.forEach(t=>st[t.table]={q:'',active:null,showAll:false});
let eOpen=new Set(), eQ='';

/* ---------- nav ---------- */
function nav(){
  const items=[['summary','Summary',TOTALS.status==='BREAK']]
    .concat(TABLES.map(t=>[t.table,t.table,t.stats.status==='BREAK']))
    .concat(ENTITY?[['entity','Entity checks',ENTITY.status==='BREAK']]:[]);
  document.getElementById('nav').innerHTML=items.map(([k,l,b])=>
    '<button data-v="'+k+'" class="'+(view===k?'on':'')+'"><i class="dot'+(b?' brk':'')+'"></i>'+esc(l)+'</button>').join('');
  document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>{view=b.dataset.v;draw();});
}
/* ---------- summary ---------- */
function metric(v,l,c){return '<div class="metric"><div class="v '+(c||'')+'">'+v+'</div><div class="l">'+l+'</div></div>';}
function summary(){
  const T=TOTALS;
  const strip='<div class="metrics">'+
    metric(T.rows.toLocaleString(),'source rows')+
    metric(TABLES.length,'tables')+
    metric(T.rows_diff,'rows differing',T.rows_diff?'alert':'good')+
    metric(T.cells,'field diffs',T.cells?'alert':'good')+
    metric(T.version,'version mismatch',T.version?'alert':'good')+
    metric(T.unmatched,'unmatched rows',T.unmatched?'alert':'good')+
    metric(T.skipped+'%','rows skipped')+
    metric(T.pruned.toLocaleString(),'columns pruned')+'</div>';
  const cards=TABLES.slice().sort((a,b)=>
      (a.stats.status==='BREAK'?0:1)-(b.stats.status==='BREAK'?0:1)||a.table.localeCompare(b.table))
    .map(t=>{const s=t.stats,brk=s.status==='BREAK';
      let r=[['rows',(s.rows_source||0).toLocaleString(),''],
             ['match rate',(s.match_rate_pct)+'%',s.match_rate_pct<100?'alert':'good'],
             ['rows differing',s.rows_with_diffs,s.rows_with_diffs?'alert':''],
             ['field diffs',s.cell_diffs,s.cell_diffs?'alert':'']];
      if(s.version_mismatched_pairs)r.push(['version mismatch',s.version_mismatched_pairs,'alert']);
      if((s.only_in_source||0)+(s.only_in_target||0))r.push(['unmatched',(s.only_in_source||0)+(s.only_in_target||0),'alert']);
      return '<button class="card '+(brk?'brk':'ok')+'" data-t="'+esc(t.table)+'">'+
        '<div class="ct"><span class="cn">'+esc(t.table)+'</span>'+
        '<span class="pill '+(brk?'brk':'ok')+'">'+s.status+'</span></div>'+
        '<div class="rows">'+r.map(([l,v,c])=>'<div><span>'+l+'</span><b class="'+c+'">'+v+'</b></div>').join('')+'</div>'+
        '<div class="foot">'+s.columns_compared+' cols compared · '+s.columns_pruned_empty+' pruned · '+
        s.buckets_skipped_pct+'% buckets skipped'+(t.flow_label?' · '+esc(t.flow_label):'')+'</div></button>';}).join('');
  let ent='';
  if(ENTITY){
    const pm={PASS:'ok',BREAK:'brk',INFO:'info'};
    ent='<h2>Entity &amp; linkage checks</h2><div class="note">Presence compared on identity '+
      '(ORDER_ID / FILL_ID), ignoring version. Same-entity-different-version is reported as a '+
      'version mismatch in the table views.</div><div class="checklist">'+
      ENTITY.checks.map(c=>'<div class="check"><div class="chead" style="cursor:default">'+
        '<span class="pill '+(pm[c.status]||'info')+'">'+c.status+'</span>'+
        '<span class="ctitle">'+esc(c.check)+'</span>'+
        ((c.only_in_source||c.only_in_target)?'<span class="pill brk">'+c.only_in_source+
          ' src / '+c.only_in_target+' tgt</span>':'')+'</div></div>').join('')+'</div>';
  }
  return strip+'<main class="pad"><h2>Table reports</h2><div class="cards">'+cards+'</div>'+ent+'</main>';
}
/* ---------- table view ---------- */
function ribbon(idx,cls,cols){const max=120,step=Math.max(1,Math.ceil(cols.length/max));
  let h='<div class="'+cls+'">';
  for(let i=0;i<cols.length;i+=step){h+='<i class="'+(idx.some(x=>x>=i&&x<i+step)?'hit':'')+'"></i>';}
  return h+'</div>';}
function matches(f,q,KEYS){if(!q)return true;q=q.toLowerCase();
  if(f.key.join(' ').toLowerCase().includes(q))return true;
  if((f.target_key||[]).join(' ').toLowerCase().includes(q))return true;
  if(KEYS.some(k=>k.toLowerCase().includes(q)))return true;
  const ir=d=>d.column.toLowerCase().includes(q)||String(d.source).toLowerCase().includes(q)||
    String(d.target).toLowerCase().includes(q);
  return f.diffs.some(ir)||(f.context||[]).some(ir);}
function keyChips(f,q,KEYS){const tk=f.target_key||null,vd=new Set((f.version_diffs||[]).map(d=>d.column));
  return '<div class="keychips">'+KEYS.map((k,i)=>{const sv=f.key[i],tv=tk?tk[i]:null,df=tk&&tv!==sv;
    const val=df?'<span class="s">'+hl(sv,q)+'</span><span class="arrow">&rarr;</span><span class="t">'+hl(tv,q)+'</span>':hl(sv,q);
    return '<div class="kc'+((vd.has(k)||df)?' vdiff':'')+'"><span class="kn">'+esc(k)+
      '</span><span class="kv">'+val+'</span></div>';}).join('')+'</div>';}
function ctxVal(c,q){return c.differs?'<span class="s">'+hl(c.source,q)+'</span><span class="arrow">&rarr;</span>'+
  '<span class="t">'+hl(c.target,q)+'</span>':hl(c.source,q);}
function ctxBlock(f,q,FL){const cx=f.context||[];if(!cx.length)return '';
  const fl=cx.find(c=>c.column===FL),rest=cx.filter(c=>c.column!==FL);
  let h='<div class="ctx"><h3>Context</h3><div class="ctxgrid">';
  if(fl)h+='<div class="ctxi flow'+(fl.differs?' d':'')+'"><div class="k">'+esc(String(FL).replace(/_/g,' '))+
    ' &nbsp;<span style="text-transform:none;letter-spacing:0">derived</span></div><div class="v">'+ctxVal(fl,q)+'</div></div>';
  h+=rest.map(c=>'<div class="ctxi'+(c.differs?' d':'')+'"><div class="k">'+esc(c.column)+
    '</div><div class="v">'+ctxVal(c,q)+'</div></div>').join('');
  return h+'</div></div>';}
function tableView(t){
  const S=st[t.table],q=S.q,KEYS=t.keys,COLS=t.cols,FL=t.flow_label;
  const s=t.stats;
  const strip='<div class="metrics">'+
    metric((s.rows_source||0).toLocaleString(),'rows source')+
    metric((s.rows_target||0).toLocaleString(),'rows target')+
    metric(s.match_rate_pct+'%','match rate',s.match_rate_pct<100?'alert':'good')+
    metric(s.rows_with_diffs,'rows differing',s.rows_with_diffs?'alert':'good')+
    metric(s.cell_diffs,'field diffs',s.cell_diffs?'alert':'good')+
    (s.version_mismatched_pairs?metric(s.version_mismatched_pairs,'version mismatch','alert'):'')+
    metric(s.only_in_source||0,'source only',s.only_in_source?'alert':'')+
    metric(s.only_in_target||0,'target only',s.only_in_target?'alert':'')+
    metric(s.buckets_dirty+'/'+s.buckets_total,'buckets scanned')+
    metric(s.columns_compared,'columns compared')+
    metric(s.columns_pruned_empty,'columns pruned')+'</div>';
  const vis=t.items.filter(f=>matches(f,q,KEYS));
  const list=vis.length?vis.map(f=>{
    const idx=f.diffs.map(d=>COLS.indexOf(d.column)).filter(i=>i>=0);
    const tag=f.type==='ONLY_IN_SOURCE'?'<span class="tag src">SOURCE ONLY</span>'
      :f.type==='ONLY_IN_TARGET'?'<span class="tag tgt">TARGET ONLY</span>'
      :f.type==='VERSION_MISMATCH'?'<span class="tag ver">VERSION MISMATCH</span>'
      :'<span class="tag diff">'+f.diffs.length+' FIELD'+(f.diffs.length===1?'':'S')+'</span>';
    const fl=(f.context||[]).find(c=>c.column===FL);
    return '<div class="fill'+(S.active===f.id?' active':'')+'" tabindex="0" data-id="'+f.id+'">'+
      '<div class="minikeys">'+KEYS.map((k,i)=>esc(k)+'=<b>'+hl(f.key[i],q)+'</b>').join(' &nbsp;·&nbsp; ')+'</div>'+
      '<div class="meta">'+tag+'</div>'+
      (fl?'<div class="flowline'+(fl.differs?' hitseg':'')+'">'+hl(fl.source,q)+'</div>':'')+
      ribbon(idx,'ribbon',COLS)+'</div>';}).join('')
    :'<div class="empty" style="padding:34px 16px;font-size:12px">Nothing matches that search.</div>';
  const f=t.items.find(x=>x.id===S.active);
  let det='<div class="empty">Select a row on the left to see its differences.</div>';
  if(f){
    const idx=f.diffs.map(x=>COLS.indexOf(x.column)).filter(i=>i>=0);
    const dm=new Map(f.diffs.map(x=>[x.column,x]));
    const rows=(S.showAll?COLS:f.diffs.map(x=>x.column)).map(c=>{const x=dm.get(c);
      if(!x)return '<tr><td class="col">'+esc(c)+'</td><td colspan="2" style="color:var(--tx-dim)">identical</td></tr>';
      const dv=x.derived?' <span class="tag" style="background:var(--rule);color:var(--tx-dim)">DERIVED</span>':'';
      return '<tr><td class="col">'+hl(x.column,q)+dv+'</td><td class="sv">'+hl(x.source,q)+
        '</td><td class="tv">'+hl(x.target,q)+'</td></tr>';}).join('');
    const vn=f.type==='VERSION_MISMATCH'?'<div class="vnote"><strong>Version mismatch.</strong> '+
      'Source and target hold the same record at different versions, so a key join reports it as '+
      'one missing plus one extra row. Differing: '+(f.version_diffs||[]).map(d=>'<code>'+esc(d.column)+
      ' '+esc(d.source)+' &rarr; '+esc(d.target)+'</code>').join(', ')+
      '. The fields below differ between those two versions.</div>':'';
    det='<div class="keysub">unique key</div>'+keyChips(f,q,KEYS)+vn+ctxBlock(f,q,FL)+
      '<div class="legend"><span><i class="sw" style="background:var(--src)"></i>source</span>'+
      '<span><i class="sw" style="background:var(--tgt)"></i>target</span>'+
      '<span><i class="sw" style="background:var(--break)"></i>differing column</span></div>'+
      ribbon(idx,'ribbon-lg',COLS)+
      '<div class="ribbon-cap">'+COLS.length+' compared columns, left to right &mdash; lit marks differ</div>'+
      '<button class="mini" id="tg">'+(S.showAll?'Show only differences':'Show all columns')+'</button>'+
      '<table><thead><tr><th style="width:30%">Column</th><th class="s" style="width:35%">Source</th>'+
      '<th class="t" style="width:35%">Target</th></tr></thead><tbody>'+rows+'</tbody></table>';
  }
  return strip+'<div class="wrap"><aside><div class="searchbox">'+
    '<input type="search" id="tq" placeholder="Search key, column, value..." value="'+esc(q)+'">'+
    '<div class="hint">'+vis.length+' of '+t.items.length+' row'+(t.items.length===1?'':'s')+' with breaks</div>'+
    '</div><div id="list">'+list+'</div></aside><main class="detail">'+det+'</main></div>';
}
/* ---------- entity view ---------- */
function counts(c){
  if(c.kind==='presence')return [['in source',c.count_source],['in target',c.count_target],
    ['only in source',c.only_in_source],['only in target',c.only_in_target]];
  if(c.kind==='linkage')return [['unlinked in source',c.unlinked_source],['unlinked in target',c.unlinked_target],
    ['drift: only source',c.only_in_source],['drift: only target',c.only_in_target]];
  return [['orphan refs source',c.orphans_source],['orphan refs target',c.orphans_target],
    ['drift: only source',c.only_in_source],['drift: only target',c.only_in_target]];}
function entityView(){
  const q=eQ.trim().toLowerCase();
  const body=ENTITY.checks.map((c,i)=>{
    const ss=c.sample_only_source.filter(v=>!q||v.join('|').toLowerCase().includes(q));
    const stt=c.sample_only_target.filter(v=>!q||v.join('|').toLowerCase().includes(q));
    const hit=!q||ss.length||stt.length;
    const pc=c.status==='PASS'?'ok':c.status==='INFO'?'info':'brk';
    const open=eOpen.has(i)||(q&&(ss.length||stt.length));
    return '<div class="check'+(open?' open':'')+'" style="'+(q&&!hit?'opacity:.35':'')+'">'+
      '<div class="chead" tabindex="0" data-i="'+i+'"><span class="chev">'+(open?'&#9660;':'&#9654;')+'</span>'+
      '<span class="ctitle">'+esc(c.check)+'</span><span class="pill '+pc+'">'+c.status+'</span>'+
      (c.only_in_source?'<span class="pill s">'+c.only_in_source+' src only</span>':'')+
      (c.only_in_target?'<span class="pill t">'+c.only_in_target+' tgt only</span>':'')+'</div>'+
      '<div class="cbody">'+(c.note?'<div class="note">'+esc(c.note)+'</div>':'')+
      '<div class="counts">'+counts(c).map(([l,v])=>'<div><span>'+l+'</span><b>'+
        (v||0).toLocaleString()+'</b></div>').join('')+'</div>'+
      '<div class="lists"><div class="lst s"><h4>only in source ('+c.only_in_source+')</h4>'+
      '<div class="vals s">'+(ss.length?ss.map(v=>'<div>'+hl(v.join(' / '),q)+'</div>').join('')
        :'<div class="none">none</div>')+'</div></div>'+
      '<div class="lst t"><h4>only in target ('+c.only_in_target+')</h4>'+
      '<div class="vals t">'+(stt.length?stt.map(v=>'<div>'+hl(v.join(' / '),q)+'</div>').join('')
        :'<div class="none">none</div>')+'</div></div></div></div></div>';}).join('');
  return '<main class="pad"><div class="searchbox" style="padding:0 0 18px;border:none;position:static">'+
    '<input type="search" id="eq" placeholder="Search an ORDER_ID or FILL_ID across all checks..." value="'+
    esc(eQ)+'"></div>'+body+'</main>';
}
/* ---------- draw ---------- */
function draw(){
  nav();
  const app=document.getElementById('app');
  if(view==='summary'){app.innerHTML=summary();
    document.querySelectorAll('.card').forEach(c=>c.onclick=()=>{view=c.dataset.t;draw();});return;}
  if(view==='entity'){app.innerHTML=entityView();
    const e=document.getElementById('eq');
    e.oninput=()=>{eQ=e.value;const p=e.selectionStart;draw();
      const n=document.getElementById('eq');n.focus();n.setSelectionRange(p,p);};
    document.querySelectorAll('.chead[data-i]').forEach(el=>{const i=+el.dataset.i;
      el.onclick=()=>{eOpen.has(i)?eOpen.delete(i):eOpen.add(i);draw();};
      el.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();el.click();}};});
    return;}
  const t=TABLES.find(x=>x.table===view);
  if(!t){view='summary';return draw();}
  app.innerHTML=tableView(t);
  const S=st[t.table];
  const inp=document.getElementById('tq');
  inp.oninput=()=>{S.q=inp.value;const p=inp.selectionStart;draw();
    const n=document.getElementById('tq');n.focus();n.setSelectionRange(p,p);};
  document.querySelectorAll('.fill').forEach(el=>{
    el.onclick=()=>{S.active=el.dataset.id;draw();};
    el.onkeydown=ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();el.click();}};});
  const tg=document.getElementById('tg');
  if(tg)tg.onclick=()=>{S.showAll=!S.showAll;draw();};
}
draw();
</script></body></html>"""


def build(title, tables, entity):
    any_break = any(t["stats"].get("status") == "BREAK" for t in tables) or \
                (entity and entity.get("status") == "BREAK")
    rows = sum(t["stats"].get("rows_source", 0) for t in tables)
    inspected = sum(t["stats"].get("rows_inspected_after_bucket_filter", 0) for t in tables)
    totals = {
        "status": "BREAK" if any_break else "PASS",
        "rows": rows,
        "rows_diff": sum(t["stats"].get("rows_with_diffs", 0) for t in tables),
        "cells": sum(t["stats"].get("cell_diffs", 0) for t in tables),
        "version": sum(t["stats"].get("version_mismatched_pairs", 0) for t in tables),
        "unmatched": sum(t["stats"].get("only_in_source", 0) +
                         t["stats"].get("only_in_target", 0) for t in tables),
        "pruned": sum(t["stats"].get("columns_pruned_empty", 0) for t in tables),
        "skipped": round(100.0 * (1 - inspected / rows), 1) if rows else 0,
    }
    return (HTML
            .replace("__TITLE__", title)
            .replace("__STATUS__", totals["status"])
            .replace("__SC__", "break" if any_break else "pass")
            .replace("__DATE__", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            .replace("__NTAB__", str(len(tables)))
            .replace("__TABLES__", json.dumps(tables))
            .replace("__ENTITY__", json.dumps(entity) if entity else "null")
            .replace("__TOTALS__", json.dumps(totals))), any_break


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--title", default="Reconciliation summary")
    ap.add_argument("--clean", action="store_true",
                    help="Delete the per-table HTML and payload files after bundling")
    a = ap.parse_args()

    tables, entity = collect(a.folder)
    if not tables:
        print(f"No *_payload.json in {a.folder} -- run recon_fast.py first.")
        sys.exit(1)

    html, any_break = build(a.title, tables, entity)
    p = os.path.join(a.folder, "index.html")
    with open(p, "w") as f:
        f.write(html)

    for t in tables:
        s = t["stats"]
        print(f"  [{s.get('status','?'):5}] {t['table']:12} "
              f"rows={s.get('rows_source',0):>7,} diffs={s.get('cell_diffs',0):>3} "
              f"items={len(t['items'])}")
    if entity:
        print(f"  [{entity.get('status','?'):5}] ENTITY       {len(entity.get('checks',[]))} checks")

    if a.clean:
        removed = 0
        for pat in ("*_payload.json",):
            for f_ in glob.glob(os.path.join(a.folder, pat)):
                os.remove(f_); removed += 1
        for f_ in glob.glob(os.path.join(a.folder, "*_recon.html")):
            os.remove(f_); removed += 1
        print(f"  cleaned {removed} intermediate file(s)")

    size = os.path.getsize(p)
    print(f"\nSingle-file report: {p}  ({size/1024:.0f} KB)")
    sys.exit(2 if any_break else 0)


if __name__ == "__main__":
    main()
