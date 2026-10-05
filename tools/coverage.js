/* Does each per-ad feed cover its platform's account spend?

   Found 2026-10-05: for 28 Sep to 4 Oct the Meta account series read E£198,386 while the
   sum of O.mads read E£156,155 -- 21.3% short, because the collector dropped ads under a
   60-day spend floor and then capped the rest at 120 per account. Google's two feeds agreed
   to E£1. Any card that sums the ad feed and presents it as the account was understating
   spend and overstating ROAS, silently, for as long as the cap had been binding.

   This is the cross-source agreement test for it: for each platform, compare the windowed
   sum of the per-ad daily series against the account daily series over the SAME DATES.

   usage: node tools/coverage.js <path-to-data.js> [windowDays=7,30]
   exit 1 if any platform covers less than MIN of its account spend. */
const fs=require('fs');
const MIN=0.95;
const p=process.argv[2]||(__dirname+'/../okv/data.js');
global.window={};
eval(fs.readFileSync(p,'utf8'));
const O=window.O;
const wins=(process.argv[3]||'7,30').split(',').map(Number);
/* Each row: platform, ad array, window, and the metrics to reconcile as
   [label, per-ad daily key, account series key]. Clicks are checked against moclk
   (outbound) NOT mclk: mclk is Meta's `clicks`, which counts likes, comments, profile
   taps and image expands, and reconciling outbound clicks against it reads 52% and
   looks like a coverage hole when it is a different metric. */
const FEEDS=[
 ['meta','mads','madsW',[['spend','sp','mspend'],['value','pv','mecomrev'],['orders','pu','mpur'],['clicks','oc','moclk']]],
 ['google','gads','gadsW',[['spend','sp','gspend'],['value','pv','gecomrev']]],
 ['tiktok','tads','tadsW',[['spend','sp','tspend']]]];
const A=O.ad||{};
let bad=0;
wins.forEach(W=>{
 console.log('\n== trailing '+W+' days');
 FEEDS.forEach(([name,adsKey,winKey,metrics])=>{
  const ads=(O[adsKey]||[]).filter(a=>a&&a.d&&a.d.sp), w=O[winKey];
  if(!ads.length||!w||!w.start){console.log('  '+name+': no feed'); return;}
  const n=w.n||60, b=n-1, a0=Math.max(0,b-(W-1));
  const as=Date.parse(A.start), ws=Date.parse(w.start);
  metrics.forEach(([ml,adK,accK])=>{
   const tag=(name+' '+ml).padEnd(16);
   if(!A[accK]){console.log('  skip  '+tag+' no account series '+accK+' yet'); return;}
   let feed=0; ads.forEach(x=>{const d=x.d[adK]; if(d)for(let i=a0;i<=b;i++)feed+=d[i]||0;});
   const S=A[accK];
   const i0=Math.round((ws+a0*864e5-as)/864e5), i1=Math.round((ws+b*864e5-as)/864e5);
   let acc=0,hit=0; for(let i=Math.max(0,i0);i<=Math.min(S.length-1,i1);i++){acc+=S[i]||0;hit++;}
   if(hit!==(b-a0+1)){console.log('  skip  '+tag+' account series does not span the window'); return;}
   const cov=acc?feed/acc:0, ok=cov>=MIN;
   if(!ok)bad++;
   console.log('  '+(ok?'ok   ':'SHORT')+' '+tag+' feed '+Math.round(feed).toLocaleString().padStart(12)
    +'  account '+Math.round(acc).toLocaleString().padStart(12)+'  coverage '+(cov*100).toFixed(1)+'%'
    +(ok?'':'  <-- gap '+Math.round(acc-feed).toLocaleString()));
  });
 });
});
console.log('\n'+(bad?bad+' feed/window pair(s) below '+(MIN*100)+'% coverage':'all feeds cover their account series'));
process.exit(bad?1:0);
