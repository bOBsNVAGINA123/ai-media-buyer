/* Does any carried-forward payload key use a truthy whole-object fallback?

   `XTRA.get(k) or prev.get(k)` reads as "this run's value, else last run's". It is not: a
   dict that only HALF filled this run is still truthy, so the carried-forward half is
   thrown away silently. Measured 2026-10-05: pull_pos_customers() is skipped whenever the
   branch window already reaches today, so XTRA["lag"] held only {"shop": ...} and the
   Stores rows vanished from "Time between orders" -- its own subtitle printed "half of
   store repeat gaps fall inside - days". `cube` and `jour` had each been hand-patched for
   exactly this already, which is how you know it recurs.

   Rule: a key written at MORE THAN ONE site in the collector must be merged per key
   (_xm() in ourkids_live.py), never with `or`.

   usage: node tools/carry.js [path-to-ourkids_live.py] */
const fs=require('fs');
const p=process.argv[2]||(__dirname+'/../ourkids_live.py');
const s=fs.readFileSync(p,'utf8');
const writes={};
for(const m of s.matchAll(/XTRA(?:\.setdefault\(|\[)\s*"([A-Za-z_0-9]+)"/g))
  writes[m[1]]=(writes[m[1]]||0)+1;
const orFallback=new Set();
for(const m of s.matchAll(/XTRA\.get\("([A-Za-z_0-9]+)"\)\s*or\s*prev/g)) orFallback.add(m[1]);
const multi=Object.keys(writes).filter(k=>writes[k]>1);
const bad=multi.filter(k=>orFallback.has(k));
console.log('keys XTRA writes at more than one site: '+(multi.join(', ')||'none'));
console.log('keys falling back with `or prev`:        '+([...orFallback].join(', ')||'none'));
if(bad.length){
  console.log('\nPARTIAL-DICT HAZARD — these are filled by several code paths AND fall back with `or`:');
  bad.forEach(k=>console.log('  XTRA["'+k+'"]  written at '+writes[k]+' sites  ->  use _xm("'+k+'")'));
}else{
  console.log('\nno partial-dict hazard: every multi-site key is merged per key');
}
process.exit(bad.length?1:0);
