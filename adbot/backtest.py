"""Replays every rule week by week over a year of ad-weeks (run monthly: python backtest.py adbot_raw.json)."""
import json,collections,sys,math
sys.path.insert(0,"/Users/shafi/Documents/Obsidian Vault/_deploy/adbot")
import engine as EN
R=json.load(open(sys.argv[1] if len(sys.argv)>1 else "adbot_raw.json")); rows=R["rows"]; sets=R["sets"]; camps=R["camps"]; auds=R["auds"]
typ={sid:EN.audience_type(s,auds,camps.get(s.get("campaign_id"),{}).get("name","")) for sid,s in sets.items()}
for r in rows:
    if r["adset"] not in typ: typ[r["adset"]]=EN.audience_type({"name":r["adset_name"]},auds,r["camp_name"])
W=sorted({r["wk"] for r in rows}); wi={w:i for i,w in enumerate(W)}; NW=len(W)
K=("sp","oc","pu7","pv7","op7","ov7")
def series(key):
    S=collections.defaultdict(lambda:[[0.0]*NW for _ in K])
    for r in rows:
        e=S[r[key]]; i=wi[r["wk"]]
        for j,k in enumerate(K): e[j][i]+=r[k]
    return S
AD=series("ad"); AS=series("adset")
ad2set={r["ad"]:r["adset"] for r in rows}
def win(e,a,b):
    a=max(0,a); return [sum(e[j][a:b+1]) for j in range(len(K))]
def roas(v): return (v[3]+v[5])/v[0] if v[0] else 0
def need(v,t):
    tot=(v[3]+v[5]) or 1; st=v[5]/tot; return 1/(EN.M_ON*(1-st)+EN.M_ST*st)/EN.INCR[t]
def pur(v): return v[2]+v[4]
# each type's CPA at each week = trailing 4 weeks across the type (what the engine would have known then)
TC={}
for t in EN.INCR:
    for i in range(NW):
        sp=pu=0
        for sid,e in AS.items():
            if typ.get(sid)!=t: continue
            v=win(e,i-3,i); sp+=v[0]; pu+=pur(v)
        TC[(t,i)]=sp/pu if pu else None
def wil(k,n):
    if not n: return (0,0)
    z=1.96;p=k/n;d=1+z*z/n;c=p+z*z/(2*n);r=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n));return (round((c-r)/d*100),round((c+r)/d*100))
OUT={}
def rep(name,grid):
    print("\n==",name)
    for g,(k,n,extra) in grid: 
        lo,hi=wil(k,n); print(f"   {g:42s} n={n:4d}  right {k/n*100 if n else 0:5.1f}%  (CI {lo}-{hi}%) {extra}")
# ---- 1. PAUSE an ad: lifetime spend >= S x typeCPA and lifetime ROAS < k x need -> did it STAY under need over its next 4 weeks (if it kept spending)?
g=[]
for S in (3,5,8):
    for kk in (0.4,0.5,0.6,0.8):
        hit=n=0; fp_lost=0
        for ad,e in AD.items():
            t=typ.get(ad2set.get(ad),"new")
            for i in range(4,NW-4):
                tc=TC.get((t,i)); 
                if not tc: continue
                life=win(e,0,i)
                if life[0]<S*tc or life[0]-e[0][i]>=S*tc: continue   # first week it crosses S
                nd=need(life,t)
                if roas(life)>=kk*nd: break
                nxt=win(e,i+1,i+4)
                if nxt[0]<500: break
                n+=1; hit+= roas(nxt)<nd
                break
        g.append((f"spend>={S}xCPA, ROAS<{kk}xneed",(hit,n,"")))
rep("PAUSE AD (right = it kept losing over the next 4 weeks)",g)
# ---- 2. DEAD: zero purchases after S x typeCPA -> did it ever reach need on the next 4 weeks?
g=[]
for S in (2,3,5):
    hit=n=0
    for ad,e in AD.items():
        t=typ.get(ad2set.get(ad),"new")
        for i in range(4,NW-4):
            tc=TC.get((t,i))
            if not tc: continue
            life=win(e,0,i)
            if life[0]<S*tc or life[0]-e[0][i]>=S*tc: continue
            if pur(life)>0: break
            nxt=win(e,i+1,i+4)
            if nxt[0]<300: break
            n+=1; hit+= roas(nxt)<need(nxt if nxt[0] else life,t); break
    g.append((f"zero purchases at {S}xCPA",(hit,n,"")))
rep("DEAD AD (right = still under need next 4 weeks)",g)
# ---- 3. SCALE ad set: last week ROAS >= m x need & purchases >= p -> next 2 weeks still >= need; and when spend actually rose 20%+
g=[]
for m in (1.1,1.3,1.5,2.0):
    for p in (5,10,20):
        hit=n=0; h2=n2=0
        for sid,e in AS.items():
            t=typ.get(sid,"new")
            for i in range(4,NW-2):
                v=win(e,i,i); 
                if pur(v)<p or not v[0]: continue
                nd=need(win(e,i-3,i),t)
                if roas(v)<m*nd: continue
                nxt=win(e,i+1,i+2)
                if nxt[0]<500: continue
                n+=1; ok=roas(nxt)>=nd; hit+=ok
                if e[0][i+1]>=1.2*v[0]: n2+=1; h2+=ok
        lo2,hi2=wil(h2,n2)
        g.append((f"ROAS>={m}xneed, purch>={p}",(hit,n,f"| when spend rose 20%+: {h2}/{n2} held ({h2/n2*100 if n2 else 0:.0f}%, CI {lo2}-{hi2}%)")))
rep("SCALE AD SET (right = stayed above need the next 2 weeks)",g)
# ---- 4. TRIM ad set: last week ROAS < j x need & spend >= 3xCPA -> next 2 weeks still under need
g=[]
for j in (0.6,0.8,1.0):
    hit=n=0
    for sid,e in AS.items():
        t=typ.get(sid,"new")
        for i in range(4,NW-2):
            tc=TC.get((t,i)); v=win(e,i,i)
            if not tc or v[0]<3*tc: continue
            nd=need(win(e,i-3,i),t)
            if roas(v)>=j*nd: continue
            nxt=win(e,i+1,i+2)
            if nxt[0]<500: continue
            n+=1; hit+=roas(nxt)<nd
    g.append((f"ROAS<{j}xneed, spend>=3xCPA",(hit,n,"")))
rep("TRIM AD SET (right = stayed under need the next 2 weeks)",g)
# ---- 5. WINNER ad: 28-day ROAS >= m x need & spend >= k x CPA -> next 4 weeks >= 1.2 x need
g=[]
for m in (1.5,2.0,3.0):
    for kk in (5,10,20):
        hit=n=0
        for ad,e in AD.items():
            t=typ.get(ad2set.get(ad),"new")
            for i in range(4,NW-4):
                tc=TC.get((t,i)); v=win(e,i-3,i)
                if not tc or v[0]<kk*tc: continue
                nd=need(v,t)
                if roas(v)<m*nd: continue
                nxt=win(e,i+1,i+4)
                if nxt[0]<1000: continue
                n+=1; hit+=roas(nxt)>=1.2*nd
        g.append((f"28d ROAS>={m}xneed, spend>={kk}xCPA",(hit,n,"")))
rep("WINNER AD (right = 1.2x+ need over the next 4 weeks)",g)
# ---- 6. DECAY: profitable ad whose last 2 weeks ROAS < d x its own best 4-week ROAS -> next 2 weeks under need
g=[]
for d in (0.5,0.6,0.7):
    hit=n=0
    for ad,e in AD.items():
        t=typ.get(ad2set.get(ad),"new")
        for i in range(6,NW-2):
            best=max((roas(win(e,j-3,j)) for j in range(3,i-1) if win(e,j-3,j)[0]>=2000),default=0)
            v=win(e,i-1,i)
            if best==0 or v[0]<1500: continue
            nd=need(win(e,i-3,i),t)
            if best<1.2*nd or roas(v)>=d*best: continue
            nxt=win(e,i+1,i+2)
            if nxt[0]<500: continue
            n+=1; hit+=roas(nxt)<nd
    g.append((f"last 2w ROAS < {d}x its best 4 weeks",(hit,n,"")))
rep("DECAY AD (right = fell under need the next 2 weeks)",g)
print("\ntypes:",collections.Counter(typ.values()))
