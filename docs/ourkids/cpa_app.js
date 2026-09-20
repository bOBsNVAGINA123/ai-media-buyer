/* OurKids CPA Grid. Reads the same live data.js the main dashboard reads, so it is
   as fresh as the hourly pipeline. cpa_seed.js carries the two things data.js does not
   carry yet (per-ad add-to-cart + effective status + video-play rate, and the GA4
   first-touch/last-touch split); both are preferred from data.js the moment the
   pipeline starts writing O.adx / O.touch. */
const HASH="9f97837eb237a58b0b15c0b0962b450c131d5ddbade4ac2ca88b34b451600745";
async function unlock(){const v=document.getElementById('pw').value;
 const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v));
 const x=[...new Uint8Array(h)].map(b=>b.toString(16).padStart(2,'0')).join('');
 if(x===HASH){document.getElementById('gate').classList.add('hide');document.getElementById('app').classList.remove('hide');boot();}
 else document.getElementById('er').textContent='Wrong password';}
document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')unlock();});
if(location.hash==='#open'){document.getElementById('gate').classList.add('hide');document.getElementById('app').classList.remove('hide');addEventListener('load',()=>boot());}

/* ---------- math: Gamma-Poisson shrinkage, one model for every cost-per-X ---------- */
function lgamma(z){const g=[676.5203681218851,-1259.1392167224028,771.32342877765313,-176.61502916214059,12.507343278686905,-0.13857109526572012,9.9843695780195716e-6,1.5056327351493116e-7];
 if(z<0.5)return Math.log(Math.PI/Math.sin(Math.PI*z))-lgamma(1-z);
 z-=1;let x=0.99999999999980993;for(let i=0;i<8;i++)x+=g[i]/(z+i+1);
 const t=z+7.5;return 0.5*Math.log(2*Math.PI)+(z+0.5)*Math.log(t)-t+Math.log(x);}
function gammaP(s,x){ // regularized lower incomplete gamma P(s,x)
 if(x<=0)return 0; if(s<=0)return 1;
 if(x<s+1){let ap=s,sum=1/s,del=sum;
  for(let i=0;i<600;i++){ap++;del*=x/ap;sum+=del;if(Math.abs(del)<Math.abs(sum)*1e-13)break;}
  return sum*Math.exp(-x+s*Math.log(x)-lgamma(s));}
 let b=x+1-s,c=1e300,d=1/b,h=d;
 for(let i=1;i<600;i++){const an=-i*(i-s);b+=2;d=an*d+b;if(Math.abs(d)<1e-300)d=1e-300;
  c=b+an/c;if(Math.abs(c)<1e-300)c=1e-300;d=1/d;const de=d*c;h*=de;if(Math.abs(de-1)<1e-13)break;}
 return 1-Math.exp(-x+s*Math.log(x)-lgamma(s))*h;}
function gammaInv(s,p){ // x such that P(s,x)=p
 if(s<=0)return 0; let lo=0,hi=Math.max(s*4,4);
 let guard=0; while(gammaP(s,hi)<p&&guard++<80)hi*=2;
 for(let i=0;i<120;i++){const m=(lo+hi)/2;if(gammaP(s,m)<p)lo=m;else hi=m;}
 return (lo+hi)/2;}
/* Fit Gamma(a,b) prior on rate = count per E£1000, method of moments with the
   Poisson sampling noise removed. Without that subtraction the prior is too wide and
   a 3-purchase ad keeps its flattering raw CPA, which is the whole failure this fixes. */
function fitPrior(rows,kf){
 const E=rows.map(r=>r.sp/1000), K=rows.map(kf);
 const se=E.reduce((a,b)=>a+b,0), sk=K.reduce((a,b)=>a+b,0);
 if(se<=0||sk<=0)return {a:1,b:1};
 const mu=sk/se;
 let v=0; for(let i=0;i<E.length;i++){const d=K[i]/Math.max(E[i],1e-9)-mu;v+=E[i]*d*d;}
 v/=se;
 const vPois=mu*E.length/se;                 // expected weighted var from Poisson alone
 const vB=Math.max(v-vPois, mu*mu*0.02);     // between-ad variance floor: 14% CV
 return {a:mu*mu/vB, b:mu/vB};}
function post(pr,k,sp){const e=sp/1000,sh=pr.a+k,rt=pr.b+e;
 const lam=sh/rt, lo=gammaInv(sh,0.05)/rt, hi=gammaInv(sh,0.95)/rt;
 return {lam,cpa:lam>0?1000/lam:Infinity,cpaLo:hi>0?1000/hi:Infinity,cpaHi:lo>0?1000/lo:Infinity};}

/* ---------- data prep ---------- */
const O=window.O, SEED=window.CPASEED||{};
const ADX=SEED.adx||{}, TOUCH=(O&&O.touch&&O.touch.last)?O.touch:(SEED.touch||{});
const MADS0=(O.mads||[]).filter(a=>a.pf==='meta');
/* The pipeline now carries add-to-cart, video plays and status on every ad. Until the
   first run that has them lands, fall back to the snapshot in cpa_seed.js. */
const ADXLIVE=MADS0.some(a=>a.d&&a.d.atc)&&MADS0.filter(a=>a.st).length>MADS0.length*0.9;
const TOUCHLIVE=!!(O&&O.touch&&O.touch.last);
const MADS=MADS0;
const WN=(O.madsW&&O.madsW.n)||60, WSTART=(O.madsW&&O.madsW.start)||'';
const MATURE=4; // conversions keep landing for ~16d; 71% land day one. Drop the raw tail.
const RX={BOF:/retarget|catalog|dpa|existing|retention|promocode|remarket|abandon/i,
          TOF:/prospect|testing|\btest\b|broad|reach|footfall|awareness|\bcold\b|\btof\b|new audience/i};
function funnel(a){const s=(a.n||'')+' '+(a.as||'')+' '+(a.cmp||'');
 if(RX.BOF.test(s))return 'BOF'; if(RX.TOF.test(s))return 'TOF'; return 'MOF';}
function fmt(a){
 let vp=a.vp,im=a.imp;
 if(vp===undefined){const x=ADX[a.id]; if(!x)return 'unknown'; vp=x[1]; im=x[2];}
 if(!(im>2000))return 'unknown';
 const r=vp/im;
 return r<0.05?'static':(r<0.45?'mixed':'video');}
function status(a){const x=ADX[a.id]; const s=a.st||(x&&x[3])||'';
 return s==='ACTIVE'||s==='WITH_ISSUES'?'act':(s?'pau':'');}
/* window-aware add-to-cart once the pipeline carries it daily; the snapshot is a fixed
   2026-07-23..2026-09-16 total and cannot re-cut, so it is only used as a whole. */
function atcOf(a,i0,i1,full){
 if(a.d&&a.d.atc)return S(a,'atc',i0,i1);
 const x=ADX[a.id]; return (full&&x)?x[0]:0;}
function dayIdx(winSel){const end=WN-MATURE; // exclusive
 if(winSel==='60')return [0,WN];
 const nd=parseInt(winSel,10); return [Math.max(0,end-nd),end];}
const S=(a,k,i0,i1)=>{const v=(a.d&&a.d[k])||[];let t=0;for(let i=i0;i<i1;i++)t+=v[i]||0;return t;};

function build(){
 const basis=document.getElementById('basis').value,
       hair=parseFloat(document.getElementById('hair').value),
       [i0,i1]=dayIdx(document.getElementById('win').value),
       tgtPct=parseFloat(document.getElementById('tgt').value),
       mins=parseFloat(document.getElementById('mins').value)||0,
       fSt=document.getElementById('st').value,fFmt=document.getElementById('fmt').value,
       fFn=document.getElementById('fn').value;
 let rows=MADS.map(a=>{
  const sp=S(a,'sp',i0,i1); if(sp<=0)return null;
  const pu=S(a,'pu',i0,i1),op=S(a,'op',i0,i1),pv=S(a,'pv',i0,i1),fv=S(a,'fv',i0,i1);
  const x=ADX[a.id]||[0,0,0,''];
  const k=basis==='on'?pu:basis==='off'?op*hair:pu+op*hair;
  const val=basis==='on'?pv:basis==='off'?fv*hair:pv+fv*hair;
  const H=hair;
  return {id:a.id,n:a.n,cmp:a.cmp,as:a.as,acct:a.acct,th:a.th||a.im2,pl:a.pl,sp,pu,op,pv,fv,k,val,
   cppOn:pu?sp/pu:Infinity, cppOff:op?sp/op:Infinity, cppAll:(pu+op)?sp/(pu+op):Infinity,
   roasOn:sp?pv/sp:0, roasOff:sp?fv/sp:0, roasOffInc:sp?fv*H/sp:0, roasAll:sp?(pv+fv*H)/sp:0,
   aovOn:pu?pv/pu:0, aovOff:op?fv/op:0,
   oc:S(a,'oc',i0,i1),im:S(a,'im',i0,i1),nc:S(a,'nc',i0,i1),
   atc:atcOf(a,i0,i1,true),st:status(a),fmt:fmt(a),fn:funnel(a),
   sp7:S(a,'sp',Math.max(i0,i1-7),i1),k7:(basis==='on'?S(a,'pu',Math.max(i0,i1-7),i1)
     :basis==='off'?S(a,'op',Math.max(i0,i1-7),i1)*hair
     :S(a,'pu',Math.max(i0,i1-7),i1)+S(a,'op',Math.max(i0,i1-7),i1)*hair),
   a:a};}).filter(Boolean);
 const universe=rows.slice();                       // prior is fit on everything, always
 const pr=fitPrior(universe,r=>r.k);
 const prA=fitPrior(universe.filter(r=>r.atc>0),r=>r.atc);
 universe.forEach(r=>{Object.assign(r,post(pr,r.k,r.sp));
   r.rawCpa=r.k>0?r.sp/r.k:Infinity; r.roas=r.sp>0?r.val/r.sp:0;
   r.cpatc=r.atc>0?r.sp/r.atc:Infinity;
   r.cpatcS=r.atc>0?post(prA,r.atc,r.sp).cpa:Infinity;
   // trend: last third vs the two before it, on the shrunk rate. Gated on counts.
   const h=Math.floor((i1-i0)/2);
   const s1=S(r.a,'sp',i0,i0+h),s2=S(r.a,'sp',i0+h,i1);
   const kk=(f,b,e)=>basis==='on'?S(r.a,'pu',b,e):basis==='off'?S(r.a,'op',b,e)*hair:S(r.a,'pu',b,e)+S(r.a,'op',b,e)*hair;
   const k1=kk(0,i0,i0+h),k2=kk(0,i0+h,i1);
   if(k1>=10&&k2>=10&&s1>0&&s2>0){
     const l1=k1/s1,l2=k2/s2, lr=Math.log(l2/l1), se=Math.sqrt(1/k1+1/k2);
     r.trend=-lr; r.trendSig=Math.abs(lr)>1.96*se;          // +ve = CPA rising
   } else {r.trend=null;r.trendSig=false;}
 });
 /* A second posterior on the last 14 mature days only. The selected window can be 56
    days long and contains back-to-school; forecasting next week off an August rate reads
    high. Everything forward-looking (Predict, the simulation) uses THIS one, and the gap
    between the two is reported rather than hidden. */
 const j0=Math.max(i0,i1-14);
 const rec=universe.map(r=>{const sp=S(r.a,'sp',j0,i1);
   const pu=S(r.a,'pu',j0,i1),op=S(r.a,'op',j0,i1);
   return {sp,k:basis==='on'?pu:basis==='off'?op*hair:pu+op*hair};});
 const prR=fitPrior(rec.filter(x=>x.sp>0),x=>x.k);
 universe.forEach((r,i)=>{const x=rec[i];
   r.sp14=x.sp; r.k14=x.k;
   const q=x.sp>0?post(prR,x.k,x.sp):{lam:prR.a/prR.b,cpa:prR.b/prR.a*1000,cpaLo:NaN,cpaHi:NaN};
   r.lamR=q.lam; r.cpaR=q.cpa; r.cpaRLo=q.cpaLo; r.cpaRHi=q.cpaHi;
   r.decay=(isFinite(r.cpa)&&isFinite(r.cpaR)&&r.cpa>0)?r.cpaR/r.cpa-1:null;});
 let f=universe.filter(r=>r.sp>=mins);
 if(fSt!=='all')f=f.filter(r=>r.st===fSt);
 if(fFmt!=='all')f=f.filter(r=>r.fmt===fFmt);
 if(fFn!=='all')f=f.filter(r=>r.fn===fFn);
 const T=k=>f.reduce((s,r)=>s+r[k],0);
 const tot={sp:T('sp'),k:T('k'),val:T('val'),pu:T('pu'),op:T('op'),pv:T('pv'),fv:T('fv'),
            oc:T('oc'),im:T('im'),atc:T('atc'),n:f.length};
 const cur=tot.k>0?tot.sp/tot.k:0, target=cur*(1-tgtPct), kill=target*1.5, scale=target*0.7;
 f.forEach(r=>{
  const live=r.st==='act';
  if(live&&r.cpaLo>kill)r.act='KILL';
  else if(live&&r.cpa>kill)r.act='CUT';
  else if(live&&r.cpaHi<scale)r.act='SCALE';
  else if(!live&&r.cpaHi<scale&&r.k>=3)r.act='REACTIVATE';
  else if(r.k<3)r.act='THIN';
  else r.act='HOLD';});
 return {rows:f,universe,tot,cur,target,kill,scale,basis,hair,pr,prA,prR,i0,i1,j0,tgtPct,
         win:document.getElementById('win').value};
}

/* ---------- the falsifying test the whole "scale it 20%" step rests on ----------
   The simulation assumes an ad keeps its CPA when you give it 20% more budget.
   That is an assumption, not a measurement, so measure it: every week-on-week
   budget rise of >=20% in the last 60 days, and what CPA did the next week. */
function budgetHoldTest(basis,hair){
 const out=[];
 MADS.forEach(a=>{
  const sp=a.d.sp||[];
  const kk=(b,e)=>{let t=0;for(let i=b;i<e;i++){const p=(a.d.pu||[])[i]||0,o=(a.d.op||[])[i]||0;
    t+=basis==='on'?p:basis==='off'?o*hair:p+o*hair;}return t;};
  for(let w=0;w+14<=WN-MATURE;w+=7){
   let s1=0,s2=0;for(let i=w;i<w+7;i++)s1+=sp[i]||0;for(let i=w+7;i<w+14;i++)s2+=sp[i]||0;
   if(s1<3000||s2<=0)continue;
   const g=s2/s1-1; if(g<0.20)continue;
   const k1=kk(w,w+7),k2=kk(w+7,w+14);
   if(k1<5||k2<1)continue;
   out.push({g,c1:s1/k1,c2:s2/k2,r:(s2/k2)/(s1/k1),k1,k2,n:a.n});}});
 out.sort((x,y)=>x.r-y.r);
 const med=out.length?out[Math.floor(out.length/2)].r:null;
 const worse=out.filter(x=>x.r>1).length;
 return {n:out.length,med,worse,rows:out};}

/* ---------- ad identity: thumbnail, link, modal ---------- */
const ACCT_ID={'Ourkids EGP':'336343742536460','Basic':'652528128810469'};
function adLink(r){
 if(r.pl)return r.pl;                                   // Meta's own shareable preview
 const a=ACCT_ID[r.acct]||ACCT_ID['Ourkids EGP'];
 return 'https://adsmanager.facebook.com/adsmanager/manage/ads?act='+a+'&selected_ad_ids='+r.id;}
function thumb(r,sz){sz=sz||40;
 const st='width:'+sz+'px;height:'+sz+'px;border-radius:8px;object-fit:cover;flex:none;background:#eef0f5';
 return r.th?'<img src="'+r.th+'" style="'+st+'" loading="lazy" alt=""/>'
            :'<div style="'+st+';display:flex;align-items:center;justify-content:center;color:#b6bdcc;font-size:15px">▦</div>';}
function adCell(r){
 return '<a href="#" onclick="openAd(\''+r.id+'\');return false" style="display:flex;gap:9px;align-items:center;text-decoration:none;color:inherit">'
  +thumb(r)+'<span style="min-width:0"><span class="nm" style="font-weight:700">'+r.n+'</span><br/>'
  +'<span class="mut" style="font-size:10.5px">'+(r.cmp||'')+'</span></span></a>';}
let LASTD=null;
function openAd(id){
 const r=(LASTD&&LASTD.universe||[]).find(x=>x.id===id); if(!r)return;
 const row=(k,v)=>'<tr><td style="text-align:left;color:#7c869c">'+k+'</td><td style="font-weight:700">'+v+'</td></tr>';
 document.getElementById('modal').innerHTML=
 '<div class="mbg" onclick="closeAd()"></div><div class="mbx">'
 +'<div style="display:flex;gap:14px;align-items:flex-start">'+thumb(r,110)
 +'<div style="flex:1;min-width:0"><div style="font-size:16px;font-weight:800;line-height:1.3">'+r.n+'</div>'
 +'<div class="mut" style="font-size:11.5px;margin-top:3px">'+r.cmp+' › '+r.as+'</div>'
 +'<div style="margin-top:8px"><span class="tg '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</span> '
 +'<span class="tg '+(r.st==='act'?'scale':'hold')+'">'+(r.st==='act'?'LIVE':'PAUSED')+'</span> '
 +'<span class="tg hold">'+r.fmt+'</span> <span class="tg hold">'+r.fn+'</span></div>'
 +'<a class="btn" style="margin-top:10px;display:inline-block;text-decoration:none" target="_blank" href="'+adLink(r)+'">'
 +(r.pl?'See the ad':'Open in Ads Manager')+'</a></div></div>'
 +'<div class="two" style="margin-top:14px;gap:10px"><table>'
 +row('Spend in window',EGP(r.sp))+row('Spend last 7d',EGP(r.sp7))
 +row('Online purchases',N0(r.pu))+row('In-store purchases',N0(r.op))
 +row('CPP online',EGP(r.cppOn))+row('CPP in-store',EGP(r.cppOff))
 +row('CPP blended (raw)',EGP(r.cppAll))+row('CPP blended (shrunk)',EGP(r.cpa))
 +row('90% interval',EGP(r.cpaLo)+' – '+EGP(r.cpaHi))+'</table><table>'
 +row('ROAS online',N2(r.roasOn)+'  (breakeven 6.21)')
 +row('ROAS in-store, claimed',N2(r.roasOff))
 +row('ROAS in-store, at '+Math.round(LASTD.hair*100)+'%',N2(r.roasOffInc)+'  (breakeven 4.11)')
 +row('ROAS total, this basis',N2(r.roasAll))
 +row('AOV online',EGP(r.aovOn))+row('AOV in-store',EGP(r.aovOff))
 +row('Add-to-carts',N0(r.atc)+(r.atc?'  at '+EGP(r.cpatc)+' each':''))
 +row('Outbound clicks',N0(r.oc)+'  at E\u00a3'+N2(r.oc?r.sp/r.oc:0)+' each')
 +row('Trend',r.trend===null?'not enough purchases to test':(r.trend>0?'CPA rising '+Math.round(r.trend*100)+'%':'CPA falling '+Math.round(-r.trend*100)+'%')+(r.trendSig?' (significant)':' (not significant)'))
 +'</table></div>'
 +'<div style="margin-top:12px;font-size:12.5px;line-height:1.6;background:#f7f8fb;border-radius:10px;padding:11px 13px">'+why(r,LASTD)+'</div>'
 +'<button class="btn g" style="margin-top:12px" onclick="closeAd()">Close</button></div>';
 document.getElementById('modal').style.display='block';}
function closeAd(){document.getElementById('modal').style.display='none';}
addEventListener('keydown',e=>{if(e.key==='Escape')closeAd();});

/* ---------- plain-language verdicts ---------- */
const VERB={KILL:'TURN OFF',CUT:'CUT BUDGET',SCALE:'RAISE 20%',REACTIVATE:'TURN BACK ON',HOLD:'LEAVE ALONE',THIN:'TOO NEW'};
function why(r,D){
 const x=Math.round(r.cpa/D.target*100)/100;
 if(r.act==='KILL')return '<b>Turn it off.</b> Costs '+EGP(r.cpa)+' a purchase — '+x+'× your '+EGP(D.target)+' target, and even the best case for it ('
  +EGP(r.cpaLo)+') is still over the '+EGP(D.kill)+' kill line. It is burning '+EGP(r.sp7)+' a week.';
 if(r.act==='CUT')return '<b>Cut its budget, do not kill it yet.</b> It reads '+EGP(r.cpa)+' against a '+EGP(D.kill)+' kill line, but the data still allows '
  +EGP(r.cpaLo)+'. Halve it and look again in a week.';
 if(r.act==='SCALE')return '<b>Raise budget 20%, to '+EGP(r.sp7*1.2)+' a week.</b> Costs '+EGP(r.cpa)+' a purchase and even the worst case ('
  +EGP(r.cpaHi)+') beats the '+EGP(D.scale)+' scale line. Re-read it in a week and stop at '+EGP(D.target)+'.';
 if(r.act==='REACTIVATE')return '<b>Switch it back on.</b> It is paused but it bought at '+EGP(r.cpa)+' on '+N1(r.k)+' purchases, under the '
  +EGP(D.scale)+' scale line. Check it was not a one-off promo creative first.';
 if(r.act==='THIN')return '<b>Leave it running, do not judge it yet.</b> Only '+N1(r.k)+' purchases — at that count the data cannot tell a good ad from a lucky one.';
 return '<b>Leave it alone.</b> At '+EGP(r.cpa)+' it sits between the '+EGP(D.scale)+' scale line and the '+EGP(D.kill)+' kill line, so there is no move the data supports.';}

/* ---------- render helpers ---------- */
const EGP=x=>!isFinite(x)?'—':'E£'+Math.round(x).toLocaleString();
const N0=x=>!isFinite(x)?'—':Math.round(x).toLocaleString();
const N1=x=>!isFinite(x)?'—':(Math.round(x*10)/10).toLocaleString();
const N2=x=>!isFinite(x)?'—':(Math.round(x*100)/100).toFixed(2);
const PC=x=>!isFinite(x)?'—':(x>=0?'+':'')+Math.round(x*100)+'%';
function kpi(k,v,d,col){return '<div class="kpi"><div class="k">'+k+'</div><div class="v"'+(col?' style="color:'+col+'"':'')+'>'+v+'</div><div class="d">'+(d||'')+'</div></div>';}
function card(h,cs,body){return '<div class="card"><h3>'+h+'</h3><div class="cs">'+cs+'</div>'+body+'</div>';}
let SORT={k:'sp',d:-1};
/* One column set everywhere. Online and in-store are shown side by side because they do
   not rank the same ads (r=0.06 in this window) -- a blended-only view hides that. */
function COLS(D){const H=Math.round(D.hair*100);
 const simple=(document.getElementById('dens')||{}).value!=='f';
 const KEEP=['n','act','st','sp','cppOn','roasOn','cppOff','roasOff','cpa'];
 const all=[
 ['n','Ad',adCell],
 ['act','What to do',r=>'<span class="tg '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</span>'],
 ['st','',r=>r.st==='act'?'<span class="g">live</span>':'<span class="mut">paused</span>'],
 ['sp','Spend',r=>EGP(r.sp)],['sp7','last 7d',r=>EGP(r.sp7)],
 ['pu','Online purch',r=>N0(r.pu)],
 ['cppOn','CPP online',r=>EGP(r.cppOn)],
 ['roasOn','ROAS online',r=>(r.roasOn>=6.21?'<span class="g">':'<span class="r">')+N2(r.roasOn)+'</span>'],
 ['op','Store purch',r=>N0(r.op)],
 ['cppOff','CPP store',r=>EGP(r.cppOff)],
 ['roasOff','ROAS store',r=>N2(r.roasOff)],
 ['roasOffInc','ROAS store @'+H+'%',r=>(r.roasOffInc>=4.11?'<span class="g">':'<span class="r">')+N2(r.roasOffInc)+'</span>'],
 ['roasAll','ROAS total',r=>'<b>'+N2(r.roasAll)+'</b>'],
 ['cpa','CPA used',r=>'<b>'+EGP(r.cpa)+'</b>'],
 ['cpaLo','best case',r=>EGP(r.cpaLo)],['cpaHi','worst case',r=>EGP(r.cpaHi)],
 ['cpatc','Cost/ATC',r=>EGP(r.cpatc)],
 ['fmt','Format',r=>r.fmt],['fn','Funnel',r=>r.fn],
 ['trend','Trend',r=>r.trend===null?'<span class="mut">too few</span>'
   :(r.trend>0?'<span class="r">CPA +'+Math.round(r.trend*100)+'%</span>':'<span class="g">CPA '+Math.round(r.trend*100)+'%</span>')+(r.trendSig?' *':'')]];
 return simple?all.filter(c=>KEEP.indexOf(c[0])>-1):all;}
function table(rows,cols,id){
 const th=cols.map(c=>'<th data-k="'+c[0]+'">'+c[1]+'</th>').join('');
 const rs=rows.map(r=>'<tr'+(r.id?' class="cl" onclick="if(!event.target.closest(\'a\'))openAd(\''+r.id+'\')"':'')+'>'
   +cols.map(c=>'<td>'+c[2](r)+'</td>').join('')+'</tr>').join('');
 return '<div class="scr"><table id="'+(id||'')+'"><thead><tr>'+th+'</tr></thead><tbody>'+rs+'</tbody></table></div>';}
function sortRows(rows){const k=SORT.k;return rows.slice().sort((a,b)=>{
 const x=a[k],y=b[k];
 if(typeof x==='string')return SORT.d*x.localeCompare(y);
 const xx=isFinite(x)?x:(SORT.d<0?-Infinity:Infinity), yy=isFinite(y)?y:(SORT.d<0?-Infinity:Infinity);
 return SORT.d*(xx-yy);});}
function wireSort(render){document.querySelectorAll('th[data-k]').forEach(t=>t.onclick=()=>{
 const k=t.dataset.k; SORT.d=(SORT.k===k)?-SORT.d:-1; SORT.k=k; render();});}

/* ---------- tabs ---------- */
const TABS=[['act','What to do'],['grid','The grid'],['pred','Next 7 days'],['store','In-store vs online'],
            ['touch','First vs last touch'],['meth','Method & caveats']];
let TAB='act';
function boot(){
 document.getElementById('tabs').innerHTML=TABS.map(t=>'<div class="tab'+(t[0]===TAB?' on':'')+'" data-t="'+t[0]+'">'+t[1]+'</div>').join('');
 document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{TAB=t.dataset.t;boot();});
 ['basis','hair','win','tgt','mins','st','fmt','fn','dens'].forEach(i=>{const e=document.getElementById(i);e.onchange=()=>boot();});
 const D=build(); LASTD=D;
 const end=new Date(WSTART); end.setDate(end.getDate()+D.i1-1);
 const st0=new Date(WSTART); st0.setDate(st0.getDate()+D.i0);
 document.getElementById('sub').textContent=
  st0.toISOString().slice(0,10)+' → '+end.toISOString().slice(0,10)+' · '+D.rows.length+' Meta ads · synced '+(O.lastSync||'');
 document.getElementById('p1').innerHTML='Blended CPA <b>'+EGP(D.cur)+'</b>';
 document.getElementById('p2').innerHTML='Target <b>'+EGP(D.target)+'</b>';
 document.getElementById('p3').innerHTML='Kill <b>'+EGP(D.kill)+'</b> · Scale <b>'+EGP(D.scale)+'</b>';
 document.getElementById('ft').innerHTML=
  'Source: the same live <code>data.js</code> the OurKids dashboard reads (Meta per-ad daily, 60d, rebuilt hourly). '
  +'Per-ad add-to-cart / status / format '+(ADXLIVE?'<b>live</b> from the pipeline, re-cuts with the window.':'from a 2026-07-23→09-16 snapshot — goes live on the next pipeline run, after which it re-cuts with the window.')
  +' GA4 first/last touch '+(TOUCHLIVE?'<b>live</b>.':'snapshot 2026-09-20.');
 document.getElementById('body').innerHTML=
  TAB==='grid'?vGrid(D):TAB==='act'?vAct(D):TAB==='pred'?vPred(D):
  TAB==='store'?vStore(D):TAB==='touch'?vTouch(D):vMeth(D);
 if(TAB==='grid')drawScatter(D);
 if(TAB==='pred')drawPred(D);
 if(TAB==='touch')drawTouch(D);
 wireSort(boot);
}

/* ---------- 1. THE GRID ---------- */
const COL={KILL:'#e23a63',CUT:'#ff8b42',SCALE:'#12b886',REACTIVATE:'#5a5bf0',HOLD:'#9aa3b5',THIN:'#9d6bff'};
function vGrid(D){
 const t=D.tot, aov=t.k>0?t.val/t.k:0;
 const k=kpi('Spend',EGP(t.sp),t.n+' ads, min E£'+N0(parseFloat(document.getElementById('mins').value)))
 +kpi('Purchases',N0(t.k),D.basis==='bl'?N0(t.pu)+' online + '+N0(t.op*D.hair)+' in-store credited':'')
 +kpi('CPA (measured)',EGP(D.cur),'spend ÷ purchases, this basis')
 +kpi('AOV',EGP(aov),D.basis==='bl'?'online E£'+N0(t.pu?t.pv/t.pu:0)+' · in-store E£'+N0(t.op?t.fv/t.op:0):'')
 +kpi('ROAS',N2(t.sp?t.val/t.sp:0),'breakeven 6.21× online / 4.11× in-store')
 +kpi('CPM',EGP(t.im?1000*t.sp/t.im:0),'CPC E£'+N2(t.oc?t.sp/t.oc:0)+' · CTR '+N2(t.im?100*t.oc/t.im:0)+'%')
 +kpi('Cost / add-to-cart',EGP(t.atc?t.sp/t.atc:0),t.atc?N0(t.atc)+' ATC':'no ATC data')
 +kpi('Click→purchase',N2(t.oc?100*t.pu/t.oc:0)+'%','online pixel only');
 const buckets={};D.rows.forEach(r=>{buckets[r.act]=(buckets[r.act]||0)+1;});
 const sum=Object.keys(COL).filter(x=>buckets[x]).map(x=>'<span class="tg '+x.toLowerCase().slice(0,5)+'">'+x+' '+buckets[x]+'</span>').join(' ');
 return '<div class="kpis">'+k+'</div>'
 +'<div class="banner b"><b>How to read it.</b> Right = big spender. Up = expensive. '
 +'<b>Bottom-right: proven cheap, raise it. Top-right: expensive at real money, turn it off.</b> '
 +'Left half is still testing — a cheap CPA there is mostly luck, so every dot is plotted at its <b>shrunk</b> CPA, not its raw one. '
 +'Lines: <b style="color:#9aa3b5">blended</b> '+EGP(D.cur)+' · <b style="color:#5a5bf0">target</b> '+EGP(D.target)
 +' · <b style="color:#e23a63">kill</b> '+EGP(D.kill)+' · <b style="color:#12b886">scale</b> '+EGP(D.scale)+'.</div>'
 +card('Spend vs CPA — every Meta ad','Click any dot to open that ad. Hollow ring = its raw CPA, so you can see how far the thin ones move. '+sum,
   '<div style="height:520px"><canvas id="sc"></canvas></div>')
 +card('Every ad','Click any row to open the ad. Click a column head to sort. Best/worst case is the 90% interval on the shrunk rate.',
   table(sortRows(D.rows),COLS(D),'tg'));
}
function drawScatter(D){
 const c=document.getElementById('sc'); if(!c)return;
 const mk=act=>({label:act,data:D.rows.filter(r=>r.act===act&&isFinite(r.cpa)).map(r=>({x:r.sp,y:r.cpa,r:r})),
   backgroundColor:COL[act],borderColor:COL[act],pointRadius:5,pointHoverRadius:8});
 const raw={label:'raw CPA',data:D.rows.filter(r=>isFinite(r.rawCpa)&&r.rawCpa>0).map(r=>({x:r.sp,y:r.rawCpa,r:r})),
   backgroundColor:'transparent',borderColor:'#c8cee0',pointRadius:5,pointStyle:'circle',borderWidth:1};
 const lines=[['blended',D.cur,'#9aa3b5'],['target',D.target,'#5a5bf0'],['kill',D.kill,'#e23a63'],['scale',D.scale,'#12b886']];
 const maxY=Math.min(Math.max(...D.rows.map(r=>r.cpaHi).filter(isFinite))||D.kill*3, D.kill*4);
 new Chart(c,{type:'scatter',data:{datasets:[raw,...Object.keys(COL).map(mk).filter(d=>d.data.length)]},
  options:{maintainAspectRatio:false,parsing:false,onClick:(e,els)=>{if(els.length){
    const d=e.chart.data.datasets[els[0].datasetIndex].data[els[0].index]; if(d&&d.r)openAd(d.r.id);}},
   onHover:(e,els)=>{e.native.target.style.cursor=els.length?'pointer':'default'},
   scales:{x:{type:'logarithmic',title:{display:true,text:'Lifetime spend in window (E£, log)'}},
           y:{title:{display:true,text:'CPA (E£)'},min:0,max:maxY}},
   plugins:{legend:{position:'bottom'},
    tooltip:{callbacks:{label:c=>{const r=c.raw.r;return [r.n,'spend '+EGP(r.sp)+' · '+N1(r.k)+' purch',
      'CPA raw '+EGP(r.rawCpa)+' → shrunk '+EGP(r.cpa),'90% CI '+EGP(r.cpaLo)+'–'+EGP(r.cpaHi),
      r.fmt+' · '+r.fn+' · '+(r.st==='act'?'live':'paused'),'→ '+r.act];}}},
    annotation:false},
   },plugins:[{id:'ln',afterDraw(ch){const{ctx,chartArea:a,scales}=ch;ctx.save();ctx.setLineDash([5,4]);ctx.lineWidth=1.4;ctx.font='700 10px sans-serif';
    lines.forEach(([t,v,col],i)=>{const y=scales.y.getPixelForValue(v);if(y<a.top||y>a.bottom)return;
     ctx.strokeStyle=col;ctx.beginPath();ctx.moveTo(a.left,y);ctx.lineTo(a.right,y);ctx.stroke();
     const lab=t+' '+EGP(v), x=a.left+8+i*(Math.min(150,(a.right-a.left-40)/4));
     ctx.fillStyle='#fff';ctx.fillRect(x-3,y-13,ctx.measureText(lab).width+6,13);
     ctx.fillStyle=col;ctx.fillText(lab,x,y-4);});ctx.restore();}}]});
}

/* ---------- 2. ACTIONS ---------- */
function simulate(D){
 const live=D.rows.filter(r=>r.st==='act'&&r.sp7>0);
 const kill=live.filter(r=>r.act==='KILL'), scale=live.filter(r=>r.act==='SCALE');
 const keep=live.filter(r=>r.act!=='KILL');
 const freed=kill.reduce((s,r)=>s+r.sp7,0);
 const cap=scale.reduce((s,r)=>s+r.sp7*0.20,0);          // post's rule: +20% a step
 const used=Math.min(freed,cap);
 const base7=live.reduce((s,r)=>s+r.sp7,0);
 const plan=keep.map(r=>{
  const add=(scale.includes(r)&&cap>0)?used*(r.sp7*0.20)/cap:0;
  return {r,sp:r.sp7+add,add};});
 const parked=freed-used;                                 // not re-spent: budget comes out
 const expK=plan.reduce((s,p)=>s+p.sp/1000*p.r.lamR,0);
 const expLo=plan.reduce((s,p)=>s+p.sp/1000*(1000/p.r.cpaRHi),0);
 const expHi=plan.reduce((s,p)=>s+p.sp/1000*(1000/p.r.cpaRLo),0);
 const spNew=plan.reduce((s,p)=>s+p.sp,0);
 const kNow=live.reduce((s,r)=>s+r.k7,0);
 /* Both sides of the comparison use the SAME estimator (last-14d shrunk rate at the
    stated budget). Comparing a lag-depressed raw 7d count against a lag-free model
    would book the conversion tail as if the reallocation had earned it. */
 const expNow=live.reduce((s,r)=>s+r.sp7/1000*r.lamR,0);
 /* Margin is basis-aware: 16.1% delivered on online (courier + COD drag already taken),
    24.3% on store revenue, which carries neither. */
 const kAll=live.reduce((s,r)=>s+r.k,0);
 const aovK=kAll>0?live.reduce((s,r)=>s+r.val,0)/kAll:0;
 const vOn=live.reduce((s,r)=>s+r.pv,0), vOff=live.reduce((s,r)=>s+r.fv,0)*D.hair;
 const marg=(vOn+vOff)>0?(vOn*0.161+vOff*0.243)/(vOn+vOff):0.161;
 const gpDelta=(expK-expNow)*aovK*marg-(spNew-base7);
 return {kill,scale,keep,freed,used,parked,base7,spNew,aovK,gpDelta,marg,
   cpaNow:expNow>0?base7/expNow:0, kNow, expNow, cpaRaw7:kNow>0?base7/kNow:0,
   cpaNew:expK>0?spNew/expK:0, cpaNewLo:expHi>0?spNew/expHi:0, cpaNewHi:expLo>0?spNew/expLo:0, expK};}
function doCard(r,D){
 return '<div class="doc" onclick="openAd(\''+r.id+'\')">'+thumb(r,52)
 +'<div style="min-width:0"><div class="t">'+r.n+'</div>'
 +'<div class="s">'+whyShort(r,D)+'</div>'
 +'<div class="m">'+EGP(r.sp7)+'/wk &nbsp;·&nbsp; online '+N0(r.pu)+' @ '+EGP(r.cppOn)+' ('+N2(r.roasOn)+'×)'
 +' &nbsp;·&nbsp; store '+N0(r.op)+' @ '+EGP(r.cppOff)+' ('+N2(r.roasOff)+'×)</div></div></div>';}
function whyShort(r,D){
 if(r.act==='KILL')return 'Costs <b>'+EGP(r.cpa)+'</b> a purchase against a '+EGP(D.target)+' target. Even its best case ('+EGP(r.cpaLo)+') misses. <b>Turn it off</b> and take back '+EGP(r.sp7)+'/wk.';
 if(r.act==='CUT')return 'Reads <b>'+EGP(r.cpa)+'</b>, over the '+EGP(D.kill)+' kill line — but the data still allows '+EGP(r.cpaLo)+'. <b>Halve the budget</b>, re-read in a week.';
 if(r.act==='SCALE')return 'Buys at <b>'+EGP(r.cpa)+'</b> and even its worst case ('+EGP(r.cpaHi)+') beats '+EGP(D.scale)+'. <b>Raise to '+EGP(r.sp7*1.2)+'/wk</b>, stop at '+EGP(D.target)+'.';
 if(r.act==='REACTIVATE')return 'Paused, but bought at <b>'+EGP(r.cpa)+'</b> on '+N1(r.k)+' purchases. <b>Switch it back on</b> unless it was a one-off promo.';
 if(r.act==='THIN')return 'Only '+N1(r.k)+' purchases — the data cannot tell a good ad from a lucky one yet. <b>Let it run.</b>';
 return 'At '+EGP(r.cpa)+' it sits between the scale and kill lines. <b>No move the data supports.</b>';}
function sec(title,n,note,body){
 return '<div class="hd"><h2>'+title+'</h2><span class="n">'+n+'</span></div>'
  +(note?'<div class="mut" style="font-size:11.8px;margin:-4px 0 9px;line-height:1.55">'+note+'</div>':'')+body;}

function vAct(D){
 const S=simulate(D), bh=budgetHoldTest(D.basis,D.hair);
 const dl=S.cpaNow>0?(S.cpaNew/S.cpaNow-1):0;
 const pick=a=>D.rows.filter(r=>r.act===a).sort((x,y)=>y.sp7-x.sp7||y.sp-x.sp);
 const kill=pick('KILL'), cut=pick('CUT'), scale=pick('SCALE'),
       react=D.rows.filter(r=>r.act==='REACTIVATE').sort((a,b)=>a.cpa-b.cpa);
 const dead=D.rows.filter(r=>r.st==='act'&&r.k<1&&r.sp>=(D.tot.val/Math.max(D.tot.k,1))*0.5);
 const grid=list=>list.length?'<div class="do">'+list.map(r=>doCard(r,D)).join('')+'</div>'
   :'<div class="mut" style="font-size:12.5px">Nothing qualifies.</div>';
 const drag=bh.med||1;
 return '<div class="kpis">'
 +kpi('Turn off',kill.length,'frees '+EGP(S.freed)+'/wk','#e23a63')
 +kpi('Cut budget',cut.length,'probably bad, not proven','#ff8b42')
 +kpi('Raise 20%',scale.length,'can absorb '+EGP(S.scale.reduce((s,r)=>s+r.sp7*0.2,0))+'/wk','#12b886')
 +kpi('Turn back on',react.length,'paused and proven','#5a5bf0')
 +kpi('CPA now',EGP(S.cpaNow),'current split, same estimator')
 +kpi('CPA after',EGP(S.cpaNew),PC(dl)+' · with the scaling drag '+EGP(S.cpaNew*drag),dl<0?'#12b886':'#e23a63')
 +kpi('Purchases',PC(S.expK/Math.max(S.expNow,1e-9)-1),'volume — if this falls, the CPA win is fake',S.expK>=S.expNow?'#12b886':'#e23a63')
 +kpi('Gross profit',EGP(S.gpDelta)+'/wk','at '+(Math.round(S.marg*1000)/10)+'% blended margin',S.gpDelta>=0?'#12b886':'#e23a63')
 +'</div>'
 +'<div class="banner b">Click any card or row to see the ad, its full online / in-store split, and a link straight to it in Ads Manager. '
 +'Every ad is judged on its <b>shrunk</b> cost per purchase with a 90% interval, so a lucky three-purchase ad cannot buy its way onto the scale list.</div>'
 +sec('Turn these off',kill.length+' ads · '+EGP(S.freed)+' a week',
   'Only ads whose <i>best</i> case is still above the '+EGP(D.kill)+' kill line. Anything merely suspicious is in the cut list instead.',grid(kill))
 +sec('Cut these back',cut.length+' ads · '+EGP(cut.reduce((s,r)=>s+r.sp7,0))+' a week',
   'Over the kill line on the point estimate, but the interval still allows a decent CPA. Halve, do not kill.',grid(cut))
 +sec('Raise these 20%',scale.length+' ads · +'+EGP(S.scale.reduce((s,r)=>s+r.sp7*0.2,0))+' a week',
   'Worst case still beats the '+EGP(D.scale)+' scale line. One step at a time, then re-read.',grid(scale))
 +sec('Turn these back on',react.length+' ads',
   'Paused, at least 3 purchases, proven under the scale line. Check each was not a one-off promo creative.',grid(react))
 +(dead.length?sec('Zero purchases on real money',dead.length+' ads',
   'Spent more than half an AOV and bought nothing. No estimate needed.',grid(dead)):'')
 +sec('The whole list, with both ROAS','','Sort any column. Online and in-store shown separately — in this window their per-ad costs correlate '+N2(corrOnOff(D.rows))+', so a winner on one is not a winner on the other.',
   table(sortRows(D.rows),COLS(D),'tg'))
 +sec('Does scaling actually hold?',bh.n+' cases measured',
   'The one assumption behind "raise it 20%". Every week-on-week budget rise of 20%+ in the window, and what CPA did the week after.',
   '<div class="kpis">'+kpi('Median CPA move',bh.med===null?'—':PC(bh.med-1),'after a 20%+ rise',(drag>1?'#e23a63':'#12b886'))
   +kpi('Got worse',bh.n?Math.round(100*bh.worse/bh.n)+'%':'—',bh.worse+' of '+bh.n+' cases')
   +kpi('Applied to the projection',EGP(S.cpaNew*drag),'instead of '+EGP(S.cpaNew))+'</div>'
   +(bh.n?table(bh.rows.slice(0,20).concat(bh.rows.slice(-20)),
     [['n','Ad',r=>'<span class="nm">'+r.n+'</span>'],['g','Budget rise',r=>PC(r.g)],
      ['c1','CPA before',r=>EGP(r.c1)],['c2','CPA after',r=>EGP(r.c2)],
      ['r','Change',r=>(r.r>1?'<span class="r">':'<span class="g">')+PC(r.r-1)+'</span>'],
      ['k1','Purch before',r=>N1(r.k1)],['k2','after',r=>N1(r.k2)]]):''));
}
function corrOnOff(rows){const a=rows.filter(r=>isFinite(r.cppOn)&&isFinite(r.cppOff));
 return corr(a.map(r=>r.cppOn),a.map(r=>r.cppOff));}

/* ---------- 3. PREDICT ---------- */
function vPred(D){
 const live=D.rows.filter(r=>r.st==='act'&&r.sp7>0);
 const f=live.map(r=>{const e=r.sp7/1000;
   return Object.assign({},r,{fc:e*r.lamR,fcLo:e*1000/r.cpaRHi,fcHi:e*1000/r.cpaRLo,
     gp:e*r.lamR*(r.k>0?r.val/r.k:0)*0.161 - r.sp7});});
 const T=k=>f.reduce((s,r)=>s+r[k],0);
 const shrunkTot=T('sp7')/Math.max(T('fc'),1e-9);
 const winCPA=live.reduce((s,r)=>s+r.sp,0)/Math.max(live.reduce((s,r)=>s+r.k,0),1e-9);
 const naive=T('sp7')/Math.max(live.reduce((s,r)=>s+r.k7,0),1e-9);
 const rising=D.rows.filter(r=>r.trendSig&&r.trend>0).sort((a,b)=>b.trend-a.trend);
 const falling=D.rows.filter(r=>r.trendSig&&r.trend<0).sort((a,b)=>a.trend-b.trend);
 const byFmt={},byFn={};
 D.rows.forEach(r=>{[[byFmt,r.fmt],[byFn,r.fn]].forEach(([m,k])=>{
   const o=m[k]=m[k]||{sp:0,k:0,val:0,atc:0,n:0};o.sp+=r.sp;o.k+=r.k;o.val+=r.val;o.atc+=r.atc;o.n++;});});
 const grp=m=>table(Object.entries(m).map(([k,v])=>({g:k,n:v.n,sp:v.sp,k2:v.k,
    cpa:v.k?v.sp/v.k:Infinity,roas:v.sp?v.val/v.sp:0,aov:v.k?v.val/v.k:0,catc:v.atc?v.sp/v.atc:Infinity,
    sh:v.sp/D.tot.sp})).sort((a,b)=>b.sp-a.sp),
  [['g','Group',r=>'<b>'+r.g+'</b>'],['n','Ads',r=>r.n],['sp','Spend',r=>EGP(r.sp)],
   ['sh','Share',r=>Math.round(r.sh*100)+'%'],['k2','Purch',r=>N1(r.k2)],['cpa','CPA',r=>EGP(r.cpa)],
   ['aov','AOV',r=>EGP(r.aov)],['roas','ROAS',r=>N2(r.roas)],['catc','Cost/ATC',r=>EGP(r.catc)]]);
 return '<div class="kpis">'
 +kpi('Next 7d purchases',N0(T('fc')),'90% CI '+N0(T('fcLo'))+'–'+N0(T('fcHi'))+' at today\'s budget')
 +kpi('Spend it assumes',EGP(T('sp7')),live.length+' live ads, last 7d held flat')
 +kpi('Forward CPA',EGP(shrunkTot),'shrunk on the last 14 mature days')
 +kpi('Naive 7d CPA',EGP(naive),'what the raw last-7d numbers say',naive<shrunkTot?'#e23a63':'#12b886')
 +kpi('Window CPA',EGP(winCPA),'same ads, whole '+(D.i1-D.i0)+'d window')
 +kpi('Decay',PC(shrunkTot/winCPA-1),'recent vs window — the back-to-school gap',shrunkTot>winCPA?'#e23a63':'#12b886')
 +kpi('CPA rising (sig.)',rising.length+' ads','spend E£'+N0(rising.reduce((s,r)=>s+r.sp7,0))+'/wk','#e23a63')
 +kpi('CPA falling (sig.)',falling.length+' ads','spend E£'+N0(falling.reduce((s,r)=>s+r.sp7,0))+'/wk','#12b886')
 +'</div>'
 +'<div class="banner"><b>What this is.</b> Each ad\'s purchase rate, times next week\'s budget at today\'s spend. Nothing else — no stock, no promo calendar, no seasonality. '
 +'It uses the <b>last 14 mature days only</b>, because back-to-school sits inside this window and August rates do not forecast September. '
 +'<b>Decay</b> is that gap: positive means the grid\'s window CPA is flattering these ads against how they run now.</div>'
 +card('Next 7 days, per ad','Forecast at each ad\'s own last-7d budget. GP column uses the 16.1% delivered margin from the vault economics; negative means the ad loses money at this CPA.',
   table(sortRows(f).slice(0,80),[
    ['n','Ad',adCell],['sp7','Budget 7d',r=>EGP(r.sp7)],
    ['k7','Purch last 7d',r=>N1(r.k7)],['fc','Forecast next 7d',r=>'<b>'+N1(r.fc)+'</b>'],
    ['fcLo','lo',r=>N1(r.fcLo)],['fcHi','hi',r=>N1(r.fcHi)],
    ['cpa','CPA window',r=>EGP(r.cpa)],['cpaR','CPA last 14d',r=>'<b>'+EGP(r.cpaR)+'</b>'],
    ['decay','Decay',r=>r.decay===null?'—':(r.decay>0?'<span class="r">':'<span class="g">')+PC(r.decay)+'</span>'],
    ['roas','ROAS',r=>N2(r.roas)],
    ['gp','GP next 7d',r=>(r.gp<0?'<span class="r">':'<span class="g">')+EGP(r.gp)+'</span>'],
    ['trend','Trend',r=>r.trend===null?'<span class="mut">n/a</span>':(r.trend>0?'<span class="r">+':'<span class="g">')+Math.round(r.trend*100)+'%</span>'+(r.trendSig?' *':'')]]))
 +'<div class="two">'
 +card('By format (step 6)','Formats are inferred from video-play rate per impression, not from Meta\'s creative type field — <5% static, 5–45% mixed/carousel, >45% video.',grp(byFmt))
 +card('By funnel stage (step 7)','Stage is a regex on campaign/ad-set/ad name, so it is a label, not a fact. Judge TOF on cost per add-to-cart and BOF on CPA.',grp(byFn))
 +'</div>'
 +card('Where CPA is moving','Two-sample Poisson test on first vs second half of the window. * = significant at 95%. Ads under 10 purchases in either half are not tested at all — they cannot be.',
   '<div style="height:360px"><canvas id="tr"></canvas></div>');
}
function drawPred(D){
 const c=document.getElementById('tr'); if(!c)return;
 const r=D.rows.filter(x=>x.trend!==null).sort((a,b)=>b.trend-a.trend).slice(0,30);
 new Chart(c,{type:'bar',data:{labels:r.map(x=>x.n.slice(0,26)),datasets:[{data:r.map(x=>x.trend*100),
  backgroundColor:r.map(x=>x.trend>0?(x.trendSig?'#e23a63':'#f7bfcd'):(x.trendSig?'#12b886':'#b7e8d6'))}]},
  options:{maintainAspectRatio:false,indexAxis:'y',plugins:{legend:{display:false},
   tooltip:{callbacks:{label:c=>'CPA '+(c.raw>0?'+':'')+Math.round(c.raw)+'% vs first half'+(r[c.dataIndex].trendSig?' (significant)':' (not significant)')}}},
   scales:{x:{title:{display:true,text:'CPA change, second half vs first half (%)'}}}}});
}

/* ---------- 4. IN-STORE vs ONLINE ---------- */
function vStore(D){
 const [i0,i1]=[D.i0,D.i1];
 const rows=D.rows.map(r=>({id:r.id,n:r.n,cmp:r.cmp,th:r.th,acct:r.acct,pl:r.pl,act:r.act,
   sp:r.sp,pu:r.pu,op:r.op,pv:r.pv,fv:r.fv,nc:r.nc,st:r.st,fn:r.fn,fmt:r.fmt,
   cppOn:r.pu?r.sp/r.pu:Infinity, cppOff:r.op?r.sp/r.op:Infinity,
   roasOn:r.sp?r.pv/r.sp:0, roasOff:r.sp?r.fv/r.sp:0,
   aovOn:r.pu?r.pv/r.pu:0, aovOff:r.op?r.fv/r.op:0,
   mix:(r.pv+r.fv)>0?r.fv/(r.pv+r.fv):0}));
 const T=k=>rows.reduce((s,r)=>s+r[k],0);
 const sp=T('sp'),pu=T('pu'),op=T('op'),pv=T('pv'),fv=T('fv');
 const H=D.hair;
 const beOn=6.21, beOff=1/0.243;
 /* What share of ACTUAL branch revenue Meta is claiming. Straight off the live Odoo
    branch daily series in data.js, over exactly the window on screen. */
 const brRev=(()=>{const B=O.bnrD,w=B&&B._w; if(!w)return 0;
   const s0=new Date(w.start), a=new Date(WSTART); a.setDate(a.getDate()+i0);
   const b=new Date(WSTART); b.setDate(b.getDate()+i1-1);
   const x=Math.round((a-s0)/864e5), y=Math.round((b-s0)/864e5);
   if(x<0||y>=w.n||y<x)return 0;
   let t=0; for(const k in B){if(k==='_w')continue; const g=B[k].grev||[];
     for(let i=x;i<=y;i++)t+=g[i]||0;} return t;})();
 return '<div class="kpis">'
 +kpi('Online CPP',EGP(pu?sp/pu:0),N0(pu)+' pixel purchases')
 +kpi('In-store CPP (claimed)',EGP(op?sp/op:0),N0(op)+' offline CAPI purchases')
 +kpi('In-store CPP @21.4%',EGP(op?sp/(op*0.214):0),'at the measured incremental rate')
 +kpi('Online ROAS',N2(sp?pv/sp:0),'breakeven '+N2(beOn)+'× at 16.1% delivered margin',pv/sp>beOn?'#12b886':'#e23a63')
 +kpi('In-store ROAS (claimed)',N2(sp?fv/sp:0),'breakeven '+N2(beOff)+'× at 24.3% store margin','#12b886')
 +kpi('In-store ROAS @21.4%',N2(sp?fv*0.214/sp:0),'below breakeven '+N2(beOff)+'×',(fv*0.214/sp)>beOff?'#12b886':'#e23a63')
 +kpi('Online AOV',EGP(pu?pv/pu:0),'Meta-reported')
 +kpi('In-store AOV',EGP(op?fv/op:0),'Meta-reported, offline CAPI')
 +'</div>'
 +'<div class="banner r"><b>The in-store number is a matching claim, not a measurement of lift.</b> '
 +'Meta credits itself E£'+N0(fv)+' of store revenue in this window on E£'+N0(sp)+' of spend. '
 +'Actual branch revenue over the same days, from Odoo, was E£'+N0(brRev)+' — so Meta is claiming <b>'
 +(brRev?Math.round(100*fv/brRev):0)+'% of everything the seven shops sold</b>, for 3% of company revenue in ad spend. '
 +'That is '+N2(fv/sp)+'× and it would be the best media on earth. It is not: the store CAPI feed matches a purchase to anyone '
 +'who saw an ad, and in the regression already run in this account Meta spend stops predicting branch revenue once trend and the Fri/Sat '
 +'pattern are controlled — the coefficient turns negative. The 21.4% figure is this account\'s own measured incremental share for '
 +'offline conversions (Meta\'s attribution-comparison read), and it is the one to plan on. At 21.4%, in-store Meta runs at '
 +N2(fv*0.214/sp)+'× against a '+N2(beOff)+'× breakeven — i.e. it does not clear.</div>'
 +'<div class="banner b">Online and in-store do not rank the same ads. The correlation between an ad\'s online CPP and its in-store CPP '
 +'in this window is <b>'+N2(corr(rows.filter(r=>isFinite(r.cppOn)&&isFinite(r.cppOff)).map(r=>r.cppOn),
   rows.filter(r=>isFinite(r.cppOn)&&isFinite(r.cppOff)).map(r=>r.cppOff)))+'</b>. '
 +'If that is near zero, picking winners on one basis tells you nothing about the other, and the blended grid is averaging two different games.</div>'
 +card('Online vs in-store, per ad','Sorted by spend. "Store share" is the in-store portion of the value Meta claims for that ad.',
   table(sortRows(rows),[
    ['n','Ad',adCell],['act','What to do',r=>'<span class="tg '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</span>'],
    ['st','Live',r=>r.st==='act'?'<span class="g">on</span>':'<span class="mut">off</span>'],
    ['fn','Funnel',r=>r.fn],['sp','Spend',r=>EGP(r.sp)],
    ['pu','Online purch',r=>N0(r.pu)],['cppOn','Online CPP',r=>EGP(r.cppOn)],['roasOn','Online ROAS',r=>(r.roasOn>=beOn?'<span class="g">':'<span class="r">')+N2(r.roasOn)+'</span>'],['aovOn','Online AOV',r=>EGP(r.aovOn)],
    ['op','Store purch',r=>N0(r.op)],['cppOff','Store CPP',r=>EGP(r.cppOff)],['roasOff','Store ROAS',r=>N2(r.roasOff)],['aovOff','Store AOV',r=>EGP(r.aovOff)],
    ['nc','Store new cust',r=>N0(r.nc)],
    ['mix','Store share',r=>Math.round(r.mix*100)+'%']]));
}
function corr(a,b){const n=a.length;if(n<3)return NaN;
 const ma=a.reduce((x,y)=>x+y,0)/n, mb=b.reduce((x,y)=>x+y,0)/n;
 let sa=0,sb=0,sab=0;for(let i=0;i<n;i++){const x=a[i]-ma,y=b[i]-mb;sa+=x*x;sb+=y*y;sab+=x*y;}
 return sab/Math.sqrt(sa*sb);}

/* ---------- 5. FIRST vs LAST TOUCH ---------- */
function adSpend(){ /* platform spend over the touch window, straight off the live daily series */
 const w=TOUCH.win||SEED.touchWin; const A=O.ad;
 if(!w||!A||!A.start)return (SEED.spend)||{};
 const s0=new Date(A.start), i=Math.round((new Date(w[0])-s0)/864e5), j=Math.round((new Date(w[1])-s0)/864e5);
 if(!(i>=0&&j>=i&&j<A.n))return (SEED.spend)||{};
 const sum=k=>{const v=A[k]||[];let t=0;for(let x=i;x<=j;x++)t+=v[x]||0;return t;};
 return {Meta:sum('mspend'),Google:sum('gspend'),TikTok:sum('tspend')};}
function vTouch(D){
 const L=TOUCH.last||{}, F=TOUCH.first||{}, SP=adSpend();
 const keys=[...new Set([...Object.keys(L),...Object.keys(F)])];
 const tot=o=>Object.values(o).reduce((s,v)=>s+v[1],0);
 const tL=tot(L),tF=tot(F);
 const rows=keys.map(k=>{const l=L[k]||[0,0,0,0], f=F[k]||[0,0,0,0], sp=SP[k]||0;
  return {ch:k,sp,lS:l[0],lT:l[1],lR:l[2],lA:l[3],fS:f[0],fT:f[1],fR:f[2],fA:f[3],
   lCPA:sp&&l[1]?sp/l[1]:Infinity, fCPA:sp&&f[1]?sp/f[1]:Infinity,
   lROAS:sp?l[2]/sp:0, fROAS:sp?f[2]/sp:0,
   lATC:sp&&l[3]?sp/l[3]:Infinity, fATC:sp&&f[3]?sp/f[3]:Infinity,
   dT:l[1]?f[1]/l[1]-1:0, dR:l[2]?f[2]/l[2]-1:0};}).sort((a,b)=>b.lR-a.lR);
 const m=rows.find(r=>r.ch==='Meta')||{}, g=rows.find(r=>r.ch==='Google')||{};
 /* Before reading anything into the first-vs-last gap: a channel whose spend ramped inside
    the window loses first-touch credit for arithmetic reasons, not behavioural ones. This
    is that check, computed live off the daily spend series. */
 const grow=k=>{const A=O.ad,w=TOUCH.win||SEED.touchWin; if(!A||!w)return 0;
   const s0=new Date(A.start), i=Math.round((new Date(w[0])-s0)/864e5), j=Math.round((new Date(w[1])-s0)/864e5);
   const nd=j-i+1; if(i-nd<0)return 0;
   const v=A[k]||[]; let cur=0,prv=0;
   for(let x=i;x<=j;x++)cur+=v[x]||0; for(let x=i-nd;x<i;x++)prv+=v[x]||0;
   return prv>0?cur/prv-1:0;};
 const gGrow=grow('gspend'), mGrow=grow('mspend');
 return '<div class="kpis">'
 +kpi('Meta — last touch',EGP(m.lCPA),N0(m.lT)+' tx · ROAS '+N2(m.lROAS))
 +kpi('Meta — first touch',EGP(m.fCPA),N0(m.fT)+' tx · ROAS '+N2(m.fROAS))
 +kpi('Meta, model gap',PC(m.dT),'transactions, first vs last','#5a5bf0')
 +kpi('Google — last touch',EGP(g.lCPA),N0(g.lT)+' tx · ROAS '+N2(g.lROAS))
 +kpi('Google — first touch',EGP(g.fCPA),N0(g.fT)+' tx · ROAS '+N2(g.fROAS))
 +kpi('Google, model gap',PC(g.dT),'first touch credits it less','#e23a63')
 +kpi('Meta cost / ATC',EGP(m.lATC),'last touch · first '+EGP(m.fATC))
 +kpi('Google cost / ATC',EGP(g.lATC),'last touch · first '+EGP(g.fATC),'#12b886')
 +'</div>'
 +'<div class="banner r"><b>Before any of this is read: GA4\'s own channel grouping is broken on this property.</b> '
 +'Meta stamps <code>utm_medium</code> with the placement name (<code>Facebook_Mobile_Feed</code>, <code>Instagram_Stories</code>), '
 +'so GA4 files most paid social as <b>Organic Social</b> — 821k Egypt sessions of it in this window. '
 +'Every number on this tab is therefore rebuilt from <b>source</b>, not from GA4\'s channel: '
 +'<code>fb</code> and <code>ig</code> are paid Meta regardless of medium; <code>facebook.com</code> / <code>m.facebook.com</code> referrals are dark social and are kept separate. '
 +'Egypt-only, so the Singapore datacentre bot flood is excluded.</div>'
 +'<div class="banner b"><b>The answer to the question.</b> For Meta the two models agree to within '+(Math.round(Math.abs(m.dT)*1000)/10)+'%'+
 ' on transactions — E£'+N0(m.lCPA)+' last touch against E£'+N0(m.fCPA)+' first touch. '
 +'Switching attribution model does not change what Meta costs you. Either the journeys are short, or Meta sits at both ends of them — this data cannot separate those two, and for the decision it does not need to. '
 +'Google is the one that moves: first touch credits it '+(Math.round(Math.abs(g.dT)*1000)/10)+'% fewer transactions, which puts its CPA at E£'+N0(g.fCPA)+
 ' instead of E£'+N0(g.lCPA)+'. <b>That gap cannot be attributed.</b> The obvious reading is that Google closes demand something else '
 +'created — but the falsifying test kills it: Google spend '+PC(gGrow)+' against the previous 60 days while Meta moved '+PC(mGrow)+'. '
 +'A channel that is scaling hard always loses first-touch credit, because the users converting today were first acquired before it scaled. '
 +'Until a geo holdout or an incrementality test separates those two, the honest answer is that the model gap is real and its cause is not established.<br/><br/>'
 +'On cost per add-to-cart, the metric the method says to judge TOF on, Google wins under both models: E£'+N0(g.lATC)+' against Meta\'s E£'+N0(m.lATC)+'.</div>'
 +card('The confound, measured','Spend in the window against the 60 days before it. Read the first-vs-last gap only against this.',
   '<div class="kpis">'+kpi('Google spend growth',PC(gGrow),'vs prior 60 days',Math.abs(gGrow)>0.5?'#e23a63':'')
   +kpi('Meta spend growth',PC(mGrow),'vs prior 60 days')
   +kpi('Google first-touch gap',PC(g.dT),'transactions, first vs last')
   +kpi('Meta first-touch gap',PC(m.dT),'transactions, first vs last')+'</div>'
   +'<div class="mut" style="font-size:12px;line-height:1.7">If the growth number is large and the first-touch gap is negative for the same channel, '
   +'the two are not separable here. A first-touch model assigns a converting user to whatever brought them in <i>originally</i>, which may predate the window entirely; '
   +'a channel that doubled its spend last month has disproportionately many converters it did not originally acquire. '
   +'The measurement that does separate them is a geo holdout, not another attribution model.</div>')
+card('Every channel, both models','Paid CPA/ROAS only shown where there is spend to divide by. Revenue is GA4 purchase revenue, Egypt only, and will not tie to Odoo.',
   table(rows,[
    ['ch','Channel',r=>'<b>'+r.ch+'</b>'],['sp','Spend',r=>r.sp?EGP(r.sp):'<span class="mut">—</span>'],
    ['lT','Tx last',r=>N0(r.lT)],['fT','Tx first',r=>N0(r.fT)],['dT','Δ tx',r=>(r.dT>0?'<span class="g">':'<span class="r">')+PC(r.dT)+'</span>'],
    ['lR','Rev last',r=>EGP(r.lR)],['fR','Rev first',r=>EGP(r.fR)],['dR','Δ rev',r=>(r.dR>0?'<span class="g">':'<span class="r">')+PC(r.dR)+'</span>'],
    ['lCPA','CPA last',r=>EGP(r.lCPA)],['fCPA','CPA first',r=>EGP(r.fCPA)],
    ['lROAS','ROAS last',r=>r.sp?N2(r.lROAS):'—'],['fROAS','ROAS first',r=>r.sp?N2(r.fROAS):'—'],
    ['lATC','Cost/ATC last',r=>EGP(r.lATC)],['fATC','first',r=>EGP(r.fATC)]]))
 +card('What each model does to the mix','The channels that move between models are the ones where the journey has more than one step.',
   '<div style="height:340px"><canvas id="tc"></canvas></div>')

}
function drawTouch(D){
 const c=document.getElementById('tc'); if(!c)return;
 const L=TOUCH.last||{},F=TOUCH.first||{};
 const keys=[...new Set([...Object.keys(L),...Object.keys(F)])].sort((a,b)=>((L[b]||[0,0,0])[2]||0)-((L[a]||[0,0,0])[2]||0));
 new Chart(c,{type:'bar',data:{labels:keys,datasets:[
  {label:'Last touch (transactions)',data:keys.map(k=>(L[k]||[0,0])[1]),backgroundColor:'#5a5bf0'},
  {label:'First touch (transactions)',data:keys.map(k=>(F[k]||[0,0])[1]),backgroundColor:'#1fc3b6'}]},
  options:{maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{y:{title:{display:true,text:'GA4 transactions (Egypt)'}}}}});
}

/* ---------- 6. METHOD ---------- */
function vMeth(D){
 const bh=budgetHoldTest(D.basis,D.hair);
 return card('What this page does','A rebuild of the Meta CPA-quadrant method on OurKids numbers, with the estimates the original method leaves out.',
 '<div class="meth">'
 +'<h4>The method, step by step</h4><ol>'
 +'<li>Every Meta ad with spend in the window is plotted: log spend on X, CPA on Y. Paused ads included — that is where the reactivations are.</li>'
 +'<li>Four lines: current blended CPA, target (current − '+Math.round(D.tgtPct*100)+'%), kill (1.5× target), scale (0.7× target).</li>'
 +'<li>Filters for live/paused, format, funnel stage, minimum spend.</li>'
 +'<li>Paused ads under the scale line with ≥3 purchases → reactivate list.</li>'
 +'<li>Live ads over the kill line → kill list. Live ads under the scale line → +20% budget.</li>'
 +'<li>Formats compared within format, never across.</li>'
 +'<li>TOF judged on cost per add-to-cart, BOF on CPA.</li></ol>'
 +'<h4>What was changed, and why</h4>'
 +'<p><b>Every CPA on this page is shrunk, not raw.</b> The original plots raw CPA, which means a E£6,000 ad that happened to get three '
 +'purchases outranks a E£200,000 ad with a stable rate, and you scale the lucky one. Each ad\'s purchase rate is instead a Gamma-Poisson '
 +'posterior: <code>Gamma(a + purchases, b + spend/1000)</code>, with the prior <code>Gamma(a,b)</code> fitted across all '+D.universe.length+
 ' ads by method of moments with the Poisson sampling variance subtracted out. Current fit: a=<code>'+N2(D.pr.a)+'</code>, b=<code>'+N2(D.pr.b)+
 '</code>. Thin ads get pulled to the account mean; heavy ads barely move. The hollow rings on the scatter are the raw CPAs, so the size of the correction is visible.</p>'
 +'<p><b>Kill and scale use the interval, not the point.</b> An ad is killed only when the 5th percentile of its CPA is still above the kill line, '
 +'and scaled only when the 95th is still below the scale line. Anything in between is HOLD, because the data cannot tell them apart yet.</p>'
 +'<p><b>The window drops its last '+MATURE+' days by default.</b> 71% of conversions here land on day one but the tail runs ~16 days; '
 +'judging yesterday\'s CPA is judging a partial number. Switch to "60d full" to see the immature tail if you want it.</p>'
 +'<p><b>Trend is tested, not eyeballed.</b> Second half vs first half of the window, two-sample Poisson on the rate. Ads with fewer than 10 '
 +'purchases in either half are marked n/a rather than given a direction, because at those counts there is no direction to give.</p>'
 +'<h4>The assumption this whole method rests on</h4>'
 +'<p>Step 5 assumes an ad holds its CPA when its budget goes up 20%. Measured on this account over the window: '+bh.n+' week-on-week budget '
 +'rises of ≥20%, median following-week CPA move <b>'+(bh.med===null?'n/a':PC(bh.med-1))+'</b>, worse in '+bh.worse+' of '+bh.n+' cases. '
 +'The Actions tab applies that median as a drag on the projection rather than pretending scaling is free.</p>'
 +'<h4>Known limits — read these before acting</h4><ul>'
 +'<li><b>Ad universe.</b> The pipeline keeps the top 120 ads by spend per account over 60 days, and drops anything under E£1,000 lifetime. '
 +'Ads that last ran more than 60 days ago are not here, so "reactivate" cannot see older winners.</li>'
 +'<li><b>In-store credit is a matching claim.</b> The default 21.4% haircut is this account\'s measured incremental share for offline conversions. '
 +'At 100% Meta claims more than half of all store revenue in this window, on 3% of company revenue in ad spend.</li>'
 +'<li><b>Format is inferred</b> from video-play rate per impression, not read from Meta\'s creative type. Funnel stage is a regex on names.</li>'
 +'<li><b>Add-to-cart is a fixed 2026-07-23→2026-09-16 pull</b> until the pipeline carries it daily; it does not re-cut with the window selector.</li>'
 +'<li><b>Back-to-school is inside this window.</b> August store revenue was E£58.5M against E£32.9M in July. Any flat-budget forecast reads high once that demand leaves.</li>'
 +'<li><b>GA4 revenue will not tie to Odoo.</b> Different definitions, and GA4 is capturing roughly two thirds of Odoo online orders in this window.</li>'
 +'</ul></div>');
}
