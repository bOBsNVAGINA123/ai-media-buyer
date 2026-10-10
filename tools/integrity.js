/* Does the payload agree with Odoo, and do its own parts add up?

   Everything else in tools/ checks the PAGE. This checks the DATA. Run it after a heavy
   crawl, or whenever a number looks wrong and you need to know whether the collector or
   the renderer is lying.

   Reconciled by hand against Odoo on 10 Oct 2026, September 2026:
     branch revenue   payload 36,292,029  vs report.pos.order 36,292,028   0.00%
     branch GP        payload 12,017,906  vs 9,012,670 + rev x 0.082807    E£2 on E£12M
     ecom revenue     payload  7,593,000  vs sale.order amount_total       0.09% (k=1000)
   So the headline figures are right. What this file guards is that they STAY right and
   that the derived series keep adding up to them.

   The gaps that are real and must stay stated rather than fixed:
     the vendor daily series is 6.4% short of till revenue and 12.8% short of the Shopify
     leg, because it only carries lines Odoo can map to a vendor, and only the vendors the
     book shows (160 of 359, 81.9% of revenue). The movers card says so.

   usage: node tools/integrity.js <path-to-data.js> */
const fs=require('fs');
const p=process.argv[2]||(__dirname+'/../okv/data.js');
global.window={};
eval(fs.readFileSync(p,'utf8'));
const O=window.O||{};
let bad=0, warn=0;
const f=v=>Math.round(v).toLocaleString();
const ok=(t,d)=>console.log('  ok    '+t+(d?('  '+d):''));
const fail=(t,d)=>{bad++;console.log('  FAIL  '+t+(d?('  '+d):''));};
const soft=(t,d)=>{warn++;console.log('  note  '+t+(d?('  '+d):''));};

console.log('payload '+(O.lastSync||'?'));

/* 1. the 4% basis stamp must be exact, not truthy -- a half-corrected payload silently
      reverts every margin on the page */
console.log('\n1. gross-profit basis');
O.gpBasis==='govt4b' ? ok('gpBasis is govt4b') : fail('gpBasis is '+JSON.stringify(O.gpBasis)+', expected "govt4b"');

/* 2. branch months must be internally consistent: every branch present in O.pos should
      also be in the branch daily book, or a tab silently loses a shop */
console.log('\n2. branch coverage');
const posB=Object.keys(O.pos||{}), bnrB=Object.keys(O.bnrD||{}).filter(k=>k!=='_w');
const missing=posB.filter(b=>bnrB.indexOf(b)<0);
missing.length?fail(missing.length+' branch(es) in O.pos but not in bnrD',missing.join(', '))
             :ok(posB.length+' branches, all present in both O.pos and bnrD');

/* 3. no month may carry negative revenue, and margin must be inside a sane band -- a
      negative or a 90% margin is a mapping error, not a good month */
console.log('\n3. branch months are plausible');
let negR=0,oddM=0,n=0;
Object.keys(O.pos||{}).forEach(b=>Object.keys(O.pos[b]).forEach(m=>{
  const v=O.pos[b][m]||[]; const r=v[0]||0, g=v[1]||0; n++;
  if(r<0)negR++;
  if(r>0){const pct=g/r; if(pct<0.05||pct>0.75)oddM++;}}));
negR?fail(negR+' branch-months with negative revenue'):ok(n+' branch-months, none negative');
oddM?soft(oddM+' branch-months with a margin outside 5-75%','worth a look, not proof of a fault')
    :ok('every branch-month margin inside 5-75%');

/* 4. the vendor daily series is SIX wide after v93.6 -- shops without MOA, Shopify, MOA.
      A 2-wide series means the heavy crawl has not run since that change and the board is
      quietly showing its combined fallback. */
console.log('\n4. vendor daily series shape');
const w=((O.vend&&O.vend.rows&&O.vend.rows[0]&&O.vend.rows[0].d)||[]).length;
w>=6?ok('vday is '+w+' wide: shops / Shopify / Mall of Arabia split is live')
    :soft('vday is '+w+' wide','the shops / Shopify / MOA split needs a heavy crawl');

/* 5. the supplier book's coverage must be published, because the movers card quotes it */
console.log('\n5. supplier book coverage');
const cov=(O.vend&&O.vend.cov)||null;
cov&&cov.revPct?ok('book carries '+cov.shown+' of '+cov.vend+' suppliers, '+cov.revPct+'% of revenue')
               :fail('O.vend.cov.revPct missing','the movers card has nothing to state');

/* 6. per-vendor products must be ranked on RECENT revenue. Ranked on lifetime, a seasonal
      vendor got last season's winners: 6.3% of its level and 2.4% of its move. */
console.log('\n6. product drill reaches the move');
const PR=(O.prodv&&O.prodv.rows)||[];
const byV={}; PR.forEach(x=>{if(x.vn)byV[x.vn]=(byV[x.vn]||0)+1;});
const big=Object.keys(byV).sort((a,b)=>byV[b]-byV[a])[0];
PR.length>=3000?ok(PR.length+' product rows, '+PR.filter(x=>x.d).length+' with a daily series; most-covered vendor has '+byV[big]+' lines')
               :soft(PR.length+' product rows','under 3,000 suggests the heavy crawl predates the quota raise');

/* 7. every ad feed must still reconcile to its own account series -- the Meta feed was
      21.3% short before v92 and nothing on the page knew */
console.log('\n7. ad feeds vs their account series (last 7 complete days)');
const A=O.ad||{};
[['meta','mads','madsW','mspend'],['google','gads','gadsW','gspend']].forEach(([nm,ak,wk,sk])=>{
  const ads=(O[ak]||[]).filter(x=>x&&x.d&&x.d.sp), W=O[wk];
  if(!ads.length||!W||!A[sk]){soft(nm+': no feed to check');return;}
  const n2=W.n||60,b=n2-1,a0=Math.max(0,b-6);
  let feed=0; ads.forEach(x=>{for(let i=a0;i<=b;i++)feed+=x.d.sp[i]||0;});
  const as=Date.parse(A.start), ws=Date.parse(W.start);
  const i0=Math.round((ws+a0*864e5-as)/864e5), i1=Math.round((ws+b*864e5-as)/864e5);
  let acc=0; for(let i=Math.max(0,i0);i<=Math.min(A[sk].length-1,i1);i++)acc+=A[sk][i]||0;
  const c=acc?feed/acc:0;
  (c>=0.95&&c<=1.05)?ok(nm+' spend '+(c*100).toFixed(1)+'% of account','feed E£'+f(feed)+' vs E£'+f(acc))
                    :fail(nm+' spend '+(c*100).toFixed(1)+'% of account','feed E£'+f(feed)+' vs E£'+f(acc));});

/* 8. the part day must be flagged, or every trailing window reads as a fall */
console.log('\n8. window hygiene');
(O.partial&&O.fullEnd&&O.fullEnd<O.partial)
  ?ok('partial '+O.partial+', last complete day '+O.fullEnd)
  :fail('partial/fullEnd missing or inconsistent',JSON.stringify({partial:O.partial,fullEnd:O.fullEnd}));

console.log('\n'+(bad?(bad+' FAILURE(S)'+(warn?(' and '+warn+' note(s)'):'')):('clean'+(warn?(' with '+warn+' note(s)'):''))));
process.exit(bad?1:0);
