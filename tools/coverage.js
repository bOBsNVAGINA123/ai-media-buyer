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
const FEEDS=[['meta','mads','madsW','mspend'],['google','gads','gadsW','gspend'],['tiktok','tads','tadsW',null]];
const A=O.ad||{};
let bad=0;
wins.forEach(W=>{
 console.log('\n== trailing '+W+' days');
 FEEDS.forEach(([name,adsKey,winKey,accKey])=>{
  const ads=(O[adsKey]||[]).filter(a=>a&&a.d&&a.d.sp), w=O[winKey];
  if(!ads.length||!w||!w.start){console.log('  '+name+': no feed'); return;}
  if(!accKey||!A[accKey]){console.log('  '+name+': no account series to check against'); return;}
  const n=w.n||60, b=n-1, a0=Math.max(0,b-(W-1));
  let feed=0; ads.forEach(x=>{for(let i=a0;i<=b;i++)feed+=x.d.sp[i]||0;});
  const as=Date.parse(A.start), ws=Date.parse(w.start), S=A[accKey];
  const i0=Math.round((ws+a0*864e5-as)/864e5), i1=Math.round((ws+b*864e5-as)/864e5);
  let acc=0,hit=0; for(let i=Math.max(0,i0);i<=Math.min(S.length-1,i1);i++){acc+=S[i]||0;hit++;}
  if(hit!==(b-a0+1)){console.log('  '+name+': account series does not span the window'); return;}
  const cov=acc?feed/acc:0, ok=cov>=MIN;
  if(!ok)bad++;
  console.log('  '+(ok?'ok  ':'SHORT')+' '+name.padEnd(7)+' feed E£'+Math.round(feed).toLocaleString()
   +'  account E£'+Math.round(acc).toLocaleString()+'  coverage '+(cov*100).toFixed(1)+'%'
   +(ok?'':'  <-- gap E£'+Math.round(acc-feed).toLocaleString()));
 });
});
console.log('\n'+(bad?bad+' feed/window pair(s) below '+(MIN*100)+'% coverage':'all feeds cover their account series'));
process.exit(bad?1:0);
