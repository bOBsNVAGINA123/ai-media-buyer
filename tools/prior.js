/* Which rendered tables show numbers with NO prior-period comparison?
   This one cannot be a static scan: the cells are built at render time, so the only
   honest check is to render every tab and read the DOM. Paste runAudit() into the
   harness console (or drive it from the browser tool) after boot().

   A table is flagged when it has >=6 numeric cells and under a third of them carry a
   delta marker. Flagging is not the same as a defect: some tables are structurally
   lifetime (branch cohorts) or come from a fixed-window pull with no prior available
   (TikTok has no daily series at all). Those must SAY so on the card; the ones that
   have the data and simply never showed it are the real finds. */
module.exports.source = `
window.runAudit = async function(tabs){
  /* v2: the first version missed a plain signed percentage, so it flagged "Best sellers"
     (which has a VS PRIOR column reading "+66% / +E£238,018") as having no prior at all.
     A delta is any arrow, any vs/was/from/→, OR any signed number. */
  const DELTA=/[▲▼]|\\bvs\\b|was\\s|from\\s|→|no prior|part week|[+−-]\\s?[\\d£E]/i;
  /* v3: two kinds of false positive kept coming back and both are structural, so they are
     rules now rather than something to re-read every sweep.
     1. A COLUMN can carry the prior for the whole row -- "Prior | Now | Change | %" or a
        "vs prior" header. Per-cell scanning cannot see that and flagged "Trending now",
        which is built entirely out of a prior comparison.
     2. A card that SAYS it is lifetime, a fixed-window pull, or has no daily series behind
        it is not hiding anything; that was the agreed answer for those. */
  const HDRPRIOR=/\\bprior\\b|\\bchange\\b|\\bvs\\b|\\bwas\\b|\\bdelta\\b|\\bmove\\b|yoy|\\bly\\b/i;
  const EXCUSED=/lifetime|all[- ]history|fixed \\d*\\s*-?\\s*day|fixed window|fixed last-\\d+|not your date box|does not follow the date box|no prior|by month|month by month|by cohort|cohort month|per month|ACTUAL|ledger detail|every posting|line-level|decile/i;
  const NUM=/E£\\s?[\\d,]|^\\s*[\\d,]+(\\.\\d+)?\\s*(%|×|x)?\\s*$/;
  const out=[];
  for(const t of tabs){
    try{tab(t);}catch(e){continue;}
    await new Promise(r=>setTimeout(r,2600));
    [...document.querySelectorAll('.card')].filter(c=>c.offsetParent!==null).forEach(c=>{
      const title=((c.querySelector('.ct')||{}).innerText||'').trim().slice(0,52);
      const tbl=c.querySelector('table'); if(!tbl||tbl.rows.length<3) return;
      let num=0, withD=0;
      [...tbl.rows].slice(1,15).forEach(r=>[...r.cells].slice(1).forEach(cell=>{
        const tx=(cell.innerText||'').trim(); if(!tx||tx==='—') return;
        if(!NUM.test(tx)) return;
        num++; if(DELTA.test(tx)) withD++;
      }));
      const hdr=[...(tbl.rows[0]||{cells:[]}).cells].some(h=>HDRPRIOR.test(h.innerText||''));
      if(num>=6 && withD/num < 0.34 && !hdr && !EXCUSED.test(c.textContent))
        out.push({tab:t,title,cells:num,withPrior:withD,pct:Math.round(withD/num*100)});
    });
  }
  return out.sort((a,b)=>a.pct-b.pct);
};`;
console.log('tools/prior.js is a BROWSER audit, not a static check.');
console.log('Run its .source in the harness after boot(), then: await runAudit([...tabs])');
