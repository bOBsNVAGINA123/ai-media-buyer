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
const G4=((O&&O.ga4ads&&O.ga4ads.ads)||(SEED.ga4ads&&SEED.ga4ads.ads)||{});
const G4LIVE=!!(O&&O.ga4ads&&O.ga4ads.ads);
function ga4Of(name){return G4[(name||'').trim().toLowerCase().slice(0,80)]||null;}
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
/* Roll the per-ad daily series up to ad set or campaign. Everything downstream -- the
   shrinkage, the intervals, the verdicts, the scatter -- is level-agnostic, so this is the
   only place that has to know. A group is "live" if any member is still delivering. */
const DK=["sp","pv","fv","pu","op","oc","im","rch","nc","ncv","vv","atc","vp"];
function units(level){
 if(level==='ad')return MADS;
 const key=a=>level==='set'?(a.asid||a.as):(a.cid||a.cmp);
 const G={};
 MADS.forEach(a=>{
  const k=key(a); if(!k)return;
  let g=G[k];
  if(!g){g=G[k]={id:String(k),n:(level==='set'?a.as:a.cmp)||'(unnamed)',
    cmp:level==='set'?a.cmp:'',as:'',acct:a.acct,pf:'meta',lvl:level,kids:0,
    d:{},imp:0,atc:0,vp:0,_sp:-1,st:''};
   DK.forEach(k2=>g.d[k2]=new Array(WN).fill(0));}
  g.kids++;
  DK.forEach(k2=>{const v=(a.d&&a.d[k2])||[];for(let i=0;i<WN;i++)g.d[k2][i]+=v[i]||0;});
  g.imp+=a.imp||0; g.atc+=a.atc||0; g.vp+=a.vp||0;
  if(status(a)==='act')g.st='ACTIVE'; else if(!g.st)g.st=a.st||'PAUSED';
  const sp=a.sp||0; if(sp>g._sp){g._sp=sp; g.th=a.th||a.im2; g.pl=null; g.topAd=a.n;}
 });
 return Object.values(G);}
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
 const level=(document.getElementById('lvl')||{}).value||'ad';
 const UNITS=units(level);
 let rows=UNITS.map(a=>{
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
   atc:atcOf(a,i0,i1,true),st:status(a),fmt:fmt(a),fn:funnel(a),lvl:level,kids:a.kids||1,topAd:a.topAd,
   g4:level==='ad'?ga4Of(a.n):null,
   sp7:S(a,'sp',Math.max(i0,i1-7),i1),k7:(basis==='on'?S(a,'pu',Math.max(i0,i1-7),i1)
     :basis==='off'?S(a,'op',Math.max(i0,i1-7),i1)*hair
     :S(a,'pu',Math.max(i0,i1-7),i1)+S(a,'op',Math.max(i0,i1-7),i1)*hair),
   a:a};}).filter(Boolean);
 const universe=rows.slice();                       // prior is fit on everything, always
 const pr=fitPrior(universe,r=>r.k);
 const prA=fitPrior(universe.filter(r=>r.atc>0),r=>r.atc);
 universe.forEach(r=>{Object.assign(r,post(pr,r.k,r.sp));
   r.rawCpa=r.k>0?r.sp/r.k:Infinity; r.roas=r.sp>0?r.val/r.sp:0;
   /* Money, not cost per purchase. CPA punishes an ad for selling fewer, bigger baskets --
      and this account's in-store AOV runs from E£866 to E£4,964 across ads, so CPA and
      profit rank them differently. Contribution is what actually pays the rent. */
   r.gp=r.pv*0.161 + r.fv*hair*0.243 - r.sp;
   /* The same ad at the three defensible in-store credits. If the SIGN moves between them,
      the verdict is an artifact of a constant nobody has verified, not a finding. */
   /* Second opinion. GA4 counts the same ad from the site's own side, and the two disagree
      by a median 1.51x with a per-ad range of 0.14x to 6.38x -- so it is read per ad, never
      applied as a blanket factor. An ad Meta claims purchases for that GA4 never saw is the
      one case where "scale it" should never be printed. */
   r.gTx=r.g4?r.g4[1]:null; r.gSess=r.g4?r.g4[0]:null; r.gRev=r.g4?r.g4[2]:null;
   r.gRatio=(r.gTx!==null&&r.gTx>0)?r.pu/r.gTx:null;
   r.ga4=(r.g4===null)?'nodata':((r.gTx===0&&r.pu>=20)?'contradicts'
        :(r.gTx>=10&&r.gRatio!==null&&r.gRatio<=2.5)?'confirms'
        :(r.gRatio!==null&&r.gRatio>3)?'overclaims':'thin');
   r.gp0=r.pv*0.161 - r.sp;                       // store credit 0 -- online only
   r.gp1=r.pv*0.161 + r.fv*0.243 - r.sp;          // store credit 100% -- Meta's own claim
   r.rob=(r.gp<0&&r.gp0<0&&r.gp1<0)?'lose':((r.gp>0&&r.gp0>0&&r.gp1>0)?'make':'depends');
   r.gpPerK=r.sp>0?1000*r.gp/r.sp:0;
   r.aovK=r.k>0?r.val/r.k:0;
   r.margK=(r.pv+r.fv*hair)>0?(r.pv*0.161+r.fv*hair*0.243)/(r.pv+r.fv*hair):0.161;
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
   r.decay=(isFinite(r.cpa)&&isFinite(r.cpaR)&&r.cpa>0)?r.cpaR/r.cpa-1:null;
   /* forward contribution per week at today's budget, and its 90% interval */
   const e7=r.sp7/1000, unit=r.aovK*r.margK;
   r.gpw   = e7*r.lamR*unit - r.sp7;
   r.gpwLo = (isFinite(r.cpaRHi)?e7*1000/r.cpaRHi:0)*unit - r.sp7;
   r.gpwHi = (isFinite(r.cpaRLo)?e7*1000/r.cpaRLo:0)*unit - r.sp7;
   r.gpwPer= r.sp7>0?r.gpw/r.sp7:0;
   const eW=r.sp/1000, unitW=r.aovK*r.margK;
   r.gpHi=(isFinite(r.cpaLo)?eW*1000/r.cpaLo:0)*unitW - r.sp;   // best case over the window
   r.gpLo=(isFinite(r.cpaHi)?eW*1000/r.cpaHi:0)*unitW - r.sp;});
 let f=universe.filter(r=>r.sp>=mins);
 if(fSt!=='all')f=f.filter(r=>r.st===fSt);
 if(fFmt!=='all')f=f.filter(r=>r.fmt===fFmt);
 if(fFn!=='all')f=f.filter(r=>r.fn===fFn);
 const T=k=>f.reduce((s,r)=>s+r[k],0);
 const tot={sp:T('sp'),k:T('k'),val:T('val'),pu:T('pu'),op:T('op'),pv:T('pv'),fv:T('fv'),
            oc:T('oc'),im:T('im'),atc:T('atc'),n:f.length};
 const cur=tot.k>0?tot.sp/tot.k:0, target=cur*(1-tgtPct), kill=target*1.5, scale=target*0.7;
 /* An ad is only killed for LOSING MONEY, never for a high CPA alone. The version of
    this tool that ranked on CPA put four profitable ads on the kill list, because their
    in-store AOV was double the account's -- fewer purchases per pound, worth more each.
    CPA still decides scaling headroom; it no longer decides life and death. */
 const judge=(document.getElementById('judge')||{}).value||'gp';
 // a paused ad is only worth switching on if it beat the account's own return on spend
 const accGpPerK=tot.sp>0?1000*f.reduce((a,r)=>a+r.gp,0)/tot.sp:0;
 f.forEach(r=>{
  const live=r.st==='act';
  if(judge==='cpa'){                                   // the original method, for comparison
   if(live&&r.cpaLo>kill)r.act='KILL';
   else if(live&&r.cpa>kill)r.act='CUT';
   else if(live&&r.cpaHi<scale)r.act='SCALE';
   else if(!live&&r.cpaHi<scale&&r.k>=3)r.act='REACTIVATE';
   else if(r.k<3)r.act='THIN';
   else r.act='HOLD';
   return;}
  const loses=r.gp<0;
  if(r.k<3){r.act='THIN';return;}
  /* An instruction is only issued where it holds at EVERY in-store credit from 0 to 100%.
     Sixteen of this account's live ads change sign between those, so a confident list built
     on the middle number alone would be a list of guesses wearing a verdict. */
  if(live&&r.rob==='depends'){r.act='DEPENDS';return;}
  if(live&&loses&&r.gpHi<0)r.act='KILL';
  else if(live&&loses)r.act='CUT';
  else if(live&&!loses&&r.cpaHi<scale&&r.ga4!=='contradicts')r.act='SCALE';
  else if(live&&!loses&&r.cpaHi<scale)r.act='DEPENDS';   // cheap on Meta, invisible to GA4
  else if(!live&&!loses&&r.rob==='make'&&r.gpPerK>=accGpPerK)r.act='REACTIVATE';
  else r.act='HOLD';});
 return {rows:f,universe,tot,cur,target,kill,scale,basis,hair,pr,prA,prR,i0,i1,j0,tgtPct,level,judge,
         win:document.getElementById('win').value};
}

/* ---------- the falsifying test the whole "scale it 20%" step rests on ----------
   The simulation assumes an ad keeps its CPA when you give it 20% more budget.
   That is an assumption, not a measurement, so measure it: every week-on-week
   budget rise of >=20% in the last 60 days, and what CPA did the next week. */
/* v2. The first version of this compared an ad's CPA before and after a budget rise and
   booked the whole difference to the rise. It has a control now: ads whose budget barely
   moved over the same weeks. On this account the raised group came out BETTER than the flat
   control, so the "scaling costs you CPA" drag I was applying was really just the decay every
   ad has. It also reports reach, frequency and CPM, because that is what a budget rise
   actually moves. */
function budgetHoldTest(basis,hair,level){
 const out=[],flat=[],cut=[];
 units(level||'ad').forEach(a=>{
  const sp=a.d.sp||[];
  const kk=(b,e)=>{let t=0;for(let i=b;i<e;i++){const p=(a.d.pu||[])[i]||0,o=(a.d.op||[])[i]||0;
    t+=basis==='on'?p:basis==='off'?o*hair:p+o*hair;}return t;};
  for(let w=0;w+14<=WN-MATURE;w+=7){
   let s1=0,s2=0;for(let i=w;i<w+7;i++)s1+=sp[i]||0;for(let i=w+7;i<w+14;i++)s2+=sp[i]||0;
   if(s1<3000||s2<=0)continue;
   const g=s2/s1-1;
   const k1=kk(w,w+7),k2=kk(w+7,w+14);
   if(k1<5||k2<1)continue;
   const sum=(k3,b,e)=>{let t=0;for(let i2=b;i2<e;i2++)t+=((a.d[k3]||[])[i2]||0);return t;};
   const i1=sum('im',w,w+7),i2=sum('im',w+7,w+14),r1=sum('rch',w,w+7),r2=sum('rch',w+7,w+14);
   const rec={g,c1:s1/k1,c2:s2/k2,r:(s2/k2)/(s1/k1),k1,k2,n:a.n,
     dR:r1>0?r2/r1-1:0, dF:(i1>0&&r1>0&&r2>0)?(i2/r2)/(i1/r1)-1:0,
     dM:i1>0&&i2>0?(s2/i2)/(s1/i1)-1:0};
   if(g>=0.20)out.push(rec); else if(Math.abs(g)<0.10)flat.push(rec); else if(g<=-0.20)cut.push(rec);}});
 const M2=a2=>{if(!a2.length)return null;const v=a2.map(x=>x.r).sort((x,y)=>x-y);return v[Math.floor(v.length/2)];};
 const MD=(a2,f)=>{if(!a2.length)return 0;const v=a2.map(f).sort((x,y)=>x-y);return v[Math.floor(v.length/2)];};
 out.sort((x,y)=>x.r-y.r);
 return {n:out.length,med:M2(out),worse:out.filter(x=>x.r>1).length,rows:out,
   nFlat:flat.length,medFlat:M2(flat),worseFlat:flat.filter(x=>x.r>1).length,
   nCut:cut.length,medCut:M2(cut),
   reach:MD(out,x=>x.dR),freq:MD(out,x=>x.dF),cpm:MD(out,x=>x.dM),
   reachCut:MD(cut,x=>x.dR)};}

/* ---------- ad identity: thumbnail, link, modal ---------- */
const ACCT_ID={'Ourkids EGP':'336343742536460','Basic':'652528128810469'};
const NOUN={ad:'ad',set:'ad set',cmp:'campaign'};
function adLink(r){
 const a=ACCT_ID[r.acct]||ACCT_ID['Ourkids EGP'];
 const b='https://adsmanager.facebook.com/adsmanager/manage/';
 if(r.lvl==='set')return b+'campaigns/adsets?act='+a+'&selected_adset_ids='+r.id;
 if(r.lvl==='cmp')return b+'campaigns?act='+a+'&selected_campaign_ids='+r.id;
 return r.pl||(b+'ads?act='+a+'&selected_ad_ids='+r.id);}
function thumb(r,sz){sz=sz||40;
 const st='width:'+sz+'px;height:'+sz+'px;border-radius:8px;object-fit:cover;flex:none;background:#eef0f5';
 return r.th?'<img src="'+r.th+'" style="'+st+'" loading="lazy" alt=""/>'
            :'<div style="'+st+';display:flex;align-items:center;justify-content:center;color:#b6bdcc;font-size:15px">▦</div>';}
function adCell(r){
 return '<a href="#" onclick="openAd(\''+r.id+'\');return false" style="display:flex;gap:9px;align-items:center;text-decoration:none;color:inherit">'
  +thumb(r)+'<span style="min-width:0"><span class="nm" style="font-weight:700">'+r.n+'</span><br/>'
  +'<span class="mut" style="font-size:10.5px">'+(r.lvl==='ad'?(r.cmp||''):r.kids+' ads · top: '+(r.topAd||''))+'</span></span></a>';}
let LASTD=null;
function openAd(id){
 const r=(LASTD&&LASTD.universe||[]).find(x=>x.id===id); if(!r)return;
 const row=(k,v)=>'<tr><td style="text-align:left;color:#7c869c">'+k+'</td><td style="font-weight:700">'+v+'</td></tr>';
 document.getElementById('modal').innerHTML=
 '<div class="mbg" onclick="closeAd()"></div><div class="mbx">'
 +'<div style="display:flex;gap:14px;align-items:flex-start">'+thumb(r,110)
 +'<div style="flex:1;min-width:0"><div style="font-size:16px;font-weight:800;line-height:1.3">'+r.n+'</div>'
 +'<div class="mut" style="font-size:11.5px;margin-top:3px">'+(r.lvl==='ad'?r.cmp+' \u203a '+r.as:r.kids+' ads inside \u00b7 biggest spender: '+(r.topAd||'?'))+'</div>'
 +'<div style="margin-top:8px"><span class="tg '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</span> '
 +'<span class="tg '+(r.st==='act'?'scale':'hold')+'">'+(r.st==='act'?'LIVE':'PAUSED')+'</span> '
 +'<span class="tg hold">'+r.fmt+'</span> <span class="tg hold">'+r.fn+'</span></div>'
 +'<a class="btn" style="margin-top:10px;display:inline-block;text-decoration:none" target="_blank" href="'+adLink(r)+'">'
 +(r.lvl==='ad'&&r.pl?'See the ad':'Open in Ads Manager')+'</a></div></div>'
 +'<div class="two" style="margin-top:14px;gap:10px"><table>'
 +row('<b>Profit in window</b>','<span style="color:'+(r.gp>=0?'#0d8a62':'#b81f45')+'">'+(r.gp>=0?'+':'')+EGP(r.gp)+'</span>')
 +row('<b>Profit per week now</b>','<span style="color:'+(r.gpw>=0?'#0d8a62':'#b81f45')+'">'+(r.gpw>=0?'+':'')+EGP(r.gpw)+'</span>  ('+EGP(r.gpwLo)+' to '+EGP(r.gpwHi)+')')
 +row('Basket size',EGP(r.aovK)+' at '+(Math.round(r.margK*1000)/10)+'% margin')
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
 +row('GA4 transactions',r.gTx===null||r.gTx===undefined?'no GA4 row for this ad name'
   :N0(r.gTx)+' vs Meta\u2019s '+N0(r.pu)+(r.gRatio===null?'':'  \u2014 Meta claims '+N2(r.gRatio)+'\u00d7'))
 +row('GA4 revenue',r.gRev===null||r.gRev===undefined?'\u2014':EGP(r.gRev)+' vs Meta\u2019s '+EGP(r.pv))
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
const VERB={KILL:'TURN OFF',CUT:'CUT BUDGET',SCALE:'RAISE 20%',REACTIVATE:'TURN BACK ON',
 HOLD:'LEAVE ALONE',THIN:'TOO NEW',DEPENDS:'CANNOT SAY YET'};
function noun(D){return NOUN[D&&D.level||'ad'];}
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
const EGP=x=>!isFinite(x)?'\u2014':(x<0?'-':'')+'E\u00a3'+Math.abs(Math.round(x)).toLocaleString();
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
 const KEEP=['n','act','st','sp','gp','ga4','cppOn','roasOn','cppOff','roasOff','cpa'];
 const all=[
 ['n','Ad',adCell],
 ['act','What to do',r=>'<span class="tg '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</span>'],
 ['st','',r=>r.st==='act'?'<span class="g">live</span>':'<span class="mut">paused</span>'],
 ['sp','Spend',r=>EGP(r.sp)],['sp7','last 7d',r=>EGP(r.sp7)],
 ['gp','Profit',r=>(r.gp>=0?'<span class="g">+':'<span class="r">')+EGP(r.gp)+'</span>'],
 ['gpw','Profit/wk',r=>(r.gpw>=0?'<span class="g">+':'<span class="r">')+EGP(r.gpw)+'</span>'],
 ['ga4','GA4 check',r=>G4TAG(r)],
 ['gTx','GA4 tx',r=>r.gTx===null||r.gTx===undefined?'<span class="mut">—</span>':N0(r.gTx)],
 ['gRatio','Meta ÷ GA4',r=>r.gRatio===null?'<span class="mut">—</span>':N2(r.gRatio)+'×'],
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
 ['lvl','basis','hair','win','tgt','mins','st','fmt','fn','dens'].forEach(i=>{const e=document.getElementById(i);e.onchange=()=>boot();});
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
  +' GA4 first/last touch '+(TOUCHLIVE?'<b>live</b>.':'snapshot 2026-09-20.')
  +' Per-ad GA4 cross-check '+(G4LIVE?'<b>live</b> ('+Object.keys(G4).length+' ad names).':'snapshot.');
 document.getElementById('body').innerHTML=
  TAB==='grid'?vGrid(D):TAB==='act'?vAct(D):TAB==='pred'?vPred(D):
  TAB==='store'?vStore(D):TAB==='touch'?vTouch(D):vMeth(D);
 stopPlay(); if(SCT.chart&&TAB!=='grid'){SCT.chart.destroy();SCT.chart=null;}
 if(TAB==='grid')drawScatter(D);
 if(TAB==='pred')drawPred(D);
 if(TAB==='touch')drawTouch(D);
 wireSort(boot);
}

/* ---------- 1. THE GRID ---------- */
const COL={KILL:'#e23a63',CUT:'#ff8b42',SCALE:'#12b886',REACTIVATE:'#5a5bf0',HOLD:'#9aa3b5',THIN:'#9d6bff',DEPENDS:'#f0b429'};
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
 +'<div class="banner b"><b>Colour is the verdict (profit), height is cost per purchase.</b> Right = big spender. Up = expensive. '
 +'<b>Bottom-right: proven cheap, raise it. Top-right: expensive at real money, turn it off.</b> '
 +'Left half is still testing — a cheap CPA there is mostly luck, so every dot is plotted at its <b>shrunk</b> CPA, not its raw one. '
 +'Lines: <b style="color:#9aa3b5">blended</b> '+EGP(D.cur)+' · <b style="color:#5a5bf0">target</b> '+EGP(D.target)
 +' · <b style="color:#e23a63">kill</b> '+EGP(D.kill)+' · <b style="color:#12b886">scale</b> '+EGP(D.scale)+'.</div>'
 +card('Spend vs cost per purchase — every '+NOUN[D.level],
   'Bubble size = purchases. Click any bubble to open it. Hit play to walk the window forward a week at a time and watch things drift. '+sum,
   '<div class="tp"><button class="btn" id="play">\u25b6 Play the 8 weeks</button>'
   +'<input type="range" id="scrub" min="0" value="0" step="1"/>'
   +'<span id="scrubL" class="mut"></span></div>'
   +'<div style="height:540px"><canvas id="sc"></canvas></div>')
 +card('Every ad','Click any row to open the ad. Click a column head to sort. Best/worst case is the 90% interval on the shrunk rate.',
   table(sortRows(D.rows),COLS(D),'tg'));
}
function drawScatter(D){
 const c=document.getElementById('sc'); if(!c)return;
 if(SCT.chart){SCT.chart.destroy();SCT.chart=null;}
 SCT.D=D; SCT.frame=SCT.frames-1;            // start on "all of it"
 buildFrames(D);
 SCT.chart=new Chart(c,{type:'bubble',data:{datasets:frameSets(D,SCT.frame)},
  options:{maintainAspectRatio:false,
   animation:{duration:650,easing:'easeOutQuart'},
   transitions:{active:{animation:{duration:220}}},
   onClick:(e,els)=>{if(els.length){const d=e.chart.data.datasets[els[0].datasetIndex].data[els[0].index];
     if(d&&d.r)openAd(d.r.id);}},
   onHover:(e,els)=>{e.native.target.style.cursor=els.length?'pointer':'default';},
   scales:{x:{type:'logarithmic',title:{display:true,text:'Spend in window (E£, log)'},
              grid:{color:'#eef0f5'}},
           y:{title:{display:true,text:'Cost per purchase (E\u00a3)'},
              min:-SCT.maxY*0.06,max:SCT.maxY*1.04,grid:{color:'#eef0f5'},
              ticks:{callback:v=>v<0?'':v.toLocaleString()}}},
   layout:{padding:{right:14,top:6}},
   plugins:{legend:{position:'bottom',labels:{filter:it=>it.text!=='trail'}},
    tooltip:{enabled:false,external:htmlTip}}},
  plugins:[quadrants(D),refLines(D)]});
 wireScrub(D);
}
const SCT={chart:null,frames:1,frame:0,byFrame:[],maxY:0,D:null,timer:null};
/* Frames are cumulative-to-date weekly cuts of the same window, so pressing play walks the
   grid forward and you watch an ad drift up as its CPA decays. Same estimator each frame. */
function buildFrames(D){
 const span=D.i1-D.i0, step=7, n=Math.max(1,Math.ceil(span/step));
 SCT.frames=n; SCT.byFrame=[];
 const basis=D.basis,hair=D.hair;
 for(let f=0;f<n;f++){
  const end=Math.min(D.i1,D.i0+(f+1)*step);
  const m={};
  D.rows.forEach(r=>{
   const sp=S(r.a,'sp',D.i0,end);
   if(sp<=0)return;
   const pu=S(r.a,'pu',D.i0,end),op=S(r.a,'op',D.i0,end);
   const k=basis==='on'?pu:basis==='off'?op*hair:pu+op*hair;
   const q=post(D.pr,k,sp);
   m[r.id]={x:sp,y:q.cpa,k,r};});
  SCT.byFrame.push(m);}
 const ys=[];D.rows.forEach(r=>{if(isFinite(r.cpaHi))ys.push(Math.min(r.cpaHi,D.kill*4));});
 ys.sort((a,b)=>a-b);
 SCT.maxY=Math.max(D.kill*1.25, ys.length?ys[Math.floor(ys.length*0.97)]:D.kill*2);
}
function frameSets(D,f){
 const m=SCT.byFrame[f]||{};
 const pts=Object.values(m);
 const rad=k=>Math.max(4,Math.min(26,4+Math.sqrt(Math.max(k,0))*1.6));
 const mk=act=>({label:act,
   data:pts.filter(p=>p.r.act===act).map(p=>({x:p.x,y:Math.min(p.y,SCT.maxY*1.02),r:rad(p.k),ad:p.r,k:p.k,cpa:p.y})),
   backgroundColor:COL[act]+'cc',borderColor:'#fff',borderWidth:1.5,
   hoverBackgroundColor:COL[act],hoverBorderWidth:3,hoverBorderColor:COL[act]});
 return Object.keys(COL).map(mk).filter(d=>d.data.length);
}
function quadrants(D){return {id:'q',beforeDatasetsDraw(ch){const{ctx,chartArea:a,scales}=ch;
 const ySc=scales.y.getPixelForValue(D.scale), yK=scales.y.getPixelForValue(D.kill);
 const xm=scales.x.getPixelForValue(Math.max(D.tot.sp/Math.max(D.rows.length,1),1));
 ctx.save();
 ctx.fillStyle='rgba(18,184,134,.07)';ctx.fillRect(xm,Math.max(ySc,a.top),a.right-xm,a.bottom-Math.max(ySc,a.top));
 ctx.fillStyle='rgba(226,58,99,.07)';ctx.fillRect(xm,a.top,a.right-xm,Math.min(yK,a.bottom)-a.top);
 ctx.fillStyle='rgba(120,130,160,.045)';ctx.fillRect(a.left,a.top,xm-a.left,a.bottom-a.top);
 ctx.font='800 10px sans-serif';ctx.fillStyle='rgba(90,100,125,.5)';
 ctx.fillText('TESTING',a.left+8,a.top+16);
 ctx.textAlign='right';
 ctx.fillStyle='rgba(226,58,99,.55)';ctx.fillText('EXPENSIVE AT REAL MONEY — TURN OFF',a.right-8,a.top+16);
 ctx.fillStyle='rgba(13,138,98,.55)';ctx.fillText('PROVEN CHEAP — RAISE IT',a.right-8,a.bottom-8);
 ctx.restore();}};}
function refLines(D){
 const L=[['blended',D.cur,'#9aa3b5'],['target',D.target,'#5a5bf0'],['kill',D.kill,'#e23a63'],['scale',D.scale,'#12b886']];
 return {id:'ln',afterDatasetsDraw(ch){const{ctx,chartArea:a,scales}=ch;ctx.save();
  ctx.setLineDash([5,4]);ctx.lineWidth=1.4;ctx.font='700 10px sans-serif';ctx.textAlign='left';
  L.forEach(([t,v,col],i)=>{const y=scales.y.getPixelForValue(v);if(y<a.top||y>a.bottom)return;
   ctx.strokeStyle=col;ctx.beginPath();ctx.moveTo(a.left,y);ctx.lineTo(a.right,y);ctx.stroke();
   const lab=t+' '+EGP(v), x=a.left+8+i*(Math.min(150,(a.right-a.left-40)/4));
   ctx.fillStyle='#fff';ctx.fillRect(x-3,y-13,ctx.measureText(lab).width+6,13);
   ctx.fillStyle=col;ctx.fillText(lab,x,y-4);});
  ctx.restore();}};}
/* HTML tooltip so the creative itself is in it -- this is a tool for looking at ads. */
function htmlTip(ctx){
 let el=document.getElementById('sctip');
 if(!el){el=document.createElement('div');el.id='sctip';document.body.appendChild(el);}
 const tt=ctx.tooltip;
 if(!tt.opacity){el.style.opacity=0;return;}
 const p=tt.dataPoints&&tt.dataPoints[0]; if(!p)return;
 const d=p.dataset.data[p.dataIndex], r=d.ad;
 el.innerHTML='<div class="tw">'+thumb(r,64)+'<div style="min-width:0">'
  +'<div class="tn">'+r.n+'</div>'
  +'<div class="tg2 '+r.act.toLowerCase().slice(0,5)+'">'+VERB[r.act]+'</div>'
  +'<div class="tl">'+EGP(d.x)+' spent · '+N1(d.k)+' purchases</div>'
  +'<div class="tl"><b>'+EGP(d.cpa)+'</b> each · '+EGP(r.cpaLo)+'–'+EGP(r.cpaHi)+'</div>'
  +'<div class="tl">online '+N2(r.roasOn)+'× · store '+N2(r.roasOff)+'×</div>'
  +'<div class="tl mut">'+(r.lvl==='ad'?r.cmp:r.kids+' ads')+'</div></div></div>';
 const b=ctx.chart.canvas.getBoundingClientRect();
 el.style.opacity=1;
 el.style.left=(b.left+scrollX+tt.caretX+16)+'px';
 el.style.top=(b.top+scrollY+tt.caretY-24)+'px';
}
function wireScrub(D){
 const sl=document.getElementById('scrub'); if(!sl)return;
 sl.max=SCT.frames-1; sl.value=SCT.frames-1;
 sl.oninput=()=>{stopPlay();setFrame(+sl.value);};
 document.getElementById('play').onclick=()=>{
  if(SCT.timer){stopPlay();return;}
  document.getElementById('play').textContent='⏸ Pause';
  let f=(SCT.frame>=SCT.frames-1)?0:SCT.frame;
  setFrame(f);
  SCT.timer=setInterval(()=>{f++;if(f>=SCT.frames){stopPlay();return;}
   setFrame(f);document.getElementById('scrub').value=f;},900);};
 setFrame(SCT.frames-1);
}
function stopPlay(){if(SCT.timer){clearInterval(SCT.timer);SCT.timer=null;}
 const b=document.getElementById('play'); if(b)b.textContent='▶ Play the 8 weeks';}
function setFrame(f){
 const D=SCT.D; if(!D||!SCT.chart)return;
 SCT.frame=f;
 SCT.chart.data.datasets=frameSets(D,f);
 SCT.chart.update();
 const end=new Date(WSTART); end.setDate(end.getDate()+Math.min(D.i1,D.i0+(f+1)*7)-1);
 const st=new Date(WSTART); st.setDate(st.getDate()+D.i0);
 const lab=document.getElementById('scrubL');
 if(lab)lab.textContent=st.toISOString().slice(0,10)+' → '+end.toISOString().slice(0,10)
  +'  ('+(f+1)+' of '+SCT.frames+' weeks)';
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
function swing(r){
 const c=x=>x>=0?'#0d8a62':'#b81f45';
 return '<div class="swing">'
 +'<div><span class="h">store 0%</span><span style="color:'+c(r.gp0)+'">'+EGP(r.gp0)+'</span></div>'
 +'<div><span class="h">store 21.4%</span><span style="color:'+c(r.gp)+'">'+EGP(r.gp)+'</span></div>'
 +'<div><span class="h">store 100%</span><span style="color:'+c(r.gp1)+'">'+EGP(r.gp1)+'</span></div></div>';}
function doCard(r,D){
 return '<div class="doc" onclick="openAd(\''+r.id+'\')">'+thumb(r,52)
 +'<div style="min-width:0"><div class="t">'+r.n+'</div>'
 +'<div class="s">'+whyShort(r,D)+'</div>'+swing(r)
 +'<div class="m"><b style="color:'+(r.gp>=0?'#0d8a62':'#b81f45')+'">'+(r.gp>=0?'+':'')+EGP(r.gp)+' profit</b>'
 +' &nbsp;·&nbsp; '+EGP(r.sp7)+'/wk &nbsp;·&nbsp; online '+N0(r.pu)+' @ '+EGP(r.cppOn)+' ('+N2(r.roasOn)+'\u00d7)'
 +' &nbsp;·&nbsp; store '+N0(r.op)+' @ '+EGP(r.cppOff)+' ('+N2(r.roasOff)+'\u00d7)'
 +(r.lvl==='ad'&&r.ga4&&r.ga4!=='nodata'?' &nbsp;·&nbsp; GA4 saw '+N0(r.gTx)+' ('+(r.gRatio===null?'\u2014':N2(r.gRatio)+'\u00d7 Meta')+')':'')
 +'</div></div></div>';}
/* When CPA and profit disagree it is almost always AOV. Say so on the card rather than
   letting the reader find a 25x ROAS sitting under the word "kill". */
function aovNote(r,D){
 const a=D.tot.val/Math.max(D.tot.k,1);
 if(!(r.aovK>a*1.25)&&!(r.aovK<a*0.8))return '';
 return ' <span class="mut">Basket '+EGP(r.aovK)+' vs '+EGP(a)+' account \u2014 '
  +(r.aovK>a?'fewer, bigger orders, so its CPA reads worse than its profit does.'
           :'more, smaller orders, so its CPA flatters it.')+'</span>';}
function whyShort(r,D){
 const it=r.lvl==='ad'?'it':'the whole '+noun(D);
 if(r.act==='DEPENDS'&&r.ga4==='contradicts'){
  return '<b>Meta says this works. GA4 has never seen a single sale from it.</b> Meta claims '+N0(r.pu)
   +' online purchases; GA4 recorded '+N0(r.gTx)+' transactions on '+N0(r.gSess)+' sessions from this ad name. '
   +'<b>No raise until that is explained</b> \u2014 it is running at '+EGP(r.sp7)+'/wk.';}
 if(r.act==='DEPENDS'){
  const sh=(r.pv+r.fv)>0?r.fv/(r.pv+r.fv):0;
  return '<b>The answer depends entirely on whether Meta\u2019s store attribution is real.</b> '
   +Math.round(sh*100)+'% of the value claimed here is in-store. Believe Meta and it makes '+EGP(r.gp1)
   +'; count only what the pixel saw and it loses '+EGP(-r.gp0)+'. <b>No instruction until that is settled</b> \u2014 '
   +'it is currently running at '+EGP(r.sp7)+'/wk.';}
 if(D.judge==='cpa'){
  if(r.act==='KILL')return 'Costs <b>'+EGP(r.cpa)+'</b> a purchase against a '+EGP(D.target)+' target; even its best case ('+EGP(r.cpaLo)+') misses. <b>Turn '+it+' off.</b>'+aovNote(r,D);
  if(r.act==='CUT')return 'Reads <b>'+EGP(r.cpa)+'</b>, over the '+EGP(D.kill)+' kill line, but the data still allows '+EGP(r.cpaLo)+'. <b>Halve the budget.</b>'+aovNote(r,D);
  if(r.act==='SCALE')return 'Buys at <b>'+EGP(r.cpa)+'</b>, worst case '+EGP(r.cpaHi)+', under the '+EGP(D.scale)+' scale line. <b>Raise to '+EGP(r.sp7*1.2)+'/wk.</b>';
  if(r.act==='REACTIVATE')return 'Paused, bought at <b>'+EGP(r.cpa)+'</b> on '+N1(r.k)+' purchases. <b>Switch '+it+' back on.</b>';
  if(r.act==='THIN')return 'Only '+N1(r.k)+' purchases. <b>Let it run.</b>';
  return 'At '+EGP(r.cpa)+' it sits between the lines. <b>No move the data supports.</b>';}
 const per=r.sp7>0?' ('+EGP(1000*r.gpw/r.sp7)+' back per E\u00a31,000 spent)':'';
 if(r.act==='KILL')return '<b>Loses '+EGP(-r.gp)+'</b> across the window, '+EGP(-r.gpw)+' a week at today\u2019s budget \u2014 and its best case ('
  +EGP(r.gpwHi)+'/wk) still does not reach zero. <b>Turn '+it+' off</b> and keep the '+EGP(r.sp7)+'/wk.'+aovNote(r,D);
 if(r.act==='CUT')return 'Down <b>'+EGP(-r.gp)+'</b> across the window, but the interval still allows '+EGP(r.gpwHi)+'/wk. '
  +'<b>Halve the budget</b> instead of killing it, and re-read in a week.'+aovNote(r,D);
 if(r.act==='SCALE')return 'Makes <b>'+EGP(r.gp)+'</b>'+per+' and buys at '+EGP(r.cpa)+', under the '+EGP(D.scale)
  +' scale line even at its worst. <b>Raise to '+EGP(r.sp7*1.2)+'/wk</b>, then re-read.'+aovNote(r,D);
 if(r.act==='REACTIVATE')return 'Paused, but it made <b>'+EGP(r.gp)+'</b> on '+N1(r.k)+' purchases at '+EGP(r.cpa)
  +' each. <b>Switch '+it+' back on</b> unless it was a one-off promo.'+aovNote(r,D);
 if(r.act==='THIN')return 'Only '+N1(r.k)+' purchases \u2014 the data cannot tell a good one from a lucky one yet. <b>Let it run.</b>';
 return 'Makes <b>'+EGP(r.gp)+'</b>'+per+', but at '+EGP(r.cpa)+' there is no headroom to scale. <b>Leave it alone.</b>'+aovNote(r,D);}
const G4LAB={confirms:['GA4 agrees','scale'],overclaims:['Meta claims 3×+','cut'],
 contradicts:['GA4 sees none','kill'],thin:['too few','hold'],nodata:['no GA4 row','hold']};
function G4TAG(r){if(r.lvl!=='ad'||!r.ga4)return '<span class="mut">—</span>';
 const t=G4LAB[r.ga4]||['?','hold'];return '<span class="tg '+t[1]+'" title="Meta '+N0(r.pu)+' online purchases vs GA4 '+(r.gTx===null?'no data':N0(r.gTx)+' transactions')+'">'+t[0]+'</span>';}
/* the Simple / Everything toggle applies here too -- this table was 30 columns wide */
function PCOLS(all){
 if((document.getElementById('dens')||{}).value==='f')return all;
 const K=['n','sp7','k7','fc','cpaR','gpw','ga4','trend'];
 return all.filter(c=>K.indexOf(c[0])>-1);}
function sec(title,n,note,body){
 return '<div class="hd"><h2>'+title+'</h2><span class="n">'+n+'</span></div>'
  +(note?'<div class="mut" style="font-size:11.8px;margin:-4px 0 9px;line-height:1.55">'+note+'</div>':'')+body;}

function vAct(D){
 const S=simulate(D), bh=budgetHoldTest(D.basis,D.hair,D.level);
 const dl=S.cpaNow>0?(S.cpaNew/S.cpaNow-1):0;
 const pick=a=>D.rows.filter(r=>r.act===a).sort((x,y)=>y.sp7-x.sp7||y.sp-x.sp);
 const kill=pick('KILL'), cut=pick('CUT'), scale=pick('SCALE'),
       react=D.rows.filter(r=>r.act==='REACTIVATE').sort((a,b)=>b.gpPerK-a.gpPerK);
 const dead=D.rows.filter(r=>r.st==='act'&&r.k<1&&r.sp>=(D.tot.val/Math.max(D.tot.k,1))*0.5);
 const grid=list=>list.length?'<div class="do">'+list.map(r=>doCard(r,D)).join('')+'</div>'
   :'<div class="mut" style="font-size:12.5px">Nothing qualifies.</div>';
 const drag=bh.med||1, dragF=bh.medFlat||1;
 const depends=D.rows.filter(r=>r.act==='DEPENDS').sort((a,b)=>b.sp7-a.sp7);
 const liveAll=D.rows.filter(r=>r.st==='act');
 const decidable=liveAll.filter(r=>r.rob!=='depends');
 const swing0=liveAll.reduce((s2,r)=>s2+r.gp0,0), swing1=liveAll.reduce((s2,r)=>s2+r.gp1,0);
 const losers=D.rows.filter(r=>r.st==='act'&&r.gp<0).sort((a,b)=>a.gp-b.gp);
 const lost=losers.reduce((s2,r)=>s2+r.gp,0);
 const winners=D.rows.filter(r=>r.gp>0);
 return '<div class="kpis">'
 +kpi('Decidable now',decidable.length+' of '+liveAll.length,'same call whatever the store credit','#12b886')
 +kpi('Undecidable',depends.length+' live','verdict flips with the store number','#f0b429')
 +kpi('The swing',EGP(swing1-swing0),'profit gap between 0% and 100% store credit','#f0b429')
 +kpi('Losing money',losers.length+' live',EGP(-lost)+' gone at 21.4% credit','#e23a63')
 +kpi('Making money',winners.length,'+'+EGP(winners.reduce((s2,r)=>s2+r.gp,0)),'#12b886')
 +kpi('Turn off',kill.length,'frees '+EGP(S.freed)+'/wk','#e23a63')
 +kpi('Cut budget',cut.length,'probably bad, not proven','#ff8b42')
 +kpi('Raise 20%',scale.length,'can absorb '+EGP(S.scale.reduce((s,r)=>s+r.sp7*0.2,0))+'/wk','#12b886')
 +kpi('Turn back on',react.length,'paused and proven','#5a5bf0')
 +kpi('CPA now',EGP(S.cpaNow),'current split, same estimator')
 +kpi('CPA after',EGP(S.cpaNew),PC(dl)+' if the kept ones hold their rate',dl<0?'#12b886':'#e23a63')
 +kpi('Purchases',PC(S.expK/Math.max(S.expNow,1e-9)-1),'volume — if this falls, the CPA win is fake',S.expK>=S.expNow?'#12b886':'#e23a63')
 +kpi('Gross profit',EGP(S.gpDelta)+'/wk','at '+(Math.round(S.marg*1000)/10)+'% blended margin',S.gpDelta>=0?'#12b886':'#e23a63')
 +'</div>'
 +'<div class="banner b"><b>What this is judging.</b> Gross profit minus spend, per '+NOUN[D.level]+', at '
 +Math.round(D.hair*100)+'% in-store credit. Nothing that makes money can be told to turn off. '
 +'Cost per purchase still decides which of the profitable ones have room to scale, and every CPA is <b>shrunk</b> with a 90% interval '
 +'so a lucky three-purchase '+NOUN[D.level]+' cannot buy its way onto the raise list. Click anything to open it.</div>'
 +(D.judge==='cpa'?'<div class="banner r"><b>You are on CPA-only mode — the method exactly as written.</b> '
 +'It will tell you to turn off ads that make money, because cost per purchase punishes an ad for selling fewer, bigger baskets. '
 +'On this account in-store basket size runs from '+EGP(Math.min.apply(null,D.rows.filter(r=>r.aovK>0).map(r=>r.aovK)))
 +' to '+EGP(Math.max.apply(null,D.rows.map(r=>r.aovK)))+' across '+NOUN[D.level]+'s. Switch <b>Judge on</b> back to Profit unless you are deliberately comparing.</div>':'')
+'<div class="banner r"><b>Read this before acting on any list below.</b> '
 +'Whether a Meta ad here makes money is decided almost entirely by one number nobody has verified: how much of the in-store revenue '
 +'Meta claims is actually caused by the ad. Across the '+liveAll.length+' live '+NOUN[D.level]+'s, total profit is '
 +EGP(swing0)+' if you credit none of it and '+EGP(swing1)+' if you credit all of it — a '+EGP(swing1-swing0)+' swing on the same spend. '
 +'<b>'+depends.length+' of '+liveAll.length+' change sign inside that range</b> and get no instruction at all; they are in their own section. '
 +'The '+decidable.length+' that do not are the ones you can act on today. '
 +'The measurement that would settle the rest is a geo holdout on store revenue, not another attribution setting.</div>'
+(depends.length?sec('Cannot say yet — the store question decides these',
   depends.length+' live · '+EGP(depends.reduce((s2,r)=>s2+r.sp7,0))+' a week riding on it',
   'Each of these is profitable at Meta\u2019s own in-store numbers and loss-making at the pixel-only numbers. '
   +'No instruction is issued for them because the data does not contain one. Leave them running and go settle the attribution question — '
   +'that is the single highest-value thing on this page.',grid(depends.slice(0,12))):'')
+sec('Losing money right now',losers.length+' live '+NOUN[D.level]+'s · '+EGP(-lost)+' gone',
   'Gross profit minus spend, at '+Math.round(D.hair*100)+'% in-store credit and the vault margins (16.1% delivered online, 24.3% in store). '
   +'Everything here is taking money out at the 21.4% credit. The ones that also lose at 100% credit are in the turn-off list below; '
   +'the rest are in the undecidable section. Sorted by how much.',grid(losers.slice(0,24)))
+sec('Turn these off',kill.length+' '+NOUN[D.level]+'s · '+EGP(S.freed)+' a week',
   D.judge==='cpa'?'Best case still above the '+EGP(D.kill)+' kill line.'
   :'<b>Safe to act on.</b> These lose money whether you credit Meta with none of the in-store revenue, the measured 21.4%, or all of it, '
   +'and the optimistic end of the interval still does not reach zero.',grid(kill))
 +sec('Cut these back',cut.length+' '+NOUN[D.level]+'s · '+EGP(cut.reduce((s2,r)=>s2+r.sp7,0))+' a week',
   'Losing money on the point estimate, but the interval still allows break-even. Halve, do not kill.',grid(cut))
 +sec('Raise these 20%',scale.length+' '+NOUN[D.level]+'s · +'+EGP(S.scale.reduce((s2,r)=>s2+r.sp7*0.2,0))+' a week',
   '<b>Safe to act on.</b> Profitable at every in-store credit from 0 to 100%, and worst-case CPA still beats the '+EGP(D.scale)+' scale line. One step, then re-read.',grid(scale))
 +sec('Turn these back on',react.length+' '+NOUN[D.level]+'s',
   'Paused, at least 3 purchases, and they returned more per pound of spend than the account average while they ran. '
   +'Best '+Math.min(12,react.length)+' of '+react.length+' shown, ranked by return on spend. Check each was not a one-off promo creative.',grid(react.slice(0,12)))
 +(dead.length?sec('Zero purchases on real money',dead.length+' ads',
   'Spent more than half an AOV and bought nothing. No estimate needed.',grid(dead)):'')
 +sec('The whole list, with both ROAS','','Sort any column. Online and in-store shown separately — in this window their per-ad costs correlate '+N2(corrOnOff(D.rows))+', so a winner on one is not a winner on the other.',
   table(sortRows(D.rows),COLS(D),'tg'))
 +sec('Does raising budget actually cost you CPA?',bh.n+' raises vs '+bh.nFlat+' flat-budget controls',
   'Every week-on-week budget rise of 20%+ in the window and what CPA did the week after \u2014 measured against '+NOUN[D.level]
   +'s whose budget barely moved over the same weeks. Without that control the ordinary week-to-week decay gets booked as a '
   +'scaling penalty, which is the mistake the first version of this page made.',
   '<div class="kpis">'
   +kpi('Raised 20%+',bh.med===null?'\u2014':PC(bh.med-1),'median CPA move, n='+bh.n,(drag>1?'#e23a63':'#12b886'))
   +kpi('Flat budget (control)',bh.medFlat===null?'\u2014':PC(bh.medFlat-1),'median CPA move, n='+bh.nFlat,(dragF>1?'#e23a63':'#12b886'))
   +kpi('Cut 20%+',bh.medCut===null?'\u2014':PC(bh.medCut-1),'median CPA move, n='+bh.nCut)
   +kpi('Reach bought',PC(bh.reach),'by the ones that raised','#12b886')
   +kpi('Frequency moved',PC(bh.freq),'barely \u2014 the money bought new people')
   +kpi('CPM moved',PC(bh.cpm),'the auction did not punish it')+'</div>'
   +'<div class="banner '+(drag<=dragF?'b':'r')+'">'
   +(drag<=dragF
     ? '<b>Raising budget did not cost CPA here.</b> A 20%+ rise moved CPA '+PC(drag-1)
       +'; budgets that sat still over the same weeks moved '+PC(dragF-1)+'. The rise bought '+PC(bh.reach)
       +' more reach at '+PC(bh.cpm)+' CPM with frequency '+PC(bh.freq)
       +' \u2014 new people, not more impressions on the same ones. Cutting 20%+ improved CPA '+PC((bh.medCut||1)-1)
       +' and gave up '+PC(bh.reachCut)+' of reach: that is the actual trade.'
     : '<b>Raising budget did cost CPA here.</b> Raised '+PC(drag-1)+' against '+PC(dragF-1)
       +' for the flat control \u2014 treat the raise list as smaller than it looks.')
   +'</div>'
   +(bh.n?table(bh.rows.slice(0,20).concat(bh.rows.slice(-20)),
     [['n','Ad',r=>'<span class="nm">'+r.n+'</span>'],['g','Budget rise',r=>PC(r.g)],
      ['c1','CPA before',r=>EGP(r.c1)],['c2','CPA after',r=>EGP(r.c2)],
      ['r','Change',r=>(r.r>1?'<span class="r">':'<span class="g">')+PC(r.r-1)+'</span>'],
      ['dR','Reach',r=>PC(r.dR)],['dF','Frequency',r=>PC(r.dF)],['dM','CPM',r=>PC(r.dM)],
      ['k1','Purch before',r=>N1(r.k1)],['k2','after',r=>N1(r.k2)]]):''));
}
function corrOnOff(rows){const a=rows.filter(r=>isFinite(r.cppOn)&&isFinite(r.cppOff));
 return corr(a.map(r=>r.cppOn),a.map(r=>r.cppOff));}

/* ---------- 3. PREDICT ---------- */
function vPred(D){
 const live=D.rows.filter(r=>r.st==='act'&&r.sp7>0);
 const f=live.map(r=>{const e=r.sp7/1000;
   return Object.assign({},r,{fc:e*r.lamR,fcLo:e*1000/r.cpaRHi,fcHi:e*1000/r.cpaRLo});});
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
 +card('Next 7 days, per ad','Forecast at each '+NOUN[D.level]+'\'s own last-7d budget, on its last-14-day rate. Profit uses 16.1% delivered margin online and 24.3% in store, so it matches the verdicts.',
   table(sortRows(f).slice(0,80),PCOLS([
    ['n','Ad',adCell],['sp7','Budget 7d',r=>EGP(r.sp7)],
    ['k7','Purch last 7d',r=>N1(r.k7)],['fc','Forecast next 7d',r=>'<b>'+N1(r.fc)+'</b>'],
    ['fcLo','lo',r=>N1(r.fcLo)],['fcHi','hi',r=>N1(r.fcHi)],
    ['cpa','CPA window',r=>EGP(r.cpa)],['cpaR','CPA last 14d',r=>'<b>'+EGP(r.cpaR)+'</b>'],
    ['decay','Decay',r=>r.decay===null?'—':(r.decay>0?'<span class="r">':'<span class="g">')+PC(r.decay)+'</span>'],
    ['roas','ROAS',r=>N2(r.roas)],
    ['gpw','Profit next 7d',r=>(r.gpw<0?'<span class="r">':'<span class="g">')+EGP(r.gpw)+'</span>'],
    ['ga4','GA4 check',r=>G4TAG(r)],
    ['trend','Trend',r=>r.trend===null?'<span class="mut">n/a</span>':(r.trend>0?'<span class="r">+':'<span class="g">')+Math.round(r.trend*100)+'%</span>'+(r.trendSig?' *':'')]])))
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
    ['ga4','GA4 check',r=>G4TAG(r)],
 ['gTx','GA4 tx',r=>r.gTx===null||r.gTx===undefined?'<span class="mut">—</span>':N0(r.gTx)],
 ['gRatio','Meta ÷ GA4',r=>r.gRatio===null?'<span class="mut">—</span>':N2(r.gRatio)+'×'],
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
 const bh=budgetHoldTest(D.basis,D.hair,D.level);
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
