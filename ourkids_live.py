#!/usr/bin/env python3
"""
OurKids live dashboard builder v2.
Pulls Odoo (online daily + per-channel daily + branch POS accounting), Meta, Shopify,
Google, TikTok. Computes sales annotations. Writes docs/ourkids/data.js.
Runs in GitHub Actions on a schedule. No assistant. Defensive per source.
"""
import os, sys, json, time, datetime, statistics, urllib.request, urllib.parse, urllib.error, re

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs", "ourkids")
os.makedirs(DOCS, exist_ok=True)
FIN_START = "2024-08-05"
# The business runs on Cairo time (UTC+3). GitHub Actions runners are UTC, so
# datetime.date.today() there is one calendar day behind Egypt for the three
# hours after Cairo midnight -- which silently dropped the most recent FULL
# Egyptian trading day from every chart. Anchor the window to Cairo.
CAIRO = datetime.timezone(datetime.timedelta(hours=3))
today = datetime.datetime.now(CAIRO).date()
# v6.2: END used to be yesterday, which left the dashboard permanently 1-2 days
# behind on MTD. END is now TODAY. The last calendar day is a PARTIAL day and is
# flagged in the payload (O.partial) so the frontend can mark it.
END = today
FULLEND = today - datetime.timedelta(days=1)
AD_START = END - datetime.timedelta(days=364)
BL_START = END - datetime.timedelta(days=119)          # branches: 120d so compare works
def drange(a, b):
    out, d = [], a
    while d <= b:
        out.append(d.isoformat()); d += datetime.timedelta(days=1)
    return out
LOGBUF = []
def log(*a):
    line = "[ourkids] " + " ".join(str(x) for x in a)
    LOGBUF.append(line)
    if len(LOGBUF) > 400: del LOGBUF[:len(LOGBUF) - 400]
    sys.stderr.write(line + "\n")

def http_json(url, data=None, headers=None, tries=5, timeout=90):
    hd = {"Content-Type": "application/json"}; hd.update(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=hd, method="POST" if body else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e.read().decode("utf-8", "ignore")
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(min(60, 2 ** i * 4)); continue
            log("HTTP", e.code, url[:60], last[:160]); return {"error": last}
        except Exception as e:
            last = str(e); time.sleep(2 ** i)
    return {"error": last}

# ------------------------------------------------------------------ ODOO
def odoo_creds():
    s = os.environ.get("ODOO_SERVER", "").strip()
    if s:
        return s, os.environ["ODOO_DB"], os.environ["ODOO_LOGIN"], os.environ["ODOO_APIKEY"]
    p = os.path.join(ROOT, "odoo_read.py")
    if os.path.exists(p):
        t = open(p).read()
        g = lambda k: re.search(k + r'\s*=\s*"([^"]+)"', t).group(1)
        return g("SERVER"), g("DB"), g("LOGIN"), g("APIKEY")
    raise RuntimeError("no Odoo credentials")

_CR = [None]; _UID = [None]
def _rpc(service, method, args):
    server = _CR[0][0]
    d = http_json(server + "/jsonrpc", {"jsonrpc": "2.0", "method": "call",
        "params": {"service": service, "method": method, "args": args}})
    return d.get("result") if isinstance(d, dict) else None
def ogroup(model, domain, fields, groupby):
    if _CR[0] is None: _CR[0] = odoo_creds()
    s, db, lo, k = _CR[0]
    if _UID[0] is None: _UID[0] = _rpc("common", "authenticate", [db, lo, k, {}])
    return _rpc("object", "execute_kw",
        [db, _UID[0], k, model, "read_group", [domain, fields, groupby], {"lazy": False}]) or []
def oexec(model, method, args, kw=None):
    if _CR[0] is None: _CR[0] = odoo_creds()
    sv, db, lo, k = _CR[0]
    if _UID[0] is None: _UID[0] = _rpc("common", "authenticate", [db, lo, k, {}])
    return _rpc("object", "execute_kw", [db, _UID[0], k, model, method, args, kw or {}]) or []

def dkey(s):
    return datetime.datetime.strptime(s, "%d %b %Y").date().isoformat()

def pull_odoo():
    st = ["sale", "done"]
    rows = ogroup("sale.order",
        [["state", "in", st], ["date_order", ">=", FIN_START + " 00:00:00"]],
        ["amount_total", "margin"], ["date_order:day"])
    daily = {}
    for r in rows:
        try: d = dkey(r["date_order:day"])
        except Exception: continue
        daily[d] = {"rev": r.get("amount_total") or 0, "gp": r.get("margin") or 0,
                    "orders": r.get("__count", 0) or 0}
    rf = ogroup("account.move",
        [["move_type", "=", "out_refund"], ["state", "=", "posted"], ["invoice_date", ">=", FIN_START]],
        ["amount_total"], ["invoice_date:day"])
    refund = {}
    for r in rf:
        try: refund[dkey(r["invoice_date:day"])] = r.get("amount_total") or 0
        except Exception: continue
    chD = {}
    for name in ["Shopify", "Noon", "Amazon", "Homzmart"]:
        mr = ogroup("sale.order",
            [["state", "in", st], ["date_order", ">=", FIN_START + " 00:00:00"], ["team_id.name", "=", name]],
            ["amount_total"], ["date_order:day"])
        m = {}
        for r in mr:
            try: m[dkey(r["date_order:day"])] = r.get("amount_total") or 0
            except Exception: continue
        chD[name] = m
    # v6.2 CANCELLED. Revenue above only ever reads state in (sale, done), so cancelled
    # orders were already excluded -- but they were invisible, which read as "not handled".
    # Pull them explicitly, both network-wide and Shopify-only, so the number is on screen.
    cx = {}; cxn = {}; cxs = {}; cxsn = {}
    for dom, dv, dn in ((None, cx, cxn), ("Shopify", cxs, cxsn)):
        d0 = [["state", "=", "cancel"], ["date_order", ">=", FIN_START + " 00:00:00"]]
        if dom: d0 = d0 + [["team_id.name", "=", dom]]
        for r in ogroup("sale.order", d0, ["amount_total"], ["date_order:day"]):
            try: d = dkey(r["date_order:day"])
            except Exception: continue
            dv[d] = r.get("amount_total") or 0; dn[d] = r.get("__count", 0) or 0
    cxt = {}
    for r in ogroup("sale.order",
                    [["state", "=", "cancel"], ["date_order", ">=", FIN_START + " 00:00:00"]],
                    ["amount_total"], ["team_id"]):
        cxt[(r.get("team_id") or [0, "(no team)"])[1]] = [round(r.get("amount_total") or 0), r.get("__count", 0) or 0]
    ds = drange(datetime.date.fromisoformat(FIN_START), END)
    k = lambda v: int(round(v / 1000))
    fin = {"start": FIN_START, "n": len(ds), "k": 1000,
           "rev": [k(daily.get(d, {}).get("rev", 0)) for d in ds],
           "gp": [k(daily.get(d, {}).get("gp", 0)) for d in ds],
           "refund": [k(refund.get(d, 0)) for d in ds],
           "orders": [int(daily.get(d, {}).get("orders", 0)) for d in ds],
           "chD": {c: [k(chD[c].get(d, 0)) for d in ds] for c in chD},
           "cxv": [k(cx.get(d, 0)) for d in ds],
           "cxn": [int(cxn.get(d, 0)) for d in ds],
           "cxsv": [k(cxs.get(d, 0)) for d in ds],
           "cxsn": [int(cxsn.get(d, 0)) for d in ds],
           "cxTeam": cxt}
    log("odoo days", len(ds))
    return fin

BRANCH_KEYS = [("dokki", "Dokki"), ("smouha", "Smouha"), ("nasr", "Nasr City"),
               ("new cairo", "New Cairo"), ("october", "October"), ("zayed", "Zayed"),
               ("arabia", "Mall of Arabia")]
def pull_branches():
    """REAL branch retail revenue: income lines on POS sales journals, daily."""
    rows = ogroup("account.move.line",
        [["parent_state", "=", "posted"], ["date", ">=", BL_START.isoformat()],
         ["account_id.internal_group", "=", "income"], ["journal_id.name", "like", "POS"]],
        ["balance"], ["date:day", "journal_id"])
    ds = drange(BL_START, END)
    idx = {d: i for i, d in enumerate(ds)}
    br = {b: [0.0] * len(ds) for _, b in BRANCH_KEYS}
    for r in rows:
        jn = (r.get("journal_id") or [0, ""])[1].lower()
        if "cash" in jn or "event" in jn: continue
        b = next((name for kw, name in BRANCH_KEYS if kw in jn), None)
        if not b: continue
        try: i = idx[dkey(r["date:day"])]
        except Exception: continue
        br[b][i] += -(r.get("balance") or 0)
    out = {"start": BL_START.isoformat(), "n": len(ds),
           "b": {b: [int(round(v)) for v in br[b]] for b in br}}
    log("branches live", {b: int(sum(v)) for b, v in br.items()})
    return out


def pull_products():
    """Top products by revenue, last 30d vs previous 30d, from sale.order.line."""
    def win(a, b):
        rows = ogroup("sale.order.line",
            [["order_id.state", "in", ["sale", "done"]], ["display_type", "=", False],
             ["product_id", "not in", [24]],
             ["order_id.date_order", ">=", a.isoformat() + " 00:00:00"],
             ["order_id.date_order", "<=", b.isoformat() + " 23:59:59"]],
            ["price_subtotal", "product_uom_qty"], ["product_id"])
        out = {}
        for r in rows:
            p = r.get("product_id")
            if not p: continue
            nm = str(p[1])[:48]
            if "discount" in nm.lower() or "shipping" in nm.lower(): continue
            out[nm] = [round(r.get("price_subtotal") or 0), int(r.get("product_uom_qty") or 0)]
        return out
    cur = win(END - datetime.timedelta(days=29), END)
    prev = win(END - datetime.timedelta(days=59), END - datetime.timedelta(days=30))
    top = sorted(cur.items(), key=lambda x: -x[1][0])[:20]
    prod = [{"n": n, "r": v[0], "q": v[1], "p": prev.get(n, [0, 0])[0]} for n, v in top]
    # biggest decliners: in prev top but collapsed
    ptop = sorted(prev.items(), key=lambda x: -x[1][0])[:20]
    for n, v in ptop:
        if n not in cur and len(prod) < 28:
            prod.append({"n": n, "r": 0, "q": 0, "p": v[0]})
    log("products", len(prod))
    return prod

# ------------------------------------------------------------------ ADS + SHOP
GRAPH = "https://graph.facebook.com/v21.0"
def _avw(actions, keys, wk):
    for a in actions or []:
        if a.get("action_type") in keys:
            try: return float(a.get(wk) or 0)
            except Exception: return 0.0
    return 0.0

MEAS = {"base": 0.0, "w7": 0.0, "w1": 0.0,
        # v8.1: the OFFLINE (in-store) leg is measured separately from the pixel leg.
        # Meta's incremental discount on in-store is nothing like its discount on web --
        # for 22-29 Jul 2026 Ads Manager reported offline default 4,840,137, 7d-click
        # 2,033,416 and Incremental 835,852. One shared coefficient cannot express that.
        "off_base": 0.0, "off_w7": 0.0, "off_w1": 0.0,
        "incr_pix": 0.0, "incr_off": 0.0, "incr_ok": False, "incr_err": ""}

def _av(actions, keys):
    for a in actions or []:
        if a.get("action_type") in keys:
            try: return float(a.get("value") or 0)
            except Exception: return 0.0
    return 0.0
ACCT_NAMES = {"act_336343742536460": "Ourkids EGP", "act_652528128810469": "Basic"}
DEFAULT_ACCTS = list(ACCT_NAMES)

def meta_accounts(tok):
    ids = os.environ.get("META_ACCOUNT_IDS", "").strip()
    if ids:
        out = [x if x.startswith("act_") else "act_" + x for x in ids.split(",") if x.strip()]
        log("meta accounts :: pinned by META_ACCOUNT_IDS ::", ", ".join(out))
        return out
    log("meta accounts :: default pin ::", ", ".join("%s (%s)" % (a, ACCT_NAMES[a]) for a in DEFAULT_ACCTS))
    return list(DEFAULT_ACCTS)

MACC = {}

# ---------- REAL per-branch in-store attribution (v6.0) --------------------
# These are live custom conversions on the Ourkids Pixel (770014046405609).
# Each one is a branch-scoped rule over the in-store CAPI events, so Meta
# reports attributed in-store VALUE, PURCHASES and NEW CUSTOMERS per branch
# natively. They arrive inside the actions / action_values arrays we already
# request, as action_type "offsite_conversion.custom.<ID>" - no extra API call.
MCC_VAL = {                       # "In-store - <Branch>"  (purchases + value)
    "1315893053867849": "Dokki",
    "1591145186067475": "Nasr City",
    "1741617780517292": "Smouha",
    "1942459063110536": "Mall of Arabia",
    "2557183011463248": "October",
    "2883144848696205": "New Cairo",
    "4432019077021060": "Zayed"}
MCC_NC = {                        # "In-store New Customer - <Branch>"
    "1565724141655319": "Dokki",
    "2466166093832059": "Nasr City",
    "1682582056306615": "Smouha",
    "1242391012293410": "Mall of Arabia",
    "2364091880790447": "October",
    "1400874301918902": "New Cairo",
    "1339358398390619": "Zayed"}
MCC_ALLNC = "1043047855247769"    # "In-store New Customers - All Branches"

CCSEEN = {}                       # every custom conversion id actually seen -> value
CCNAME = {}                       # id -> live name, for the diagnostic log

def _cc_discover(acct, tok):
    """v8.2: resolve the in-store custom conversions LIVE for one ad account.

    Returns (value_map, newcust_map, all_branch_ids). Anything Meta does not hand back is
    left to the hardcoded v6.0 map, so a lookup failure degrades to the old behaviour
    instead of blanking the branch table."""
    val, nc, allnc = {}, {}, []
    d = None
    try:
        d = http_json("%s/%s/customconversions?%s" % (GRAPH, acct, urllib.parse.urlencode(
            {"fields": "id,name", "limit": 200, "access_token": tok})))
    except Exception as e:
        log("meta custom conversions ::", acct, ":: lookup raised ::", str(e)[:140])
        return val, nc, allnc
    rows = (d or {}).get("data")
    if rows is None:
        log("meta custom conversions ::", acct, ":: no list returned ::",
            str(((d or {}).get("error") or {}).get("message") or d)[:160])
        return val, nc, allnc
    for r in rows:
        cid = str(r.get("id") or ""); nm = (r.get("name") or "").strip()
        CCNAME[cid] = nm
        low = nm.lower()
        if not cid or ("in-store" not in low and "in store" not in low): continue
        if "all branch" in low or "all-branch" in low:
            allnc.append(cid); continue
        br = re.split(r"[-\u2013\u2014]", nm)[-1].strip()
        if not br: continue
        if "new customer" in low: nc[cid] = br
        else: val[cid] = br
    log("meta custom conversions ::", acct, ":: listed", len(rows),
        ":: branch value", len(val), ":: branch new-customer", len(nc),
        ":: all-branch", len(allnc))
    if val: log("meta custom conversions ::", acct, ":: branches ::",
                ", ".join(sorted(set(val.values()))))
    return val, nc, allnc

def _cc_harvest(day, av, acts, maps, ad):
    """Fold one insights row's custom conversions into MBR / instoreNC."""
    cv = _cc(av); ca = _cc(acts)
    for cid, v in cv.items(): CCSEEN[cid] = CCSEEN.get(cid, 0.0) + v
    for cid, v in ca.items(): CCSEEN.setdefault(cid, 0.0)
    hit = 0
    for cid, br in maps["val"].items():
        v = cv.get(cid, 0.0); pu = ca.get(cid, 0.0)
        if v or pu:
            e = MBR.setdefault(br, {}).setdefault(day, {"v": 0.0, "p": 0.0, "nc": 0.0})
            e["v"] += v; e["p"] += pu; hit += 1
    for cid, br in maps["nc"].items():
        n = ca.get(cid, 0.0)
        if n:
            e = MBR.setdefault(br, {}).setdefault(day, {"v": 0.0, "p": 0.0, "nc": 0.0})
            e["nc"] += n; hit += 1
    for cid in maps["allnc"]:
        ad["instoreNC"][day] += ca.get(cid, 0.0)
    return hit

MBR = {}                          # branch -> "YYYY-MM" -> {v, p, nc}
# v6.5: outputs that several pulls contribute to. vmon/pmon = vendor/product monthly [rev,gp];
# dec = customer deciles per scope; lag = inter-order gap histograms; bunr = unregistered revenue;
# mreach/treach = monthly deduplicated reach from Meta / TikTok.
XTRA = {}

def _attr_profile(chunk, attrs, base, baseTot, kind, topn=6):
    """Revenue share of each attribute inside this decile, plus how far it over-indexes
    against the whole base (100 = exactly average, 180 = 80% more of their money there)."""
    cur = {}
    for p in chunk:
        a2 = attrs.get(p)
        if not a2: continue
        for k, v in (a2.get(kind) or {}).items():
            if v > 0: cur[k] = cur.get(k, 0.0) + v
    tot = sum(cur.values())
    if tot <= 0: return []
    rows = []
    for k, v in cur.items():
        sh = v / tot * 100.0
        bs = (base[kind].get(k, 0.0) / baseTot[kind] * 100.0) if baseTot[kind] else 0.0
        if sh < 1.0 and (not bs or sh / bs < 1.5): continue
        rows.append([k, round(sh, 1), (round(sh / bs * 100) if bs else 999), round(v)])
    rows.sort(key=lambda x: -x[3])
    return [r[:3] for r in rows[:topn]]


def _catname(c):
    """Odoo categ_id display name 'All / Kids / Shoes' -> 'Shoes'."""
    c = str(c or "").strip()
    if not c or c.lower() == "all": return ""
    p = [x.strip() for x in c.split("/") if x.strip() and x.strip().lower() != "all"]
    return p[-1] if p else ""


def _decile(agg, ocnt, attrs=None):
    """agg: pid -> [rev, gp, qty, negrev, first_d, last_d, gross_retail, disc]. ocnt: pid -> orders.
    attrs: pid -> {"cat": {name: rev}, "ven": {name: rev}} -- optional, powers the per-decile
    "these people buy X" profile. Returns 10 rows, D1 = top spenders by lifetime net revenue."""
    pids = sorted(agg.keys(), key=lambda p: -agg[p][0])
    n = len(pids)
    if n < 50: return []
    base = {"cat": {}, "ven": {}}
    if attrs:
        for p in pids:
            a2 = attrs.get(p)
            if not a2: continue
            for kind in ("cat", "ven"):
                for k, v in (a2.get(kind) or {}).items():
                    if v > 0: base[kind][k] = base[kind].get(k, 0.0) + v
    baseTot = {kind: sum(base[kind].values()) for kind in base}
    out = []
    for i in range(10):
        chunk = pids[n * i // 10: n * (i + 1) // 10]
        r = g = q = neg = gr = dc = ls = 0.0; o = 0; rep2 = 0
        for p in chunk:
            a = agg[p]; r += a[0]; g += a[1]; q += a[2]; neg += a[3]; gr += a[6]; dc += a[7]
            import datetime as _dt
            ls += (_dt.date.fromisoformat(a[5]) - _dt.date.fromisoformat(a[4])).days
            oc = ocnt.get(p, 0); o += oc
            if oc >= 2: rep2 += 1
        row = {"c": len(chunk), "o": o, "r": round(r), "g": round(g), "q": round(q),
               "ng": round(neg), "gr": round(gr), "dc": round(dc),
               "rep": rep2, "ls": round(ls / len(chunk), 1) if chunk else 0}
        if attrs and baseTot["cat"]:
            row["ct"] = _attr_profile(chunk, attrs, base, baseTot, "cat")
            row["vn"] = _attr_profile(chunk, attrs, base, baseTot, "ven")
        out.append(row)
    return out


def _cc(lst):
    """Pull every offsite_conversion.custom.<id> entry out of an actions or
    action_values array and return {custom_conversion_id: float}."""
    out = {}
    for a in lst or []:
        t = a.get("action_type") or ""
        if t.startswith("offsite_conversion.custom."):
            try: out[t.rsplit(".", 1)[-1]] = float(a.get("value") or 0)
            except Exception: pass
    return out

def _meta_incremental(acct, c0, c1, tok):
    """v8.1: Meta's ACTUAL Incremental-attribution column, not a coefficient I made up.

    The dashboard used to render "Incremental" by multiplying live value by a hardcoded
    0.6. That is an estimate sitting on top of a number Meta will hand over for free, and
    it was wrong by a mile: over 22-29 Jul 2026 the real offline incremental was 835,852
    against a live offline figure of 4,840,137 -- a factor of 0.17, not 0.60.

    "incrementality" is an action_attribution_windows value referenced in Meta's
    breakdowns documentation but absent from the canonical enum on the insights reference
    page, so it may be rejected or may quietly disappear. This runs as its own isolated
    request: if it fails, the main pull is untouched and the dashboard falls back to the
    modelled coefficient WITH A LABEL SAYING SO, rather than pretending."""
    p = {"level": "account", "access_token": tok,
         "time_range": json.dumps({"since": c0, "until": c1}),
         "fields": "action_values", "limit": 500,
         "action_attribution_windows": json.dumps(["incrementality"])}
    d = http_json("%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p)))
    rows = (d or {}).get("data")
    if rows is None:
        e = ((d or {}).get("error") or {})
        return None, str(e.get("message") or d)[:180]
    pix = off = 0.0
    for row in rows:
        av = row.get("action_values") or []
        pix += _avw(av, ("offsite_conversion.fb_pixel_purchase",), "incrementality")
        off += _avw(av, ("offline_conversion.purchase",), "incrementality")
    return (pix, off), ""

def pull_meta(win):
    tok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    ad = {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur", "instoreNC", "mimp", "mclk", "moffv", "instoreOnsite"]}
    chunks = []
    a = datetime.date.fromisoformat(win[0]); endd = datetime.date.fromisoformat(win[-1])
    while a <= endd:
        b = min(endd, a + datetime.timedelta(days=89)); chunks.append((a.isoformat(), b.isoformat())); a = b + datetime.timedelta(days=1)
    anames = {}
    try:
        if tok:
            d0 = http_json("%s/me/adaccounts?fields=name,account_id&limit=100&access_token=%s" % (GRAPH, tok))
            for a in (d0.get("data") or []): anames["act_" + str(a.get("account_id"))] = a.get("name", "")
    except Exception: pass
    accts = meta_accounts(tok)
    CCM = {}
    for acct in accts:
      # v8.2: per-account maps. Custom conversions are account-scoped, so the second
      # account gets its OWN branch IDs instead of being scored against the first one's.
      m = {"val": dict(MCC_VAL), "nc": dict(MCC_NC), "allnc": [MCC_ALLNC]}
      if tok:
          v2, n2, a2 = _cc_discover(acct, tok)
          if v2: m["val"] = v2
          if n2: m["nc"] = n2
          if a2: m["allnc"] = a2
      CCM[acct] = m
    for acct in accts:
      an = anames.get(acct, acct)
      maps = CCM[acct]
      for c0, c1 in chunks:
        p = {"level": "account", "time_increment": 1, "access_token": tok,
             "time_range": json.dumps({"since": c0, "until": c1}),
             "fields": "spend,impressions,clicks,action_values,actions", "limit": 500,
             "action_attribution_windows": json.dumps(["7d_click", "1d_click", "1d_view"])}
        d = http_json("%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p)))
        for row in (d.get("data") or []):
            day = row.get("date_start")
            if day not in ad["mspend"]: continue
            ad["mspend"][day] += float(row.get("spend") or 0)
            ad["mimp"][day] += float(row.get("impressions") or 0)
            ad["mclk"][day] += float(row.get("clicks") or 0)
            av = row.get("action_values") or []
            pixel = _av(av, ("offsite_conversion.fb_pixel_purchase",))
            omni = _av(av, ("omni_purchase",)) or pixel
            ad["mecomrev"][day] += pixel; ad["metaOmniValue"][day] += omni
            MEAS["base"] += pixel
            MEAS["w7"] += _avw(av, ("offsite_conversion.fb_pixel_purchase",), "7d_click")
            MEAS["w1"] += _avw(av, ("offsite_conversion.fb_pixel_purchase",), "1d_click")
            offv = _av(av, ("offline_conversion.purchase",))
            ad["instoreMeta"][day] += offv
            ad["moffv"][day] += offv
            ad["instoreOnsite"][day] += max(0.0, omni - pixel - offv)
            ad["metaOfflinePur"][day] += _av(row.get("actions"), ("offline_conversion.purchase",))
            MEAS["off_base"] += offv
            MEAS["off_w7"] += _avw(av, ("offline_conversion.purchase",), "7d_click")
            MEAS["off_w1"] += _avw(av, ("offline_conversion.purchase",), "1d_click")
            ad["mpur"][day] += _av(row.get("actions"), ("offsite_conversion.fb_pixel_purchase",))
            mo = day[:7]
            _cc_harvest(day, av, row.get("actions"), maps, ad)
            mc = MACC.setdefault(an, {}).setdefault(mo, {"sp": 0, "pv": 0, "ov": 0})
            mc["sp"] += float(row.get("spend") or 0); mc["pv"] += pixel; mc["ov"] += omni
        try:
            got, err = _meta_incremental(acct, c0, c1, tok)
            if got:
                MEAS["incr_pix"] += got[0]; MEAS["incr_off"] += got[1]; MEAS["incr_ok"] = True
            elif err and not MEAS["incr_err"]:
                MEAS["incr_err"] = err
        except Exception as e:
            if not MEAS["incr_err"]: MEAS["incr_err"] = str(e)[:180]
    if not MBR and tok:
        lo = max(win[0], (datetime.date.fromisoformat(win[-1]) - datetime.timedelta(days=180)).isoformat())
        log("meta custom conversions :: account level returned none :: retrying at campaign level",
            lo, "->", win[-1])
        for acct in accts:
            maps = CCM[acct]
            try:
                p = {"level": "campaign", "time_increment": 1, "access_token": tok,
                     "time_range": json.dumps({"since": lo, "until": win[-1]}),
                     "fields": "action_values,actions", "limit": 500,
                     "action_attribution_windows": json.dumps(["7d_click", "1d_click", "1d_view"])}
                d = http_json("%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p)))
                for row in (d.get("data") or []):
                    day = row.get("date_start")
                    if day in ad["mspend"]:
                        _cc_harvest(day, row.get("action_values") or [], row.get("actions"), maps, ad)
            except Exception as e:
                log("meta custom conversions :: campaign-level retry failed", acct, str(e)[:140])
        log("meta custom conversions :: after campaign-level retry :: branches", len(MBR))
    if CCSEEN:
        top = sorted(CCSEEN.items(), key=lambda kv: -kv[1])[:14]
        log("meta custom conversions SEEN ::",
            " | ".join("%s=%s (%d)" % (CCNAME.get(c, c), c, round(v)) for c, v in top))
    else:
        log("meta custom conversions SEEN :: NONE -- Meta returned no "
            "offsite_conversion.custom.* action at any level for", len(accts), "account(s)")
    bnc_val = sum(sum(e.get("v", 0.0) for e in ms.values()) for ms in MBR.values())
    bnc_cnt = sum(sum(e.get("nc", 0.0) for e in ms.values()) for ms in MBR.values())
    allnc = sum(ad["instoreNC"].values())
    XTRA["metaCC"] = {"listed": len(CCNAME), "seen": len(CCSEEN),
                      "branchVal": round(bnc_val), "branchNC": round(bnc_cnt),
                      "allNC": round(allnc), "branches": len(MBR),
                      "offlineVal": round(sum(ad["instoreMeta"].values()))}
    log("metaCC summary ::", json.dumps(XTRA["metaCC"]))
    return ad

_SHOPV = []
def _shopify_versions(host, tok):
    """v7.9: the sessions dataset only exists in recent ShopifyQL. A pinned
    SHOPIFY_API_VERSION secret that has aged out returns tableData with ZERO ROWS --
    no error, no parseError, just an empty table that silently flatlines the funnel.
    So ask the store which versions it supports and try the newest first."""
    if _SHOPV: return _SHOPV
    d = http_json("https://%s/admin/api/2025-07/graphql.json" % host,
                  {"query": "{publicApiVersions{handle supported}}"},
                  {"X-Shopify-Access-Token": tok})
    raw = (((d or {}).get("data") or {}).get("publicApiVersions") or [])
    vs = sorted([v.get("handle") for v in raw
                 if v.get("supported") and re.match(r"^\d{4}-\d{2}$", str(v.get("handle") or ""))],
                reverse=True)
    _SHOPV.extend(vs)
    log("shopify api versions supported:", ",".join(vs) if vs else "NONE DISCOVERED (falling back to the hardcoded ladder)")
    return _SHOPV

def shopify_ql(ql, tag="ql"):
    """v7.6: never fail silently. Tries the pinned API version first, then a fallback
    ladder -- a stale SHOPIFY_API_VERSION secret used to zero the whole funnel with one
    unexplained log line. Logs version, row count and column names for every attempt."""
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    if not store or not tok:
        log("shopifyql", tag, "SKIPPED - SHOPIFY_STORE/SHOPIFY_TOKEN missing from the run env")
        return None
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    envv = os.environ.get("SHOPIFY_API_VERSION", "").strip()
    vers, seen = [], set()
    for v in _shopify_versions(host, tok) + [envv, "2025-07", "2025-04", "2025-01", "2024-10"]:
        if v and v not in seen: seen.add(v); vers.append(v)
    # v7.1: shopifyqlQuery returns ShopifyqlQueryResponse (a plain object) -- the old
    # "... on TableResponse" union fragment was removed by Shopify. Read fields directly.
    q = 'query($ql:String!){ shopifyqlQuery(query:$ql){ parseErrors tableData{ columns{ name } rows } } }'
    last = ""
    for ver in vers:
        d = http_json("https://%s/admin/api/%s/graphql.json" % (host, ver),
                      {"query": q, "variables": {"ql": ql}}, {"X-Shopify-Access-Token": tok})
        sq = (((d or {}).get("data") or {}).get("shopifyqlQuery") or {})
        td = sq.get("tableData")
        if not td:
            last = str(sq.get("parseErrors") or (d or {}).get("errors") or d)[:200]
            log("shopifyql", tag, "v" + ver, "no tableData ::", last)
            continue
        cols = [c["name"] for c in td.get("columns", [])]
        rows = td.get("rows", []) or []
        log("shopifyql", tag, "v" + ver, "rows", len(rows), "cols", ",".join(cols))
        if not rows:
            last = "tableData returned 0 rows on v" + ver
            continue
        # v8.0: rows may arrive as positional arrays OR as objects keyed by column
        # name -- Shopify serves the object form on current API versions. Zipping cols
        # against an object yields its KEYS, which silently zeroed the whole funnel.
        shaped = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in rows]
        log("shopifyql", tag, "row shape", "object" if isinstance(rows[0], dict) else "array",
            ":: first ::", str(shaped[0])[:160])
        return shaped
    if "0 rows" in last:
        log("shopifyql fail:", tag, ":: every supported API version returned an EMPTY table. "
            "The query parsed fine, so this is not a syntax problem -- the app's access token "
            "is most likely missing the read_analytics/read_reports scope for this dataset. "
            "Last attempt ::", last)
    else:
        log("shopifyql fail:", tag, "::", last)
    return None

def pull_meta_reach(win, tok):
    """Monthly DEDUPLICATED reach per ad account, summed across accounts, + spend -> CPMR.
    time_increment=monthly makes Meta dedup people inside each month; months cannot be summed."""
    if not tok: return
    MR = {}
    for acct in meta_accounts(tok):
        d = http_json(GRAPH + "/" + acct + "/insights?" + urllib.parse.urlencode(
            {"level": "account", "fields": "reach,spend", "time_increment": "monthly",
             "time_range": json.dumps({"since": win[0], "until": win[-1]}), "limit": 100, "access_token": tok}))
        for row in (d.get("data") or []):
            m = str(row.get("date_start", ""))[:7]
            if not m: continue
            e = MR.setdefault(m, {"r": 0, "s": 0.0})
            e["r"] += int(float(row.get("reach") or 0)); e["s"] += float(row.get("spend") or 0)
    for m in MR: MR[m]["s"] = round(MR[m]["s"])
    if MR: XTRA["mreach"] = MR; log("meta reach months", len(MR))


def pull_shopify(win):
    out = {k: {d: 0.0 for d in win} for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]}
    rows = shopify_ql("FROM sessions SHOW sessions, sessions_with_cart_additions, "
                      "sessions_that_reached_checkout, sessions_that_completed_checkout "
                      "TIMESERIES day SINCE -%dd UNTIL today" % (len(win) + 1), "sessions")
    if not rows:
        rows = shopify_ql("FROM sessions SHOW sessions, sessions_with_cart_additions, "
                          "sessions_that_reached_checkout, sessions_that_completed_checkout "
                          "TIMESERIES day SINCE -90d UNTIL today", "sessions90")
    for r in (rows or []):
        day = str(r.get("day"))[:10]
        if day not in out["sessions"]: continue
        s = float(r.get("sessions") or 0); atc = float(r.get("sessions_with_cart_additions") or 0)
        chk = float(r.get("sessions_that_reached_checkout") or 0); comp = float(r.get("sessions_that_completed_checkout") or 0)
        out["sessions"][day] = s
        if s:
            out["atcRatio"][day] = round(atc / s * 100, 2)
            out["checkoutRatio"][day] = round(chk / s * 100, 2)
            out["cvr"][day] = round(comp / s * 100, 2)
    # v7.6: modern ShopifyQL has NO `orders` dataset and no `customer_type` dimension --
    # "FROM orders ... GROUP BY customer_type" was a hard parse error on every single run,
    # which is what produced the four parseErrors in the log. Counts come from `FROM sales`
    # (verified live against the store); build() then overwrites all four series with
    # customer-level ACTUALS from Odoo, which is the only source that can split revenue.
    nr = shopify_ql("FROM sales SHOW orders, total_sales, customers, returning_customers "
                    "TIMESERIES day SINCE -%dd UNTIL today" % (len(win) + 1), "custsplit")
    for r in (nr or []):
        day = str(r.get("day"))[:10]
        if day not in out["newcust"]: continue
        cu = float(r.get("customers") or 0); rc = float(r.get("returning_customers") or 0)
        ts = float(r.get("total_sales") or 0)
        out["retcust"][day] = rc; out["newcust"][day] = max(0.0, cu - rc)
        if cu:
            out["rcrev"][day] = round(ts * rc / cu, 2)
            out["ncrev"][day] = round(ts - ts * rc / cu, 2)
    SRCOK["shopify"] = bool(rows)
    log("shopify days", sum(1 for v in out["sessions"].values() if v),
        "| cust-split days", sum(1 for v in out["retcust"].values() if v))
    return out

CVR_DAYS = 14      # rolling window, matches the "CVR routing" report the team exports by hand
CVR_LIMIT = 2500   # landing pages kept, ordered by sessions desc

def pull_cvr_routing():
    """The "CVR routing" Shopify report, pulled instead of exported by hand.

    Same grain as the manual CSV (landing page path x type) but ShopifyQL hands back
    RAW COUNTS, not the rounded rates the CSV carries -- so orders are exact rather than
    sessions x conversion_rate, and nothing is lost to the CSV's weighted averaging.
    COMPARE TO previous_period gives the prior-window columns the routing verdicts use
    for trend. Rows are capped at CVR_LIMIT; the cap and the sessions it drops are
    reported so the dashboard can say so out loud instead of implying full coverage."""
    ql = ("FROM sessions SHOW sessions, sessions_with_cart_additions, "
          "sessions_that_reached_checkout, sessions_that_completed_checkout "
          "GROUP BY landing_page_path, landing_page_type "
          "SINCE -%dd UNTIL today COMPARE TO previous_period "
          "ORDER BY sessions DESC LIMIT %d" % (CVR_DAYS, CVR_LIMIT))
    rows = shopify_ql(ql, "cvrroute")
    if not rows:
        log("cvr routing :: no rows -- keeping whatever data.js already had")
        return
    TY = ["Homepage", "Product", "Collection", "Custom Page", "Blog Article",
          "Search", "Cart", "Checkout", "Other"]
    tix = {t: i for i, t in enumerate(TY)}
    out, tot = [], 0.0
    for r in rows:
        p = (r.get("landing_page_path") or "").strip()
        if not p:
            continue
        t = (r.get("landing_page_type") or "Other").strip() or "Other"
        f = lambda k: float(r.get(k) or 0)
        s = f("sessions")
        tot += s
        out.append([p, tix.get(t, len(TY) - 1), int(s), int(f("sessions_with_cart_additions")),
                    int(f("sessions_that_reached_checkout")), int(f("sessions_that_completed_checkout")),
                    int(f("comparison_sessions__previous_period")),
                    int(f("comparison_sessions_that_completed_checkout__previous_period"))])
    # total sessions across ALL landing pages, so the tab can state the coverage honestly
    allsess = 0.0
    tr = shopify_ql("FROM sessions SHOW sessions SINCE -%dd UNTIL today" % CVR_DAYS, "cvrtotal")
    for r in (tr or []):
        allsess += float(r.get("sessions") or 0)
    XTRA["cvr"] = {"days": CVR_DAYS, "types": TY, "rows": out, "kept": len(out),
                   "capped": len(out) >= CVR_LIMIT, "keptSess": int(tot),
                   "allSess": int(allsess or tot), "pulled": END.isoformat()}
    log("cvr routing pages", len(out), "sessions", int(tot),
        "of", int(allsess or tot), "capped" if len(out) >= CVR_LIMIT else "full")

GTOK = [None]
# v7.8: did the API actually answer? Lets us tell "the feed is broken" apart from
# "the account simply spent nothing" -- two very different problems that both look
# like a flat zero on a chart.
SRCOK = {}
def pull_google(win):
    out = {"gspend": {d: 0.0 for d in win}, "gecomrev": {d: 0.0 for d in win}, "gconv": {d: 0.0 for d in win},
           "gimp": {d: 0.0 for d in win}, "gclk": {d: 0.0 for d in win}}
    dev = os.environ.get("GOOGLE_DEVELOPER_TOKEN", ""); cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    if not (dev and cid): log("google skipped"); return out
    try:
        data = urllib.parse.urlencode({"client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "refresh_token": os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
            "grant_type": "refresh_token"}).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=60) as r: at = json.loads(r.read()).get("access_token")
    except Exception as e:
        log("google token", str(e)[:120]); return out
    GTOK[0] = at
    gql = ("SELECT segments.date, metrics.cost_micros, metrics.conversions_value, metrics.conversions, "
           "metrics.impressions, metrics.clicks FROM customer "
           "WHERE segments.date BETWEEN '%s' AND '%s'" % (win[0], win[-1]))
    hd = {"Authorization": "Bearer " + at, "developer-token": dev, "Content-Type": "application/json"}
    lc = os.environ.get("GOOGLE_LOGIN_CID", "")
    if lc: hd["login-customer-id"] = lc
    try:
        req = urllib.request.Request("https://googleads.googleapis.com/v21/customers/%s/googleAds:searchStream" % cid,
                                     data=json.dumps({"query": gql}).encode(), headers=hd)
        with urllib.request.urlopen(req, timeout=90) as r: batches = json.loads(r.read())
    except Exception as e:
        log("google query", str(e)[:160]); return out
    for b in (batches if isinstance(batches, list) else [batches]):
        for row in b.get("results", []):
            day = row.get("segments", {}).get("date")
            if day not in out["gspend"]: continue
            out["gspend"][day] += float(row.get("metrics", {}).get("costMicros", 0)) / 1e6
            out["gecomrev"][day] += float(row.get("metrics", {}).get("conversionsValue", 0))
            out["gconv"][day] += float(row.get("metrics", {}).get("conversions", 0))
            out["gimp"][day] += float(row.get("metrics", {}).get("impressions", 0))
            out["gclk"][day] += float(row.get("metrics", {}).get("clicks", 0))
    SRCOK["google"] = True
    log("google days", sum(1 for v in out["gspend"].values() if v))
    return out

def pull_tiktok(win):
    out = {"tspend": {d: 0.0 for d in win}, "ttValue": {d: 0.0 for d in win}, "tpur": {d: 0.0 for d in win},
           "ttOffValue": {d: 0.0 for d in win}, "ttOffPur": {d: 0.0 for d in win},
           "timp": {d: 0.0 for d in win}, "tclk": {d: 0.0 for d in win}}
    tok = os.environ.get("TIKTOK_TOKEN", "").strip(); adv = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
    if not (tok and adv): log("tiktok skipped"); return out
    p = {"advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_ADVERTISER",
         "dimensions": json.dumps(["stat_time_day"]),
         "metrics": json.dumps(["spend", "complete_payment", "offline_shopping_events",
                                "offline_shopping_events_value", "impressions", "clicks"]),
         "start_date": win[0], "end_date": win[-1], "page_size": 1000}
    d = http_json("https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/?" + urllib.parse.urlencode(p),
                  None, {"Access-Token": tok})
    for row in (((d or {}).get("data") or {}).get("list") or []):
        day = str(row.get("dimensions", {}).get("stat_time_day", ""))[:10]
        if day not in out["tspend"]: continue
        m = row.get("metrics", {})
        out["tspend"][day] = float(m.get("spend") or 0)
        out["ttOffValue"][day] += float(row.get("metrics", {}).get("offline_shopping_events_value") or 0)
        out["ttOffPur"][day] += float(row.get("metrics", {}).get("offline_shopping_events") or 0)
        out["ttValue"][day] = float(m.get("complete_payment") or 0)
        out["timp"][day] += float(m.get("impressions") or 0)
        out["tclk"][day] += float(m.get("clicks") or 0)
    log("tiktok days", sum(1 for v in out["tspend"].values() if v))
    try:
        TR = {}
        months = sorted({d[:7] for d in win})
        for mo in months:
            a = mo + "-01"
            nxt = (datetime.date.fromisoformat(a) + datetime.timedelta(days=35)).replace(day=1)
            b = min(END, nxt - datetime.timedelta(days=1)).isoformat()
            if a > END.isoformat(): continue
            p2 = {"advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_ADVERTISER",
                  "dimensions": json.dumps(["advertiser_id"]), "metrics": json.dumps(["reach", "spend"]),
                  "start_date": a, "end_date": b, "page_size": 10}
            d2 = http_json("https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/?" + urllib.parse.urlencode(p2),
                           None, {"Access-Token": tok})
            for row in (((d2 or {}).get("data") or {}).get("list") or []):
                m2 = row.get("metrics", {})
                TR[mo] = {"r": int(float(m2.get("reach") or 0)), "s": round(float(m2.get("spend") or 0))}
        if TR: XTRA["treach"] = TR; log("tiktok reach months", len(TR))
    except Exception as e:
        log("tiktok reach fail", str(e)[:120])
    return out


def pull_google_ads():
    """Google campaigns, last 30d, with real serving status. Same shape as the Meta ad rows
    so the Paid Insights tab can render all three platforms through one renderer."""
    out = []
    dev = os.environ.get("GOOGLE_DEVELOPER_TOKEN", ""); cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    at = GTOK[0]
    if not (dev and cid and at): log("google campaigns skipped"); return out
    c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=29)).isoformat()
    gql = ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, "
           "metrics.cost_micros, metrics.conversions_value, metrics.conversions, "
           "metrics.impressions, metrics.clicks FROM campaign "
           "WHERE segments.date BETWEEN '%s' AND '%s'" % (c0, c1))
    hd = {"Authorization": "Bearer " + at, "developer-token": dev, "Content-Type": "application/json"}
    lc = os.environ.get("GOOGLE_LOGIN_CID", "")
    if lc: hd["login-customer-id"] = lc
    try:
        req = urllib.request.Request("https://googleads.googleapis.com/v21/customers/%s/googleAds:searchStream" % cid,
                                     data=json.dumps({"query": gql}).encode(), headers=hd)
        with urllib.request.urlopen(req, timeout=90) as r: batches = json.loads(r.read())
    except Exception as e:
        log("google campaigns", str(e)[:160]); return out
    agg = {}
    for b in (batches if isinstance(batches, list) else [batches]):
        for row in b.get("results", []):
            c = row.get("campaign", {}); m = row.get("metrics", {})
            k = str(c.get("id"))
            e = agg.setdefault(k, {"id": k, "n": str(c.get("name") or "")[:80], "sp": 0.0, "pv": 0.0,
                                   "ov": 0.0, "ofv": 0, "pur": 0.0, "opur": 0, "imp": 0.0, "clk": 0.0,
                                   "st": str(c.get("status") or "UNKNOWN"),
                                   "cmp": str(c.get("advertisingChannelType") or ""), "pf": "google"})
            e["sp"] += float(m.get("costMicros", 0)) / 1e6
            e["pv"] += float(m.get("conversionsValue", 0))
            e["pur"] += float(m.get("conversions", 0))
            e["imp"] += float(m.get("impressions", 0))
            e["clk"] += float(m.get("clicks", 0))
    for e in agg.values():
        e["ov"] = e["pv"]
        for kk in ("sp", "pv", "ov"): e[kk] = round(e[kk])
        for kk in ("pur", "imp", "clk"): e[kk] = int(round(e[kk]))
    out = sorted(agg.values(), key=lambda a: -a["sp"])[:40]
    log("google campaigns", len(out))
    return out


def pull_tiktok_ads():
    """TikTok campaigns, last 30d, with operation status."""
    out = []
    tok = os.environ.get("TIKTOK_TOKEN", "").strip(); adv = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
    if not (tok and adv): log("tiktok campaigns skipped"); return out
    c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=29)).isoformat()
    p = {"advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_CAMPAIGN",
         "dimensions": json.dumps(["campaign_id"]),
         "metrics": json.dumps(["campaign_name", "spend", "complete_payment", "complete_payment_roas",
                                "impressions", "clicks", "offline_shopping_events",
                                "offline_shopping_events_value"]),
         "start_date": c0, "end_date": c1, "page_size": 1000}
    try:
        d = http_json("https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/?" + urllib.parse.urlencode(p),
                      None, {"Access-Token": tok})
    except Exception as e:
        log("tiktok campaigns", str(e)[:140]); return out
    rows = (((d or {}).get("data") or {}).get("list") or [])
    st = {}
    try:
        q = {"advertiser_id": adv, "page_size": 1000,
             "fields": json.dumps(["campaign_id", "campaign_name", "operation_status", "secondary_status"])}
        ds = http_json("https://business-api.tiktok.com/open_api/v1.3/campaign/get/?" + urllib.parse.urlencode(q),
                       None, {"Access-Token": tok})
        for c in (((ds or {}).get("data") or {}).get("list") or []):
            st[str(c.get("campaign_id"))] = str(c.get("secondary_status") or c.get("operation_status") or "UNKNOWN")
    except Exception as e:
        log("tiktok campaign status", str(e)[:120])
    for r in rows:
        cid = str((r.get("dimensions") or {}).get("campaign_id") or "")
        m = r.get("metrics") or {}
        sp = float(m.get("spend") or 0)
        if sp <= 0: continue
        out.append({"id": cid, "n": str(m.get("campaign_name") or "")[:80], "sp": round(sp),
                    "pv": round(float(m.get("complete_payment") or 0)),
                    "ov": round(float(m.get("complete_payment") or 0) + float(m.get("offline_shopping_events_value") or 0)),
                    "ofv": round(float(m.get("offline_shopping_events_value") or 0)),
                    "pur": 0, "opur": int(float(m.get("offline_shopping_events") or 0)),
                    "imp": int(float(m.get("impressions") or 0)),
                    "clk": int(float(m.get("clicks") or 0)),
                    "st": st.get(cid, "UNKNOWN"), "cmp": "", "pf": "tiktok"})
    out.sort(key=lambda a: -a["sp"]); out = out[:40]
    log("tiktok campaigns", len(out))
    return out


def pull_shop_channel(fin_win):
    """Shopify-team daily actuals: revenue, margin, orders, refunds (credit notes are team-tagged)."""
    out = {"srev": {}, "sgp": {}, "sord": {}, "sref": {}}
    try:
        g = oexec("sale.order", "read_group",
                  [[["state", "in", ["sale", "done"]], ["team_id.name", "=", "Shopify"], ["date_order", ">=", fin_win[0]]],
                   ["amount_total", "margin"], ["date_order:day"]], {"lazy": False})
        for r in g:
            d = _gday(r.get("date_order:day"))
            if d: out["srev"][d] = round(r["amount_total"]); out["sgp"][d] = round(r["margin"]); out["sord"][d] = r["__count"]
        g = oexec("account.move", "read_group",
                  [[["move_type", "=", "out_refund"], ["state", "=", "posted"], ["team_id.name", "=", "Shopify"], ["invoice_date", ">=", fin_win[0]]],
                   ["amount_total"], ["invoice_date:day"]], {"lazy": False})
        for r in g:
            d = _gday(r.get("invoice_date:day"))
            if d: out["sref"][d] = round(r["amount_total"])
        log("shop-channel days", len(out["srev"]))
    except Exception as e:
        log("shop-channel fail", str(e)[:150])
    return out

def _gday(lbl):
    """Odoo read_group day label '05 Aug 2024' -> ISO."""
    try:
        return datetime.datetime.strptime(str(lbl), "%d %b %Y").date().isoformat()
    except Exception:
        try: return datetime.date.fromisoformat(str(lbl)[:10]).isoformat()
        except Exception: return None

def pull_cohorts():
    """REAL customer-level cohorts: Shopify-team orders, first-order month, cumulative GP at 30/90/180/365d."""
    try:
        orders = []; off = 0
        while True:
            page = oexec("sale.order", "search_read",
                         [[["state", "in", ["sale", "done"]], ["team_id.name", "=", "Shopify"], ["date_order", ">=", "2023-06-01"]]],
                         {"fields": ["partner_id", "date_order", "amount_total", "margin"], "limit": 10000, "offset": off, "order": "id"})
            if not page: break
            for o in page:
                orders.append((o["partner_id"][0] if o.get("partner_id") else 0, o["date_order"][:10],
                               float(o.get("amount_total") or 0), float(o.get("margin") or 0)))
            off += len(page)
            if len(page) < 10000: break
        first = {}
        for pid, d, amt, mg in sorted(orders, key=lambda o: o[1]):
            if pid and pid not in first: first[pid] = d
        by_p = {}
        for pid, d, amt, mg in orders:
            if pid: by_p.setdefault(pid, []).append((d, amt, mg))
        coh = {}
        for pid, f in first.items():
            m = f[:7]
            if m < "2024-08": continue
            c = coh.setdefault(m, {"size": 0, "g30": 0.0, "g90": 0.0, "g180": 0.0, "g365": 0.0, "r90": 0.0, "r365": 0.0})
            c["size"] += 1
            f0 = datetime.date.fromisoformat(f)
            for d, amt, mg in by_p[pid]:
                dd = (datetime.date.fromisoformat(d) - f0).days
                if dd < 0: continue
                if dd <= 30: c["g30"] += mg
                if dd <= 90: c["g90"] += mg; c["r90"] += amt
                if dd <= 180: c["g180"] += mg
                if dd <= 365: c["g365"] += mg; c["r365"] += amt
        out = [{"m": m, "size": c["size"], "g30": round(c["g30"]), "g90": round(c["g90"]),
                "g180": round(c["g180"]), "g365": round(c["g365"]),
                "r90": round(c["r90"]), "r365": round(c["r365"])} for m, c in sorted(coh.items())]
        lagH = [0] * 15
        for pid, lst in by_p.items():
            dl = sorted({d for d, _a, _m in lst})
            for i in range(1, len(dl)):
                gap = (datetime.date.fromisoformat(dl[i]) - datetime.date.fromisoformat(dl[i - 1])).days
                lagH[min(gap // 10, 14)] += 1
        XTRA.setdefault("lag", {})["shop"] = lagH
        # v8.8 ONLINE cohort cube (each orders row IS one order)
        try:
            PMO2 = {}
            for pid2, lst in by_p.items():
                for d2, amt2, mg2 in lst:
                    e2 = PMO2.setdefault(pid2, {}).setdefault(d2[:7], [0, 0.0, 0.0])
                    e2[0] += 1; e2[1] += amt2; e2[2] += mg2
            _mo = lambda a3, b3: (int(b3[:4]) - int(a3[:4])) * 12 + int(b3[5:7]) - int(a3[5:7])
            CURM2 = END.isoformat()[:7]
            C2 = {}
            for pid2, f2 in first.items():
                cm = f2[:7]
                cc = C2.setdefault(cm, {"n": 0, "r": 0, "m": {}})
                cc["n"] += 1
                ret2 = False
                for mo2, e2 in PMO2.get(pid2, {}).items():
                    k = _mo(cm, mo2)
                    if k < 0 or k > 17: continue
                    a3 = cc["m"].setdefault(k, [0, 0.0, 0.0])
                    if (k > 0 and e2[0] >= 1) or (k == 0 and e2[0] >= 2): a3[0] += 1; ret2 = True
                    a3[1] += e2[1]; a3[2] += e2[2]
                if ret2: cc["r"] += 1
            onl = {}
            for cm, cc in C2.items():
                mx = _mo(cm, CURM2)
                if mx < 0: continue
                onl[cm] = {"n": cc["n"], "r": cc["r"],
                           "m": [[cc["m"].get(k, [0, 0, 0])[0],
                                  round(cc["m"].get(k, [0, 0, 0])[1]),
                                  round(cc["m"].get(k, [0, 0, 0])[2])]
                                 for k in range(0, min(17, mx) + 1)]}
            XTRA.setdefault("cube", {"scopes": {}, "ven": {}, "cat": {}})["scopes"]["ONLINE"] = onl
            log("online cohort cube :: cohorts", len(onl))
        except Exception as e:
            log("online cube fail", str(e)[:120])
        # v8.3 ONLINE journey profile (categories need order lines; POS journeys carry the mix)
        d2o = []; hist = [0] * 13; opy = [0] * 6; toto = 0; yrs = 0.0; ltg2 = 0.0; ltv2 = 0.0
        for pid, f in first.items():
            ds = sorted({d for d, _a, _m in by_p[pid]})
            if len(ds) >= 2:
                g = (datetime.date.fromisoformat(ds[1]) - datetime.date.fromisoformat(ds[0])).days
                d2o.append(g); hist[min(g // 30, 12)] += 1
            span = max(30, (END - datetime.date.fromisoformat(f)).days) / 365.0
            c = len(ds); toto += c; yrs += span; opy[min(max(c, 1), 6) - 1] += 1
            ltv2 += sum(a2 for _d, a2, _m in by_p[pid]); ltg2 += sum(m2 for _d, _a, m2 in by_p[pid])
        if first:
            n = len(first)
            XTRA.setdefault("jour", {}).setdefault("scopes", {})["ONLINE"] = {
                "n": n, "rep": round(len(d2o) / n * 100, 1),
                "med2": int(statistics.median(d2o)) if d2o else None,
                "oyr": round(toto / yrs, 2) if yrs else 0, "ltgp": round(ltg2 / n), "ltv": round(ltv2 / n),
                "h2": hist, "opy": opy, "cat": [], "ven": []}
        nr = {}
        seen = set()
        for pid, d, amt, mg in sorted(orders, key=lambda o: (o[1], o[0])):
            m = d[:7]
            if m < "2024-08": continue
            c = nr.setdefault(m, {"nrev": 0.0, "ngp": 0.0, "nord": 0, "rrev": 0.0, "rgp": 0.0, "rord": 0})
            if pid and pid not in seen and first.get(pid) == d:
                seen.add(pid); c["nrev"] += amt; c["ngp"] += mg; c["nord"] += 1
            else:
                c["rrev"] += amt; c["rgp"] += mg; c["rord"] += 1
        for m in nr: nr[m] = {k: (round(v) if isinstance(v, float) else v) for k, v in nr[m].items()}
        # v7.6: same split at DAY grain -- this is what feeds New vs Returning on the
        # dashboard. Actual first-order date per customer, not a ShopifyQL allocation.
        nrd = {}; seen2 = set()
        for pid, d, amt, mg in sorted(orders, key=lambda o: (o[1], o[0])):
            c = nrd.setdefault(d, {"nrev": 0.0, "nord": 0, "rrev": 0.0, "rord": 0})
            if pid and pid not in seen2 and first.get(pid) == d:
                seen2.add(pid); c["nrev"] += amt; c["nord"] += 1
            else:
                c["rrev"] += amt; c["rord"] += 1
        log("cohorts", len(out), "customers", len(first), "nr months", len(nr), "nr days", len(nrd))
        return {"coh": out, "nr": nr, "nrd": nrd}
    except Exception as e:
        log("cohorts fail", str(e)[:150]); return {"coh": [], "nr": {}, "nrd": {}}

EXPG = [("Payroll & benefits", ("31.01.01.", "31.01.09.")), ("Rent — branches", ("31.01.04.02",)),
        ("Rent — HQ / warehouses / flats", ("31.01.04.01", "31.01.04.03", "31.01.04.04")),
        ("Utilities (elec/water/gas)", ("31.01.08.01", "31.01.08.02", "31.01.08.03")),
        ("Packing & bags", ("31.01.08.10",)), ("Payment & collection fees", ("31.01.08.17", "31.01.08.31", "31.01.08.32", "31.01.08.33", "31.01.08.34", "31.01.08.35", "31.01.08.36", "31.01.08.37")),
        ("Cargo & transport", ("31.01.06.", "31.01.08.08", "31.01.08.09")),
        ("Offline advertising & gifts", ("31.01.05.",)), ("Maintenance", ("31.01.02.",)),
        ("Comms & internet", ("31.01.03.",))]

BRANCH_AR = [("\u0645\u0648\u0644 \u0627\u0644\u0639\u0631\u0628", "Mall of Arabia"), ("\u0627\u0644\u0639\u0631\u0628", "Mall of Arabia"),
             ("\u0627\u0644\u062a\u062c\u0645\u0639", "New Cairo"), ("\u0646\u0635\u0631", "Nasr City"), ("\u062f\u0642", "Dokki"),
             ("\u0643\u062a\u0648\u0628\u0631", "October"), ("\u0632\u0627\u064a\u062f", "Zayed"), ("\u0633\u0645\u0648\u062d", "Smouha")]

def pull_expenses():
    """Monthly OpEx from the real 31.* expense accounts + per-branch rent parsed from line descriptions."""
    exp = {}; rentB = {}
    try:
        acc = oexec("account.account", "search_read", [[["code", "=like", "31.%"]]], {"fields": ["code", "name"], "limit": 300})
        amap = {a["id"]: a["code"] for a in acc}
        g = oexec("account.move.line", "read_group",
                  [[["account_id", "in", list(amap.keys())], ["parent_state", "=", "posted"], ["date", ">=", "2024-08-01"], ["date", "<=", END.isoformat()]],
                   ["balance"], ["account_id", "date:month"]], {"lazy": False})
        for r in g:
            code = amap.get(r["account_id"][0], "")
            try: mon = datetime.datetime.strptime(str(r["date:month"]), "%B %Y").strftime("%Y-%m")
            except Exception: continue
            grp = "Other OpEx"
            for name, prefixes in EXPG:
                if any(code.startswith(px) for px in prefixes): grp = name; break
            exp.setdefault(mon, {})[grp] = exp.setdefault(mon, {}).get(grp, 0) + round(r["balance"])
        rb = [a["id"] for a in acc if "RENT Branches" in a["name"]]
        lines = oexec("account.move.line", "search_read",
                      [[["account_id", "in", rb], ["parent_state", "=", "posted"], ["date", ">=", "2025-01-01"], ["date", "<=", END.isoformat()]]],
                      {"fields": ["name", "balance", "date"], "limit": 2000})
        for l in lines:
            nm = str(l.get("name") or ""); mon = l["date"][:7]; b = "(unlabelled)"
            for k, v in BRANCH_AR:
                if k in nm: b = v; break
            rentB.setdefault(b, {})[mon] = rentB.setdefault(b, {}).get(mon, 0) + round(l["balance"])
        log("expenses months", len(exp), "rent branches", len(rentB))
    except Exception as e:
        log("expenses fail", str(e)[:150])
    return exp, rentB


POS_CFG = [("Dokki", "Dokki"), ("New  Cairo", "New Cairo"), ("New Cairo", "New Cairo"), ("October", "October"),
           ("Zayed", "Zayed"), ("Nasr City", "Nasr City"), ("Smouha", "Smouha"), ("Mall OF Arabia", "Mall of Arabia"), ("Mall of Arabia", "Mall of Arabia")]
ANA_BR = {"18": "Dokki", "19": "New Cairo", "20": "Nasr City", "21": "October", "22": "Zayed", "38": "Smouha", "39": "Mall of Arabia",
          "41": "Dokki", "42": "Nasr City", "43": "New Cairo", "44": "October", "46": "Zayed"}

def pull_pos_branches():
    """REAL per-branch monthly revenue + margin + orders from report.pos.order (readable POS reporting view)."""
    out = {}
    try:
        g = oexec("report.pos.order", "read_group",
                  [[["date", ">=", "2024-08-01"]], ["price_total", "margin", "order_id:count_distinct"], ["config_id", "date:month"]], {"lazy": False})
        for r in g:
            cfg = (r.get("config_id") or [0, ""])[1]
            br = next((b for k, b in POS_CFG if k in cfg), None)
            if not br: continue
            try: mon = datetime.datetime.strptime(str(r["date:month"]), "%B %Y").strftime("%Y-%m")
            except Exception: continue
            c = out.setdefault(br, {}).setdefault(mon, [0, 0, 0])
            c[0] += round(r["price_total"]); c[1] += round(r["margin"]); c[2] += int(r.get("order_id") or r["__count"])
        log("pos branches", len(out), "months", len(next(iter(out.values()), {})))
    except Exception as e:
        log("pos branches fail", str(e)[:150])
    return out

RENT_DX = {}

def pull_branch_costs():
    """ACTUAL per-branch monthly rent + salaries from expense lines' analytic distribution."""
    out = {}
    RENT_DX.clear()
    try:
        acc = oexec("account.account", "search_read", [["|", ["code", "=like", "31.01.04.02%"], ["code", "=like", "31.01.01.%"]]],
                    {"fields": ["code"], "limit": 40})
        rent_ids = [a["id"] for a in acc if a["code"].startswith("31.01.04")]
        sal_ids = [a["id"] for a in acc if a["code"].startswith("31.01.01")]
        lines = oexec("account.move.line", "search_read",
                      [[["account_id", "in", rent_ids + sal_ids], ["parent_state", "=", "posted"], ["date", ">=", "2024-08-01"]]],
                      {"fields": ["account_id", "balance", "date", "analytic_distribution"], "limit": 20000})
        endi = END.isoformat(); fwd = {}
        for l in lines:
            ad2 = l.get("analytic_distribution") or {}
            mon = l["date"][:7]; kind = "rent" if l["account_id"][0] in rent_ids else "sal"
            future = l["date"] > endi
            for aid, pct in ad2.items():
                br = ANA_BR.get(str(aid))
                if not br: continue
                v = l["balance"] * (float(pct) / 100.0)
                if future:
                    if kind == "rent": fwd[br] = fwd.get(br, 0) + v
                    continue
                c = out.setdefault(br, {}).setdefault(mon, {"rent": 0, "sal": 0})
                c[kind] += v
        for br in out:
            for mon in out[br]:
                out[br][mon] = {k: round(v) for k, v in out[br][mon].items()}
        for br in set(list(out.keys()) + list(fwd.keys()) + list(ANA_BR.values())):
            mv = out.get(br, {})
            rm = sorted(m for m in mv if mv[m].get("rent", 0) > 0)
            last6 = [mv[m]["rent"] for m in sorted(mv)[-6:] if mv.get(m, {}).get("rent", 0) > 0]
            RENT_DX[br] = {"mos": len(rm), "all": len(mv), "last": rm[-1] if rm else None,
                           "av6": round(sum(last6) / len(last6)) if last6 else 0,
                           "tot": round(sum(mv[m]["rent"] for m in rm)),
                           "fwd": round(fwd.get(br, 0))}
        log("branch costs", {b: len(v) for b, v in out.items()})
        log("rent coverage", {b: (d["mos"], d["av6"]) for b, d in sorted(RENT_DX.items())})
    except Exception as e:
        log("branch costs fail", str(e)[:150])
    return out


BR_TOKENS = ("dokki", "new cairo", "newcairo", "october", "zayed", "nasr", "smouha", "arabia", "al ahli", "alahli")

def anon_partner_ids():
    """POS partners that are NOT a real registered customer: the generic walk-in partner and the
    per-branch house accounts cashiers use when the shopper gives no phone number.
    Resolved by name every run so a new branch house account is caught automatically."""
    ids = {}
    try:
        ps = oexec("res.partner", "search_read", [["|", ["name", "ilike", "pos customer"], ["name", "ilike", "ourkids"]]],
                   {"fields": ["name"], "limit": 200})
        for p in ps:
            nm = (p.get("name") or "").strip().lower()
            if "pos customer" in nm:
                ids[p["id"]] = p["name"]; continue
            if nm.startswith("ourkids") or nm.startswith("our kids"):
                tail = nm.replace("our kids", "").replace("ourkids", "").strip()
                if any(t in tail for t in BR_TOKENS): ids[p["id"]] = p["name"]
        log("anon partners", len(ids), list(ids.values())[:12])
    except Exception as e:
        log("anon partners fail", str(e)[:140])
    return ids


def pull_pos_customers():
    """Branch customer economics from report.pos.order: new vs returning per branch-month, repeat rate, LTGP.
    Walk-in / house-account receipts are excluded from every customer number and counted separately in bun."""
    bnr = {}; bstat = {}; bun = {}
    ANON = anon_partner_ids()
    TV = tmpl_vendor_map(); VNM = vendor_names()
    VCS = {}; PFD = {}; PFV = {}; PATTR = {}; JR = []
    try:
        rows = []
        q = datetime.date(2024, 8, 1)
        while q <= END:
            q2 = (q.replace(day=1) + datetime.timedelta(days=95)).replace(day=1)
            off = 0
            while off < 400000:
                page = oexec("report.pos.order", "search_read",
                             [[["partner_id", "!=", False], ["date", ">=", q.isoformat()], ["date", "<", q2.isoformat()]]],
                             {"fields": ["partner_id", "config_id", "date", "margin", "order_id", "product_tmpl_id", "price_total", "product_qty"],
                              "limit": 10000, "offset": off, "order": "id"})
                if not page: break
                for r in page:
                    cfg = (r.get("config_id") or [0, ""])[1]
                    br = next((b for k, b in POS_CFG if k in cfg), None)
                    if not br: continue
                    pid = r["partner_id"][0]
                    # v6.2 BUGFIX: oid used to be assigned only inside the ANON branch, so every
                    # registered-customer row reused the PREVIOUS anonymous row's order id -- and the
                    # very first row of the crawl raised NameError, which the outer except swallowed.
                    # That is why bun (unregistered receipts) never once reached data.js.
                    oid = (r.get("order_id") or [0])[0]
                    rv = float(r.get("price_total") or 0); qy = float(r.get("product_qty") or 0)
                    _tid = (r.get("product_tmpl_id") or [0])[0]
                    _tv = TV.get(_tid, ("", "", ""))
                    _vn = _tv[0]
                    _m7 = r["date"][:7]
                    if _vn:
                        _vm = XTRA.setdefault("vmon", {}).setdefault(_vn, {}).setdefault(_m7, [0.0, 0.0])
                        _vm[0] += rv; _vm[1] += float(r.get("margin") or 0)
                    if _tid:
                        _pm = XTRA.setdefault("pmon", {}).setdefault(_tid, {}).setdefault(_m7, [0.0, 0.0])
                        _pm[0] += rv; _pm[1] += float(r.get("margin") or 0)
                    if pid in ANON:
                        bun.setdefault(br, {}).setdefault(_m7, set()).add(oid)
                        _bv = XTRA.setdefault("bunr", {}).setdefault(br, {})
                        _bv[_m7] = _bv.get(_m7, 0.0) + rv
                        continue
                    d10 = r["date"][:10]
                    rows.append((pid, d10, br, float(r.get("margin") or 0), oid, rv, qy))
                    if rv > 0:
                        _at = PATTR.setdefault(pid, {"cat": {}, "ven": {}})
                        _cg = _catname(_tv[1])
                        if _cg:
                            _at["cat"][_cg] = _at["cat"].get(_cg, 0.0) + rv
                            JR.append((pid, d10, _cg, rv))
                        if _vn:
                            _vl = VNM.get(_vn) or _vn
                            _at["ven"][_vl] = _at["ven"].get(_vl, 0.0) + rv
                    if _vn:
                        VCS.setdefault(_vn, set()).add(pid)
                        _p0 = PFD.get(pid)
                        if _p0 is None or d10 < _p0: PFD[pid] = d10; PFV[pid] = set([_vn])
                        elif d10 == _p0: PFV[pid].add(_vn)
                off += len(page)
                if len(page) < 10000: break
            q = q2
        rows.sort(key=lambda x: (x[1], x[0]))
        first = {}; cnt = {}; ltg = {}; seenOrd = set()
        for pid, d, br, mg, oid, rv, qy in rows:
            if pid not in first: first[pid] = (d, br)
            if oid and (pid, oid) not in seenOrd:
                seenOrd.add((pid, oid)); cnt[pid] = cnt.get(pid, 0) + 1
            ltg[pid] = ltg.get(pid, 0.0) + mg
        ordNet = {}
        for pid, d, br, mg, oid, rv, qy in rows:
            if oid: ordNet[(pid, oid)] = ordNet.get((pid, oid), 0.0) + mg
        newSeen = set(); ordSeen = set()
        for pid, d, br, mg, oid, rv, qy in rows:
            m = d[:7]
            c = bnr.setdefault(br, {}).setdefault(m, {"nc": 0, "ng": 0, "rc": 0, "rg": 0})
            isNew = pid not in newSeen and first[pid][0] == d and first[pid][1] == br
            if isNew: newSeen.add(pid); c["nc"] += 1
            newOrd = oid and (pid, oid, "b") not in ordSeen
            if newOrd: ordSeen.add((pid, oid, "b"))
            # v6.5: revenue and order counts split by FIRST-DAY (a customer's whole first-day basket
            # is new-customer money, not just its first line -- ng/rg kept as-is for continuity)
            isFD = first[pid][0] == d and first[pid][1] == br
            if isFD: c["nrev"] = c.get("nrev", 0) + round(rv)
            else: c["rrev"] = c.get("rrev", 0) + round(rv)
            if newOrd:
                if isFD: c["nord"] = c.get("nord", 0) + 1
                else: c["rord"] = c.get("rord", 0) + 1
            if isNew: c["ng"] += round(mg)
            else:
                if newOrd:
                    c["rc"] += 1
                    if ordNet.get((pid, oid), 0) > 0: c["rcx"] = c.get("rcx", 0) + 1
                c["rg"] += round(mg)
        # branch cohorts: LTGP per customer acquired at each branch, by cohort month
        pm_ = {}
        for pid, d, br, mg, oid, rv, qy in rows: pm_.setdefault(pid, []).append((d, mg, rv))
        bcoh = {}
        for pid, (fd, fb) in first.items():
            m = fd[:7]
            c2 = bcoh.setdefault(fb, {}).setdefault(m, {"size": 0, "g30": 0.0, "g90": 0.0, "g180": 0.0, "g365": 0.0, "r90": 0.0, "r365": 0.0})
            c2["size"] += 1
            f0 = datetime.date.fromisoformat(fd)
            for d, mg, rv in pm_[pid]:
                dd = (datetime.date.fromisoformat(d) - f0).days
                if dd < 0: continue
                if dd <= 30: c2["g30"] += mg
                if dd <= 90: c2["g90"] += mg; c2["r90"] += rv
                if dd <= 180: c2["g180"] += mg
                if dd <= 365: c2["g365"] += mg; c2["r365"] += rv
        for b2 in bcoh:
            for m in bcoh[b2]: bcoh[b2][m] = {k: (round(v) if isinstance(v, float) else v) for k, v in bcoh[b2][m].items()}
        cntX = {}
        for (pid, oid), net in ordNet.items():
            if net > 0: cntX[pid] = cntX.get(pid, 0) + 1
        fb = {}
        for pid, (d, br) in first.items(): fb.setdefault(br, []).append(pid)
        for br, pids in fb.items():
            rep2 = sum(1 for p2 in pids if cnt.get(p2, 0) >= 2)
            rep2x = sum(1 for p2 in pids if cntX.get(p2, 0) >= 2)
            bstat[br] = {"cust": len(pids), "repeatRate": round(rep2 / len(pids) * 100, 1) if pids else 0,
                         "repeatRateX": round(rep2x / len(pids) * 100, 1) if pids else 0,
                         "ordPerCust": round(sum(cnt.get(p2, 0) for p2 in pids) / len(pids), 2) if pids else 0,
                         "ltgp": round(sum(ltg.get(p2, 0) for p2 in pids) / len(pids)) if pids else 0}
        VACQ = {}
        for pid, vs in PFV.items():
            for v in vs: VACQ.setdefault(v, set()).add(pid)
        VC.clear()
        for v, pids in VCS.items():
            n = len(pids)
            if n < 25: continue
            VC[v] = {"cust": n,
                     "acq": len(VACQ.get(v, ())),
                     "ltgp": round(sum(ltg.get(p2, 0) for p2 in pids) / n),
                     "rep": round(sum(1 for p2 in pids if cntX.get(p2, 0) >= 2) / n * 100, 1),
                     "opc": round(sum(cnt.get(p2, 0) for p2 in pids) / n, 2)}
        allp = list(first.keys())
        if allp:
            VC["__net__"] = {"cust": len(allp),
                             "acq": len(allp),
                             "ltgp": round(sum(ltg.values()) / len(allp)),
                             "rep": round(sum(1 for p2 in allp if cntX.get(p2, 0) >= 2) / len(allp) * 100, 1),
                             "opc": round(sum(cnt.values()) / len(allp), 2)}
        log("vendor customer economics", len(VC) - 1, "vendors >=25 customers")
        # ---- v6.5 customer deciles, per branch and network, plus inter-order lag ----
        DA = {}; DOC = {}; DSEEN = {}
        for pid, d, br, mg, oid, rv, qy in rows:
            for sc in (br, "ALL STORES"):
                a = DA.setdefault(sc, {}).get(pid)
                if a is None: a = DA[sc][pid] = [0.0, 0.0, 0.0, 0.0, d, d, 0.0, 0.0]
                a[0] += rv; a[1] += mg; a[2] += qy
                if rv < 0: a[3] += -rv
                if d < a[4]: a[4] = d
                if d > a[5]: a[5] = d
                if oid and (pid, oid) not in DSEEN.setdefault(sc, set()):
                    DSEEN[sc].add((pid, oid))
                    _dsc = DOC.setdefault(sc, {})
                    _dsc[pid] = _dsc.get(pid, 0) + 1
        XTRA["dec"] = {sc: _decile(DA[sc], DOC.get(sc, {}), PATTR) for sc in DA}
        XTRA["posagg"] = {p: (a[0], a[1]) for p, a in DA.get("ALL STORES", {}).items()}
        XTRA["vcs"] = VCS
        pdates = {}
        for pid, d, br, mg, oid, rv, qy in rows:
            if oid: pdates.setdefault(pid, set()).add(d)
        lagH = [0] * 15
        for pid, ds in pdates.items():
            dl = sorted(ds)
            for i in range(1, len(dl)):
                gap = (datetime.date.fromisoformat(dl[i]) - datetime.date.fromisoformat(dl[i - 1])).days
                lagH[min(gap // 10, 14)] += 1
        XTRA.setdefault("lag", {})["pos"] = lagH
        # ---- v8.3 customer journeys: what a first purchase turns into ----
        d2 = {}
        for pid, ds in pdates.items():
            dl = sorted(ds)
            if len(dl) >= 2:
                d2[pid] = (datetime.date.fromisoformat(dl[1]) - datetime.date.fromisoformat(dl[0])).days
        lrev = {}
        for pid, d, br, mg, oid, rv, qy in rows: lrev[pid] = lrev.get(pid, 0.0) + rv
        def _jprof(pids):
            n = len(pids)
            if not n: return None
            reps = [d2[p] for p in pids if p in d2]
            hist = [0] * 13
            for g in reps: hist[min(g // 30, 12)] += 1
            opy = [0] * 6
            yrs = 0.0; tot_o = 0
            for p in pids:
                span = max(30, (END - datetime.date.fromisoformat(first[p][0])).days) / 365.0
                c = cnt.get(p, 0); tot_o += c; yrs += span
                opy[min(max(c, 1), 6) - 1] += 1
            return {"n": n, "rep": round(len(reps) / n * 100, 1),
                    "med2": int(statistics.median(reps)) if reps else None,
                    "oyr": round(tot_o / yrs, 2) if yrs else 0,
                    "ltgp": round(sum(ltg.get(p, 0) for p in pids) / n),
                    "ltv": round(sum(lrev.get(p, 0) for p in pids) / n),
                    "h2": hist, "opy": opy}
        def _jmix(pids, key, topn=8):
            agg = {}
            for p in pids:
                for k2, v2 in PATTR.get(p, {}).get(key, {}).items(): agg[k2] = agg.get(k2, 0.0) + v2
            return [[k2, round(v2)] for k2, v2 in sorted(agg.items(), key=lambda kv: -kv[1])[:topn]]
        JOUR = {"scopes": {}, "cat": {}, "ven": {}}
        for br2, pids in fb.items():
            pr = _jprof(pids)
            if pr: pr["cat"] = _jmix(pids, "cat"); pr["ven"] = _jmix(pids, "ven"); JOUR["scopes"][br2] = pr
        allpids = list(first.keys())
        pr = _jprof(allpids)
        if pr: pr["cat"] = _jmix(allpids, "cat"); pr["ven"] = _jmix(allpids, "ven"); JOUR["scopes"]["ALL STORES"] = pr
        # first-purchase category -> the rest of the journey
        fcat = {}; nxt = {}
        for pid, d, cg, rv in JR:
            f0 = first.get(pid)
            if not f0: continue
            if d == f0[0]:
                e = fcat.setdefault(pid, {}); e[cg] = e.get(cg, 0.0) + rv
            else:
                nx = nxt.setdefault(pid, {}); nx[cg] = nx.get(cg, 0.0) + rv
        bycat = {}
        for pid, cs in fcat.items(): bycat.setdefault(max(cs, key=cs.get), []).append(pid)
        for cg, pids in bycat.items():
            if len(pids) < 40: continue
            pr = _jprof(pids)
            if not pr: continue
            nagg = {}
            for p in pids:
                for k2, v2 in nxt.get(p, {}).items(): nagg[k2] = nagg.get(k2, 0.0) + v2
            pr["next"] = [[k2, round(v2)] for k2, v2 in sorted(nagg.items(), key=lambda kv: -kv[1])[:6]]
            JOUR["cat"][cg] = pr
        # first-purchase vendor -> journey (top 15 by acquired customers)
        byven = {}
        for pid, vs in PFV.items():
            for v in vs: byven.setdefault(VNM.get(v) or v, []).append(pid)
        for vn2, pids in sorted(byven.items(), key=lambda kv: -len(kv[1]))[:40]:
            if len(pids) < 25: continue
            pr = _jprof(pids)
            if pr: pr["cat"] = _jmix(pids, "cat", 6); JOUR["ven"][vn2] = pr
        J0 = XTRA.setdefault("jour", {"scopes": {}, "cat": {}, "ven": {}})
        J0.setdefault("scopes", {}).update(JOUR["scopes"]); J0["cat"] = JOUR["cat"]; J0["ven"] = JOUR["ven"]
        log("journeys :: scopes", len(J0["scopes"]), ":: first-category segments", len(JOUR["cat"]),
            ":: first-vendor segments", len(JOUR["ven"]))
        # ---- v8.8 cohort CUBE: per cohort-month, month-offset grid [active, revenue, gp]
        # like Shopify's cohort explorer, but from the real Odoo history, per scope AND
        # sliceable by FIRST-purchase vendor/category.
        # v9.8: each month now carries TWO trios -- all orders, and the same figures with
        # returns/exchanges dropped (net margin <= 0, the rule the other tabs already use).
        # The Cohorts tab's "Exclude returns" switch reads the second trio.
        PMO = {}
        for pid, d, br, mg, oid, rv, qy in rows:
            e = PMO.setdefault(pid, {}).setdefault(d[:7], [set(), 0.0, 0.0, set(), 0.0, 0.0])
            if oid: e[0].add(oid)
            e[1] += rv; e[2] += mg
            if mg > 0:
                if oid: e[3].add(oid)
                e[4] += rv; e[5] += mg
        def _moff(a2, b2): return (int(b2[:4]) - int(a2[:4])) * 12 + int(b2[5:7]) - int(a2[5:7])
        CURM = END.isoformat()[:7]
        def _cubeb(pids):
            C = {}
            for p in pids:
                f = first.get(p)
                if not f: continue
                cm = f[0][:7]
                cc = C.setdefault(cm, {"n": 0, "r": 0, "rx": 0, "m": {}})
                cc["n"] += 1
                ret = False; retX = False
                for mo, e in PMO.get(p, {}).items():
                    k = _moff(cm, mo)
                    if k < 0 or k > 17: continue
                    a3 = cc["m"].setdefault(k, [0, 0.0, 0.0, 0, 0.0, 0.0])
                    nord = len(e[0]); nordX = len(e[3])
                    if (k > 0 and nord >= 1) or (k == 0 and nord >= 2): a3[0] += 1; ret = True
                    a3[1] += e[1]; a3[2] += e[2]
                    if (k > 0 and nordX >= 1) or (k == 0 and nordX >= 2): a3[3] += 1; retX = True
                    a3[4] += e[4]; a3[5] += e[5]
                if ret: cc["r"] += 1
                if retX: cc["rx"] += 1
            out = {}
            Z = [0, 0.0, 0.0, 0, 0.0, 0.0]
            for cm, cc in C.items():
                mx = _moff(cm, CURM)
                if mx < 0: continue
                out[cm] = {"n": cc["n"], "r": cc["r"], "rx": cc["rx"],
                           "m": [[cc["m"].get(k, Z)[0], round(cc["m"].get(k, Z)[1]), round(cc["m"].get(k, Z)[2]),
                                  cc["m"].get(k, Z)[3], round(cc["m"].get(k, Z)[4]), round(cc["m"].get(k, Z)[5])]
                                 for k in range(0, min(17, mx) + 1)]}
            return out
        CUBE = {"scopes": {}, "ven": {}, "cat": {}}
        for br2, pids in fb.items(): CUBE["scopes"][br2] = _cubeb(pids)
        CUBE["scopes"]["ALL STORES"] = _cubeb(allpids)
        for cg, pids in bycat.items():
            if len(pids) >= 100: CUBE["cat"][cg] = _cubeb(pids)
        for vn2, pids in sorted(byven.items(), key=lambda kv: -len(kv[1]))[:20]:
            if len(pids) >= 100: CUBE["ven"][vn2] = _cubeb(pids)
        X0 = XTRA.setdefault("cube", {"scopes": {}, "ven": {}, "cat": {}})
        X0["scopes"].update(CUBE["scopes"]); X0["ven"] = CUBE["ven"]; X0["cat"] = CUBE["cat"]
        log("cohort cube :: scopes", len(X0["scopes"]), ":: ven slices", len(CUBE["ven"]),
            ":: cat slices", len(CUBE["cat"]))
        log("deciles", {sc: len(v) for sc, v in XTRA["dec"].items()}, "lag gaps", sum(lagH))
        bun = {b: {m: len(v) for m, v in ms.items()} for b, ms in bun.items()}
        log("pos customers", len(first), "branches", list(bstat.keys()),
            "unregistered receipts", sum(sum(v.values()) for v in bun.values()))
        return bnr, bstat, bcoh, bun
    except Exception as e:
        log("pos customers fail", str(e)[:160])
    return bnr, bstat, {}, {b: {m: len(v) for m, v in ms.items()} for b, ms in bun.items()}


def pull_shop_lines():
    """Shopify customer deciles at line level from sale.report: net revenue (untaxed, after
    discount), margin, units, and the discount actually given -- so AUR, UPT, DR%% and gross
    retail are real sums, not allocations. Also feeds vendor/product monthly for the online side."""
    TV = tmpl_vendor_map(); VNM = vendor_names()
    agg = {}; oc = {}; oseen = set(); SATTR = {}; ODATES = {}; JRO = []; JVL = []
    off = 0
    while off < 900000:
        page = oexec("sale.report", "search_read",
                     [[["team_id.name", "=", "Shopify"], ["date", ">=", "2024-08-01"],
                       ["state", "not in", ["draft", "sent", "cancel"]], ["partner_id", "!=", False]]],
                     {"fields": ["partner_id", "date", "price_subtotal", "margin", "product_uom_qty",
                                 "discount", "order_reference", "product_tmpl_id"],
                      "limit": 10000, "offset": off, "order": "id"})
        if not page: break
        for r in page:
            pid = r["partner_id"][0]; d = str(r["date"])[:10]
            rv = float(r.get("price_subtotal") or 0); mg = float(r.get("margin") or 0)
            qy = float(r.get("product_uom_qty") or 0); dc = float(r.get("discount") or 0)
            gr = rv / (1 - dc / 100.0) if 0 < dc < 100 else rv
            _tid = (r.get("product_tmpl_id") or [0])[0]
            _tv = TV.get(_tid, ("", "", ""))
            _vn = _tv[0]
            _m7 = d[:7]
            if rv > 0:
                _at = SATTR.setdefault(pid, {"cat": {}, "ven": {}})
                _cg = _catname(_tv[1])
                if _cg:
                    _at["cat"][_cg] = _at["cat"].get(_cg, 0.0) + rv
                    JRO.append((pid, d, _cg, rv))
                if _vn:
                    _vl = VNM.get(_vn) or _vn
                    _at["ven"][_vl] = _at["ven"].get(_vl, 0.0) + rv
            if _vn:
                _vm = XTRA.setdefault("vmon", {}).setdefault(_vn, {}).setdefault(_m7, [0.0, 0.0])
                _vm[0] += rv; _vm[1] += mg
            if _tid:
                _pm = XTRA.setdefault("pmon", {}).setdefault(_tid, {}).setdefault(_m7, [0.0, 0.0])
                _pm[0] += rv; _pm[1] += mg
            a = agg.get(pid)
            if a is None: a = agg[pid] = [0.0, 0.0, 0.0, 0.0, d, d, 0.0, 0.0]
            a[0] += rv; a[1] += mg; a[2] += qy
            if rv < 0: a[3] += -rv
            if d < a[4]: a[4] = d
            if d > a[5]: a[5] = d
            a[6] += gr; a[7] += gr - rv
            ref = r.get("order_reference") or 0  # reference field -> 'sale.order,<id>' STRING
            if ref and (pid, ref) not in oseen:
                oseen.add((pid, ref)); oc[pid] = oc.get(pid, 0) + 1
            if ref: ODATES.setdefault(pid, set()).add(d)
            if _vn and rv > 0: JVL.append((pid, d, VNM.get(_vn) or _vn))
        off += len(page)
        if len(page) < 10000: break
    XTRA.setdefault("dec", {})["Shopify"] = _decile(agg, oc, SATTR)
    log("shopify decile customers", len(agg), "orders", len(oseen))
    # ---- v8.4 ONLINE journeys from the same line crawl (real line-level cat/vendor) ----
    try:
        first_o = {p: a2[4] for p, a2 in agg.items()}
        d2o = {}
        for p, ds in ODATES.items():
            dl = sorted(ds)
            if len(dl) >= 2:
                d2o[p] = (datetime.date.fromisoformat(dl[1]) - datetime.date.fromisoformat(dl[0])).days
        def _jp(pids):
            n = len(pids)
            if not n: return None
            reps = [d2o[p] for p in pids if p in d2o]
            hist = [0] * 13
            for g2 in reps: hist[min(g2 // 30, 12)] += 1
            opy = [0] * 6; yrs = 0.0; tot_o = 0
            for p in pids:
                span = max(30, (END - datetime.date.fromisoformat(first_o[p])).days) / 365.0
                c2 = oc.get(p, 0); tot_o += c2; yrs += span
                opy[min(max(c2, 1), 6) - 1] += 1
            return {"n": n, "rep": round(len(reps) / n * 100, 1),
                    "med2": int(statistics.median(reps)) if reps else None,
                    "oyr": round(tot_o / yrs, 2) if yrs else 0,
                    "ltgp": round(sum(agg[p][1] for p in pids) / n),
                    "ltv": round(sum(agg[p][0] for p in pids) / n),
                    "h2": hist, "opy": opy}
        def _jm(pids, key, topn=8):
            ag2 = {}
            for p in pids:
                for k2, v2 in SATTR.get(p, {}).get(key, {}).items(): ag2[k2] = ag2.get(k2, 0.0) + v2
            return [[k2, round(v2)] for k2, v2 in sorted(ag2.items(), key=lambda kv: -kv[1])[:topn]]
        allp = list(agg.keys())
        pr = _jp(allp)
        if pr:
            pr["cat"] = _jm(allp, "cat"); pr["ven"] = _jm(allp, "ven")
            XTRA.setdefault("jour", {}).setdefault("scopes", {})["ONLINE"] = pr
        fcat = {}; nxt = {}
        for p, d, cg, rv in JRO:
            f0 = first_o.get(p)
            if not f0: continue
            if d == f0:
                e2 = fcat.setdefault(p, {}); e2[cg] = e2.get(cg, 0.0) + rv
            else:
                nx = nxt.setdefault(p, {}); nx[cg] = nx.get(cg, 0.0) + rv
        bycat = {}
        for p, cs in fcat.items(): bycat.setdefault(max(cs, key=cs.get), []).append(p)
        catON = {}
        for cg, pids in bycat.items():
            if len(pids) < 40: continue
            pr = _jp(pids)
            if not pr: continue
            nagg = {}
            for p in pids:
                for k2, v2 in nxt.get(p, {}).items(): nagg[k2] = nagg.get(k2, 0.0) + v2
            pr["next"] = [[k2, round(v2)] for k2, v2 in sorted(nagg.items(), key=lambda kv: -kv[1])[:6]]
            catON[cg] = pr
        JVN0 = {}
        for p, d, vl in JVL:
            if d == first_o.get(p): JVN0.setdefault(p, set()).add(vl)
        byven = {}
        for p, vs in JVN0.items():
            for v in vs: byven.setdefault(v, []).append(p)
        venON = {}
        for vn2, pids in sorted(byven.items(), key=lambda kv: -len(kv[1]))[:40]:
            if len(pids) < 25: continue
            pr = _jp(pids)
            if pr: venON[vn2] = pr
        J0 = XTRA.setdefault("jour", {})
        J0["catON"] = catON; J0["venON"] = venON
        log("online journeys :: customers", len(allp), ":: first-category segments", len(catON),
            ":: first-vendor segments", len(venON))
    except Exception as e:
        log("online journeys fail", str(e)[:150])
    # ---- v6.5 online->offline crossover: the same partner_id on both sides ----
    pa = XTRA.get("posagg") or {}
    if pa:
        inter = set(pa) & set(agg)
        posr = sum(pa[p][0] for p in inter); posg = sum(pa[p][1] for p in inter)
        shr = sum(agg[p][0] for p in inter); shg = sum(agg[p][1] for p in inter)
        vx = []
        for v, pids in (XTRA.get("vcs") or {}).items():
            n2 = len(pids & inter)
            if n2 >= 25: vx.append((v, n2))
        vx.sort(key=lambda x: -x[1])
        XTRA["xchan"] = {"cust": len(inter), "shopOnly": len(set(agg) - inter), "posOnly": len(set(pa) - inter),
                         "posrev": round(posr), "posgp": round(posg), "shoprev": round(shr), "shopgp": round(shg),
                         "vend": [[v, n2] for v, n2 in vx[:20]]}
        log("xchan customers", len(inter), "of", len(agg), "online")

def _visit_platform(v):
    """Classify a Shopify visit into a paid platform by its source / utm_source."""
    if not v: return None
    src = (v.get("source") or "").lower()
    utm = ((v.get("utmParameters") or {}).get("source") or "").lower()
    if any(k in src for k in ("facebook", "instagram", "meta")) or utm in ("facebook", "instagram", "meta", "fb", "ig", "an", "msg"): return "meta"
    if "google" in src or utm in ("google", "googleads", "adwords", "gads") or "gclid" in src: return "google"
    if "tiktok" in src or utm in ("tiktok", "tt", "ttclid"): return "tiktok"
    return None

def pull_meta_driven_cross():
    """v8.7: orders Shopify itself attributes to Facebook/Instagram (first or last
    visit), buyer emails -> Odoo partners -> their REAL POS purchases. Heavy-only."""
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    if not store or not tok:
        log("paid-driven cross :: SKIPPED - shopify env missing"); return
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    since = (END - datetime.timedelta(days=180)).isoformat()
    q = ('query($c:String){ orders(first:250, after:$c, query:"created_at:>=%s") '
         '{ pageInfo{hasNextPage endCursor} nodes{ email '
         'customerJourneySummary{ firstVisit{ source utmParameters{ source } } '
         'lastVisit{ source utmParameters{ source } } } } } }' % since)
    emails = {"meta": set(), "google": set(), "tiktok": set()}; total = 0; cursor = None; pages = 0
    while pages < 200:
        d = http_json("https://%s/admin/api/2025-07/graphql.json" % host,
                      {"query": q, "variables": {"c": cursor}}, {"X-Shopify-Access-Token": tok})
        od = (((d or {}).get("data") or {}).get("orders") or {})
        nodes = od.get("nodes")
        if nodes is None:
            log("paid-driven cross :: orders query failed ::", str((d or {}).get("errors") or d)[:180]); break
        pages += 1; total += len(nodes)
        for o in nodes:
            cj = o.get("customerJourneySummary") or {}
            em = (o.get("email") or "").strip().lower()
            if not em or "@" not in em: continue
            for pf in {_visit_platform(cj.get("firstVisit")), _visit_platform(cj.get("lastVisit"))}:
                if pf: emails[pf].add(em)
        pi = od.get("pageInfo") or {}
        if not pi.get("hasNextPage"): break
        cursor = pi.get("endCursor")
    log("paid-driven cross :: shopify orders scanned", total, "(180d, %d pages)" % pages,
        ":: attributed emails :: meta", len(emails["meta"]), "google", len(emails["google"]),
        "tiktok", len(emails["tiktok"]))
    if not any(emails.values()): return
    if pages >= 200: log("paid-driven cross :: NOTE hit the 200-page cap; earliest orders in the window not scanned")
    pa = XTRA.get("posagg") or {}
    vcs = XTRA.get("vcs") or {}
    # one email->partner lookup over the union, then split per platform
    allmail = sorted(set().union(*emails.values()))
    e2p = {}
    for i in range(0, len(allmail), 200):
        try:
            for r in (oexec("res.partner", "search_read", [[["email", "in", allmail[i:i + 200]]]],
                            {"fields": ["id", "email"]}) or []):
                em2 = (r.get("email") or "").strip().lower()
                if em2: e2p.setdefault(em2, set()).add(r["id"])
        except Exception as e:
            log("paid-driven cross :: partner lookup failed ::", str(e)[:120]); break
    plats = {}
    for pf, ems in emails.items():
        pids = set()
        for em2 in ems: pids |= e2p.get(em2, set())
        hit = [p for p in pids if p in pa]
        hs = set(hit)
        vend = sorted(((v, len(cust & hs)) for v, cust in vcs.items()), key=lambda kv: -kv[1])
        plats[pf] = {"onlineCust": len(ems), "matched": len(pids), "cust": len(hit),
                     "posrev": round(sum(pa[p][0] for p in hit)),
                     "posgp": round(sum(pa[p][1] for p in hit)),
                     "vend": [[v, c] for v, c in vend if c >= 5][:15]}
        log("paid-driven cross ::", pf, ":: matched", len(pids), ":: store buyers", len(hit),
            ":: store revenue", plats[pf]["posrev"])
    mc = dict(plats["meta"]); mc.update({"days": 180, "orders": total, "plats": plats})
    XTRA["mcross"] = mc

def pull_google_attr():
    """v9.0: Google revenue attribution at campaign, asset-group (PMax) and ad level,
    last 60d totals from the Google Ads API. Conversion value as Google reports it."""
    dev = os.environ.get("GOOGLE_DEVELOPER_TOKEN", ""); cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    if not (dev and cid): return {}
    try:
        at, hd = _gads_hdr()
    except Exception as e:
        log("google attr :: token fail", str(e)[:120]); return {}
    if not at: return {}
    c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=59)).isoformat()
    base = "https://googleads.googleapis.com/v21/customers/%s/googleAds:searchStream" % cid
    def q(gql):
        try:
            req = urllib.request.Request(base, data=json.dumps({"query": gql}).encode(), headers=hd)
            with urllib.request.urlopen(req, timeout=90) as r: d = json.loads(r.read())
            rows = []
            for b2 in (d if isinstance(d, list) else [d]): rows += b2.get("results", [])
            return rows
        except urllib.error.HTTPError as e2:
            log("google attr :: query fail ::", e2.code, e2.read().decode("utf-8", "ignore")[:400]); return []
        except Exception as e:
            log("google attr :: query fail ::", str(e)[:140]); return []
    W = "WHERE segments.date BETWEEN '%s' AND '%s'" % (c0, c1)
    M = "metrics.cost_micros, metrics.conversions_value, metrics.conversions, metrics.impressions, metrics.clicks"
    out = {"win": [c0, c1], "campaigns": [], "assetGroups": [], "ads": []}
    for r in q("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, %s FROM campaign %s" % (M, W)):
        c = r.get("campaign", {}); m = r.get("metrics", {})
        sp = float(m.get("costMicros", 0)) / 1e6
        if sp < 50: continue
        out["campaigns"].append({"n": (c.get("name") or "")[:70], "t": c.get("advertisingChannelType", ""),
                                 "sp": round(sp), "cv": round(float(m.get("conversionsValue", 0))),
                                 "cn": round(float(m.get("conversions", 0)), 1),
                                 "im": int(m.get("impressions", 0)), "ck": int(m.get("clicks", 0))})
    for r in q("SELECT asset_group.id, asset_group.name, campaign.name, %s FROM asset_group %s" % (M, W)):
        a2 = r.get("assetGroup", {}); m = r.get("metrics", {})
        sp = float(m.get("costMicros", 0)) / 1e6
        if sp < 50: continue
        out["assetGroups"].append({"n": (a2.get("name") or "")[:70], "cmp": (r.get("campaign", {}).get("name") or "")[:50],
                                   "sp": round(sp), "cv": round(float(m.get("conversionsValue", 0))),
                                   "cn": round(float(m.get("conversions", 0)), 1),
                                   "im": int(m.get("impressions", 0)), "ck": int(m.get("clicks", 0))})
    for r in q("SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group.name, campaign.name, %s FROM ad_group_ad %s" % (M, W)):
        ad2 = (r.get("adGroupAd", {}) or {}).get("ad", {}); m = r.get("metrics", {})
        sp = float(m.get("costMicros", 0)) / 1e6
        if sp < 50: continue
        out["ads"].append({"id": str(ad2.get("id", "")), "t": ad2.get("type", ""),
                           "ag": (r.get("adGroup", {}).get("name") or "")[:50],
                           "cmp": (r.get("campaign", {}).get("name") or "")[:50],
                           "sp": round(sp), "cv": round(float(m.get("conversionsValue", 0))),
                           "cn": round(float(m.get("conversions", 0)), 1),
                           "im": int(m.get("impressions", 0)), "ck": int(m.get("clicks", 0))})
    # v9.1: the ACTUAL attribution model per conversion action -- queried, not assumed
    out["actions"] = []
    for r in q("SELECT conversion_action.name, conversion_action.type, "
               "conversion_action.attribution_model_settings.attribution_model, "
               "conversion_action.click_through_lookback_window_days, "
               "conversion_action.primary_for_goal "
               "FROM conversion_action WHERE conversion_action.status = 'ENABLED'"):
        ca = r.get("conversionAction", {})
        out["actions"].append({"n": (ca.get("name") or "")[:60],
                               "t": ca.get("type", ""),
                               "model": ((ca.get("attributionModelSettings") or {}).get("attributionModel") or ""),
                               "win": ca.get("clickThroughLookbackWindowDays"),
                               "pri": bool(ca.get("primaryForGoal"))})
    if out["actions"]:
        log("google attribution models ::", " | ".join(
            "%s=%s(%sd)%s" % (a2["n"], a2["model"], a2["win"], " PRIMARY" if a2["pri"] else "")
            for a2 in out["actions"][:8]))
    # v9.2: per-campaign CONVERSION-ACTION breakdown -- proves WHICH events each campaign
    # counts. metrics.conversions only counts actions used for bidding, all_conversions
    # counts everything -- the gap per row exposes valueless actions steering spend.
    out["cmpAct"] = []
    for r in q("SELECT campaign.name, segments.conversion_action_name, "
               "segments.conversion_action_category, metrics.conversions, metrics.conversions_value, "
               "metrics.all_conversions, metrics.all_conversions_value FROM campaign %s "
               "AND metrics.all_conversions > 0" % W):
        c = r.get("campaign", {}); m = r.get("metrics", {}); sg = r.get("segments", {})
        out["cmpAct"].append({"cmp": (c.get("name") or "")[:70],
                              "a": (sg.get("conversionActionName") or "")[:60],
                              "cat": sg.get("conversionActionCategory", ""),
                              "cn": round(float(m.get("conversions", 0)), 1),
                              "cv": round(float(m.get("conversionsValue", 0))),
                              "an": round(float(m.get("allConversions", 0)), 1),
                              "av": round(float(m.get("allConversionsValue", 0)))})
    out["cmpAct"].sort(key=lambda x: -x["an"])
    out["cmpAct"] = out["cmpAct"][:150]
    # v9.2: OFFLINE / store-side totals per conversion action (store visits, calls,
    # directions live only in all_conversions -- Google's own offline-attributed numbers)
    out["offline"] = []
    _tmap = {a4["n"]: a4.get("t", "") for a4 in out["actions"]}
    for r in q("SELECT customer.id, segments.conversion_action_name, "
               "segments.conversion_action_category, "
               "metrics.all_conversions, metrics.all_conversions_value, "
               "metrics.conversions, metrics.conversions_value FROM customer %s "
               "AND metrics.all_conversions > 0" % W):
        m = r.get("metrics", {}); sg = r.get("segments", {})
        nm2 = (sg.get("conversionActionName") or "")[:60]
        out["offline"].append({"n": nm2, "cat": sg.get("conversionActionCategory", ""),
                               "t": _tmap.get(nm2, ""),
                               "an": round(float(m.get("allConversions", 0)), 1),
                               "av": round(float(m.get("allConversionsValue", 0))),
                               "cn": round(float(m.get("conversions", 0)), 1),
                               "cv": round(float(m.get("conversionsValue", 0)))})
    out["offline"].sort(key=lambda x: -x["an"])
    # v9.2: the ASSETS inside each PMax asset group. Google exposes NO per-asset
    # conversion metrics here -- only its own performance label -- so that is what we show.
    out["assets"] = []
    for r in q("SELECT campaign.name, asset_group.name, asset_group_asset.field_type, "
               "asset_group_asset.performance_label, asset.type, asset.name, asset.text_asset.text "
               "FROM asset_group_asset WHERE asset_group_asset.status = 'ENABLED'"):
        a3 = r.get("asset", {}); ga = r.get("assetGroupAsset", {})
        txt = ((a3.get("textAsset") or {}).get("text") or a3.get("name") or "")[:80]
        out["assets"].append({"cmp": (r.get("campaign", {}).get("name") or "")[:50],
                              "ag": (r.get("assetGroup", {}).get("name") or "")[:50],
                              "f": ga.get("fieldType", ""), "t": a3.get("type", ""),
                              "x": txt, "pl": ga.get("performanceLabel", "")})
    out["assets"] = out["assets"][:400]
    for k in ("campaigns", "assetGroups", "ads"): out[k].sort(key=lambda x: -x["sp"])
    out["ads"] = out["ads"][:40]
    log("google attr :: campaigns", len(out["campaigns"]), ":: asset groups", len(out["assetGroups"]),
        ":: ads", len(out["ads"]), ":: cmp-action rows", len(out["cmpAct"]),
        ":: offline actions", len(out["offline"]), ":: assets", len(out["assets"]))
    return out

def pull_meta_ads(tok):
    """v8.3: per-ad DAILY series for the last 60 days -- spend, online value, REAL
    offline value, purchases, outbound clicks, in-store new customers (value+count)
    -- tagged with account, adset and campaign. The Top Ads view filters by level,
    account and ANY date range from these; the Budget tab judges changes from the
    campaign/adset rollups. Totals keep the old field names; per-ad "offline" is now
    offline_conversion.purchase, not omni minus pixel."""
    out = []
    if not tok:
        p2 = os.path.join(DOCS, "mads_fallback.json")
        if os.path.exists(p2):
            try: return json.load(open(p2))
            except Exception: pass
        return out
    try:
        c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=59)).isoformat()
        XTRA["madsW"] = {"start": c0, "n": 60}
        di = {}
        for i in range(60): di[(datetime.date.fromisoformat(c0) + datetime.timedelta(days=i)).isoformat()] = i
        A = {}
        for acct in meta_accounts(tok):
            allnc = [MCC_ALLNC]
            try:
                _v2, _n2, _a2 = _cc_discover(acct, tok)
                if _a2: allnc = _a2
            except Exception: pass
            p = {"level": "ad", "time_increment": 1, "access_token": tok,
                 "time_range": json.dumps({"since": c0, "until": c1}),
                 "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
                           "spend,impressions,outbound_clicks,actions,action_values",
                 "limit": 500}
            url = "%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p))
            pages = 0
            while url and pages < 60:
                d = http_json(url); pages += 1
                if d.get("error"):
                    log("meta ads :: PAGE ERROR on", acct, "page", pages, "::", str(d.get("error"))[:160]); break
                for r in (d.get("data") or []):
                    i = di.get(r.get("date_start"))
                    if i is None: continue
                    aid = r.get("ad_id")
                    a = A.get(aid)
                    if a is None:
                        a = A[aid] = {"id": aid, "n": (r.get("ad_name") or "")[:80],
                                      "as": (r.get("adset_name") or "")[:60], "asid": r.get("adset_id"),
                                      "cmp": (r.get("campaign_name") or "")[:60], "cid": r.get("campaign_id"),
                                      "acct": ACCT_NAMES.get(acct, acct), "pf": "meta",
                                      "d": {k: [0.0] * 60 for k in ("sp", "pv", "fv", "pu", "op", "oc", "im", "nc", "ncv", "vv")}}
                    av = r.get("action_values") or []; ac = r.get("actions") or []
                    D = a["d"]
                    D["sp"][i] += float(r.get("spend") or 0)
                    D["im"][i] += float(r.get("impressions") or 0)
                    D["oc"][i] += _av(r.get("outbound_clicks"), ("outbound_click",))
                    D["pv"][i] += _av(av, ("offsite_conversion.fb_pixel_purchase",))
                    D["fv"][i] += _av(av, ("offline_conversion.purchase",))
                    D["pu"][i] += _av(ac, ("offsite_conversion.fb_pixel_purchase",))
                    D["op"][i] += _av(ac, ("offline_conversion.purchase",))
                    D["vv"][i] += _av(ac, ("video_view",))
                    ccv = _cc(av); cca = _cc(ac)
                    for cid2 in allnc:
                        D["nc"][i] += cca.get(cid2, 0.0); D["ncv"][i] += ccv.get(cid2, 0.0)
                url = (d.get("paging") or {}).get("next")
        ads = []
        for a in A.values():
            D = a["d"]; sp = sum(D["sp"])
            if sp < 1000: continue
            a.update({"sp": round(sp), "pv": round(sum(D["pv"])), "ofv": round(sum(D["fv"])),
                      "ov": round(sum(D["pv"]) + sum(D["fv"])),
                      "pur": int(sum(D["pu"])), "opur": int(sum(D["op"])),
                      "imp": int(sum(D["im"])), "clk": int(sum(D["oc"])),
                      "nc": int(sum(D["nc"])), "ncv": round(sum(D["ncv"])), "vv": int(sum(D["vv"]))})
            a["d"] = {k: [int(round(x)) for x in v] for k, v in D.items()}
            ads.append(a)
        # v9.8: this used to be a single global top-120. The big account's ads filled every
        # slot (its smallest still outspent everything on Basic), so the Basic account
        # vanished from Top Ads / Creative Benchmarks entirely and looked "not synced".
        # Cap PER ACCOUNT so a small account always gets its own shelf.
        ads.sort(key=lambda a: -a["sp"])
        _per = {}
        _keep = []
        for a in ads:
            k = a.get("acct") or "?"
            _per[k] = _per.get(k, 0) + 1
            if _per[k] <= 120: _keep.append(a)
        ads = _keep
        log("meta ads :: kept per account", {k: v for k, v in _per.items()})
        lastday = max([max((i for i in range(60) if a["d"]["sp"][i] > 0), default=0) for a in ads] or [0])
        if lastday < 55:
            log("meta ads :: PARTIAL PULL -- newest spend day is index", lastday, "of 60 ::",
                "Meta rate-limited the pagination. Using the last complete pull instead.")
            p2 = os.path.join(DOCS, "mads_fallback.json")
            if os.path.exists(p2):
                try:
                    fb = json.load(open(p2))
                    fdays = max([max((i for i in range(len((a.get("d") or {}).get("sp") or [])) if a["d"]["sp"][i] > 0), default=0) for a in fb if a.get("d")] or [0])
                    if fdays > lastday:
                        log("meta ads :: fallback covers day", fdays, "-- keeping it, NOT overwriting")
                        return fb
                except Exception: pass
        def _f0(a2):
            sp2 = a2["d"]["sp"]
            for k in range(len(sp2)):
                if sp2[k] > 0: return k
            return -1
        lset = [a for a in ads if _f0(a) >= max(1, 60 - 21)]
        ids = list(dict.fromkeys([a["id"] for a in ads[:40] if a["id"]] + [a["id"] for a in lset if a["id"]]))[:80]
        for i in range(0, len(ids), 25):
            d = http_json("%s/?ids=%s&fields=creative.thumbnail_width(600).thumbnail_height(600){thumbnail_url,image_url,object_type},preview_shareable_link,effective_status&access_token=%s" % (GRAPH, ",".join(ids[i:i + 25]), tok))
            for a in ads:
                info = (d or {}).get(a["id"]) or {}
                cr = info.get("creative") or {}
                if cr.get("thumbnail_url"): a["th"] = cr["thumbnail_url"]
                if cr.get("image_url"): a["im2"] = cr["image_url"]
                if info.get("preview_shareable_link"): a["pl"] = info["preview_shareable_link"]
                if info.get("effective_status"): a["st"] = str(info["effective_status"])[:32]
        for a in ads:
            if a.get("im2"): a["im"] = a.pop("im2")
        log("meta ads v8.3 ::", len(ads), "ads with 60d daily series :: accounts",
            len(set(a["acct"] for a in ads)), ":: offline value", sum(a["ofv"] for a in ads),
            ":: in-store NC value", sum(a["ncv"] for a in ads))
        try: json.dump(ads, open(os.path.join(DOCS, "mads_fallback.json"), "w"))
        except Exception: pass
        return ads
    except Exception as e:
        log("meta ads fail", str(e)[:150])
        p2 = os.path.join(DOCS, "mads_fallback.json")
        if os.path.exists(p2):
            try: return json.load(open(p2))
            except Exception: pass
        return out

def pull_budget_events(tok):
    """v8.3: every budget change in the last 60 days from the account activity feed.
    CBO arrives as update_campaign_budget, ABO as update_ad_set_budget. extra_data
    old/new values are minor units (piastres) -> /100; raw kept when parsing is odd."""
    if not tok: return []
    out = []
    c0 = (END - datetime.timedelta(days=59)).isoformat()
    KEEP = ("update_campaign_budget", "update_ad_set_budget",
            "update_campaign_daily_budget", "update_campaign_lifetime_budget")
    for acct in meta_accounts(tok):
        p = {"access_token": tok, "limit": 500, "since": c0,
             "fields": "event_type,event_time,object_id,object_name,extra_data"}
        url = "%s/%s/activities?%s" % (GRAPH, acct, urllib.parse.urlencode(p))
        pages = 0
        while url and pages < 10:
            d = http_json(url); pages += 1
            rows = d.get("data")
            if rows is None:
                log("budget events ::", acct, "::", str(d.get("error") or d)[:160]); break
            for r in rows:
                et = r.get("event_type") or ""
                if et not in KEEP: continue
                try: xd = json.loads(r.get("extra_data") or "{}")
                except Exception: xd = {}
                def _cents(v, key):
                    # live schema: extra_data.old_value = {"type":"payment_amount",
                    # "currency":"EGP","old_value":660000,...} -- the number sits INSIDE,
                    # under the same key name, in minor units.
                    if isinstance(v, dict):
                        v = v.get(key, v.get("value", v.get("amount")))
                    try: return round(float(v) / 100.0, 2)
                    except Exception: return None
                ov2 = _cents(xd.get("old_value"), "old_value"); nv2 = _cents(xd.get("new_value"), "new_value")
                out.append({"t": str(r.get("event_time"))[:10],
                            "lvl": "campaign" if "campaign" in et else "adset",
                            "id": str(r.get("object_id") or ""), "nm": (r.get("object_name") or "")[:70],
                            "old": ov2, "new": nv2, "acct": ACCT_NAMES.get(acct, acct),
                            "raw": "" if (ov2 is not None and nv2 is not None) else str(xd)[:120]})
            url = (d.get("paging") or {}).get("next")
    out.sort(key=lambda e2: e2["t"], reverse=True)
    log("budget events ::", len(out), "changes in 60d")
    return out[:200]

def pull_campaign_reach(tok):
    """v8.3: daily deduplicated reach per campaign, 60d -- for judging budget changes."""
    if not tok: return {}
    c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=59)).isoformat()
    out = {}
    for acct in meta_accounts(tok):
        p = {"level": "campaign", "time_increment": 1, "access_token": tok,
             "time_range": json.dumps({"since": c0, "until": c1}),
             "fields": "campaign_id,reach", "limit": 500}
        url = "%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p))
        pages = 0
        while url and pages < 20:
            d = http_json(url); pages += 1
            for r in (d.get("data") or []):
                try: i = (datetime.date.fromisoformat(r["date_start"]) - datetime.date.fromisoformat(c0)).days
                except Exception: continue
                if 0 <= i < 60:
                    out.setdefault(str(r.get("campaign_id")), [0] * 60)[i] += int(float(r.get("reach") or 0))
            url = (d.get("paging") or {}).get("next")
    log("campaign reach ::", len(out), "campaigns 60d daily")
    return out

def pull_obj_daily(tok, bev):
    """v8.4: daily series (60d) for EXACTLY the campaigns/ad sets that appear in the
    budget-change feed, from their own account-level insights at that level."""
    if not tok or not bev: return {}
    want = {e["id"] for e in bev if e.get("id")}
    c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=59)).isoformat()
    out = {}
    for acct in meta_accounts(tok):
        for lvl, idf in (("campaign", "campaign_id"), ("adset", "adset_id")):
            p = {"level": lvl, "time_increment": 1, "access_token": tok,
                 "time_range": json.dumps({"since": c0, "until": c1}),
                 "fields": "%s,spend,impressions,outbound_clicks,actions,action_values" % idf,
                 "limit": 500}
            url = "%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p))
            pages = 0
            while url and pages < 40:
                d = http_json(url); pages += 1
                if d.get("error"):
                    log("budget-object daily :: PAGE ERROR", acct, lvl, "::", str(d.get("error"))[:140]); break
                for r in (d.get("data") or []):
                    oid = str(r.get(idf) or "")
                    if oid not in want: continue
                    try: i = (datetime.date.fromisoformat(r["date_start"]) - datetime.date.fromisoformat(c0)).days
                    except Exception: continue
                    if not (0 <= i < 60): continue
                    o = out.setdefault(oid, {k: [0] * 60 for k in ("sp", "pv", "fv", "pu", "op", "oc", "im")})
                    av = r.get("action_values") or []; ac = r.get("actions") or []
                    o["sp"][i] += int(round(float(r.get("spend") or 0)))
                    o["im"][i] += int(float(r.get("impressions") or 0))
                    o["oc"][i] += int(_av(r.get("outbound_clicks"), ("outbound_click",)))
                    o["pv"][i] += int(round(_av(av, ("offsite_conversion.fb_pixel_purchase",))))
                    o["fv"][i] += int(round(_av(av, ("offline_conversion.purchase",))))
                    o["pu"][i] += int(_av(ac, ("offsite_conversion.fb_pixel_purchase",)))
                    o["op"][i] += int(_av(ac, ("offline_conversion.purchase",)))
                url = (d.get("paging") or {}).get("next")
    log("budget-object daily series ::", len(out), "of", len(want), "event objects have delivery data")
    return out

def _norm_contact(r):
    """Odoo partner -> (email_lower, egypt_phone_digits '20...'). Empty string when unusable."""
    em = (r.get("email") or "").strip().lower()
    if "@" not in em: em = ""
    # never upload our own staff/company addresses as if they were customers -- several
    # internal accounts (accounting@, it@, m.emad@ ...) appear as the partner on real orders
    if em.endswith("@ourkids-eg.com"): em = ""
    ph = re.sub(r"\D", "", str(r.get("mobile") or r.get("phone") or ""))
    if ph.startswith("00"): ph = ph[2:]
    if ph.startswith("0"): ph = "20" + ph[1:]
    elif ph and not ph.startswith("20") and len(ph) == 10: ph = "20" + ph
    if len(ph) < 11: ph = ""
    return em, ph

def _branch_transactions(days):
    """Per-branch POS transactions with buyer contact, for the Google store-sales upload:
    [(branch, email, phone, amount, 'YYYY-MM-DD HH:MM:SS')]. One row per ORDER (lines summed).
    Anonymous walk-in partners are excluded before anything is read."""
    cutoff = (END - datetime.timedelta(days=days)).isoformat()
    ANON = anon_partner_ids()
    orders = {}
    off = 0
    while off < 400000:
        page = oexec("report.pos.order", "search_read",
                     [[["partner_id", "!=", False], ["date", ">=", cutoff]]],
                     {"fields": ["partner_id", "config_id", "date", "price_total", "order_id"],
                      "limit": 10000, "offset": off, "order": "id"})
        if not page: break
        for r in page:
            pid = r["partner_id"][0]
            if pid in ANON: continue
            cfg = (r.get("config_id") or [0, ""])[1]
            br = next((b for k, b in POS_CFG if k in cfg), None)
            if not br: continue
            oid = (r.get("order_id") or [0])[0]
            if not oid: continue
            o = orders.setdefault(oid, [pid, br, str(r.get("date") or cutoff), 0.0])
            o[3] += float(r.get("price_total") or 0)
        off += len(page)
        if len(page) < 10000: break
    pids = sorted({o[0] for o in orders.values()})
    CT = {}
    for i in range(0, len(pids), 2000):
        for r in (oexec("res.partner", "read", [pids[i:i + 2000]], {"fields": ["email", "phone", "mobile"]}) or []):
            CT[r["id"]] = _norm_contact(r)
    out = []
    for oid, (pid, br, d, amt) in orders.items():
        em, ph = CT.get(pid, ("", ""))
        dt = d[:19] if len(d) > 10 else d[:10] + " 12:00:00"
        if (em or ph) and amt > 0: out.append((br, em, ph, amt, dt, oid))
    log("branch transactions ::", len(orders), "orders in", days, "d ::", len(out), "with contact+amount")
    return out

_DM = "https://datamanager.googleapis.com/v1"

def _dm_scope_block(d):
    """True when the Data Manager call failed because the refresh token lacks the
    datamanager scope -> the one-time google_reauth.sh run is needed."""
    t = str(d)
    return ("ACCESS_TOKEN_SCOPE_INSUFFICIENT" in t or "insufficient authentication scopes" in t.lower()
            or ("PERMISSION_DENIED" in t and "scope" in t.lower()))

def _dm_dest(pid):
    """Data Manager destination for this Google Ads account (+ MCC login when set)."""
    cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    d = {"operatingAccount": {"product": "GOOGLE_ADS", "accountId": cid},
         "productDestinationId": str(pid)}
    lc = os.environ.get("GOOGLE_LOGIN_CID", "")
    if lc: d["loginAccount"] = {"product": "GOOGLE_ADS", "accountId": lc}
    return d

def sync_gmb_branch_sales():
    """v9.2, per the owner's instruction: make store events FIRE TO THE RELEVANT BRANCH on
    Google. One UPLOAD_CLICKS conversion action per branch (find-by-name, idempotent --
    Google's API refuses to create STORE_SALES-type actions, verified live), then real Odoo
    POS receipts uploaded to each branch's OWN action as enhanced-conversions-for-leads
    (uploadClickConversions with hashed identifiers, order-id deduped). Google matches the
    email/phone to signed-in ad clicks. Emails/phones SHA256-hashed to Google's spec before
    anything leaves the job; amounts in EGP micros. 30d backfill the run the actions are
    created, 4d top-ups after. Kill switch: GMB_BRANCH_SALES=off.
    Gated extras (touch LIVE bidding, so OFF until the owner flips them):
      GMB_GOALS=on          -> each 'GMB <branch>' PMax campaign optimises ONLY its
                               branch's store-sale action (custom goal per campaign)
      GOOGLE_DEMOTE_CALLS=on -> 'Clicks to call' loses PRIMARY so valueless calls stop
                               steering bidding account-wide"""
    if os.environ.get("GMB_BRANCH_SALES", "on").lower() == "off": return
    cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    if not cid: return
    import hashlib
    try:
        at, hd = _gads_hdr()
        if not at: log("gmb branch sales :: skipped, no google creds"); return
        base = "https://googleads.googleapis.com/v21/customers/%s" % cid
        def q(gql):
            d = http_json(base + "/googleAds:searchStream", {"query": gql}, hd)
            rows = []
            for b2 in (d if isinstance(d, list) else [d]): rows += b2.get("results", [])
            return rows
        BR = ["Dokki", "Nasr City", "Smouha", "Mall of Arabia", "October", "New Cairo", "Zayed"]
        have = {}
        for r in q("SELECT conversion_action.resource_name, conversion_action.name "
                   "FROM conversion_action WHERE conversion_action.status = 'ENABLED'"):
            ca = r.get("conversionAction", {})
            have[ca.get("name") or ""] = ca.get("resourceName")
        arn = {}; created = 0
        for br in BR:
            nm = "OurKids Store Sale - %s (auto)" % br
            if have.get(nm): arn[br] = have[nm]; continue
            d = http_json(base + "/conversionActions:mutate",
                          {"operations": [{"create": {"name": nm, "type": "UPLOAD_CLICKS",
                            "category": "PURCHASE", "status": "ENABLED",
                            "valueSettings": {"defaultValue": 0.0, "alwaysUseDefaultValue": False}}}]}, hd)
            try:
                arn[br] = d["results"][0]["resourceName"]; created += 1
                log("gmb branch sales :: created action", nm)
            except Exception:
                log("gmb branch sales :: ACTION CREATE FAILED", br, "::", str(d)[:600])
        if not arn: log("gmb branch sales :: no branch actions available, stopping"); return
        win = 30 if created else 4
        tx = _branch_transactions(win)
        convs = []; per = {}
        for br, em, ph, amt, dt, oid in tx:
            rn = arn.get(br)
            if not rn: continue
            ids = []
            if em: ids.append({"userIdentifierSource": "FIRST_PARTY",
                               "hashedEmail": hashlib.sha256(em.encode()).hexdigest()})
            if ph: ids.append({"userIdentifierSource": "FIRST_PARTY",
                               "hashedPhoneNumber": hashlib.sha256(("+" + ph).encode()).hexdigest()})
            if not ids: continue
            convs.append({"conversionAction": rn, "conversionDateTime": dt + "+02:00",
                          "conversionValue": round(amt, 2), "currencyCode": "EGP",
                          "orderId": "pos-%s" % oid, "userIdentifiers": ids[:5]})
            per[br] = per.get(br, 0) + 1
        if not convs: log("gmb branch sales :: nothing to upload"); return
        # v9.4: Google closed ConversionUploadService to new integrations (verified live:
        # "New integrations ... should use the Data Manager API") -> events:ingest there.
        byact = {}
        for c in convs: byact.setdefault(c["conversionAction"].split("/")[-1], []).append(c)
        sent = 0; blocked = False
        for aid, lst in byact.items():
            evs = [{"transactionId": c["orderId"],
                    "eventTimestamp": c["conversionDateTime"].replace(" ", "T"),
                    "conversionValue": c["conversionValue"], "currency": "EGP",
                    "eventSource": "IN_STORE",
                    "userData": {"userIdentifiers": [
                        ({"emailAddress": u["hashedEmail"]} if "hashedEmail" in u
                         else {"phoneNumber": u["hashedPhoneNumber"]}) for u in c["userIdentifiers"]]},
                    "consent": {"adUserData": "CONSENT_GRANTED", "adPersonalization": "CONSENT_GRANTED"}}
                   for c in lst]
            for i in range(0, len(evs), 1500):
                d = http_json(_DM + "/events:ingest",
                              {"destinations": [_dm_dest(aid)], "encoding": "HEX",
                               "events": evs[i:i + 1500]}, hd)
                if d.get("error"):
                    if _dm_scope_block(d):
                        log("gmb branch sales :: RE-AUTH NEEDED -- the Google token lacks the Data "
                            "Manager scope. Run: bash google_reauth.sh (one time), then bash wire_ourkids.sh.")
                    else:
                        log("gmb branch sales :: DM UPLOAD FAILED (full body, first failure only) ::",
                            str(d.get("error"))[:3000])
                    blocked = True
                    break
                sent += len(evs[i:i + 1500])
            if blocked: break
        log("gmb branch sales :: window", win, "d :: events sent via Data Manager", sent, "of", len(convs), "::",
            " ".join("%s=%d" % (b, c) for b, c in sorted(per.items())))
        # ---- gated: wire each GMB campaign to ITS branch's action (LIVE bidding change)
        if os.environ.get("GMB_GOALS", "off").lower() == "on":
            goals = {}
            for r in q("SELECT custom_conversion_goal.resource_name, custom_conversion_goal.name "
                       "FROM custom_conversion_goal"):
                g2 = r.get("customConversionGoal", {})
                goals[g2.get("name") or ""] = g2.get("resourceName")
            for br in BR:
                gn = "Store Sale %s (auto)" % br
                if gn in goals or br not in arn: continue
                d = http_json(base + "/customConversionGoals:mutate",
                              {"operations": [{"create": {"name": gn, "conversionActions": [arn[br]],
                                                          "status": "ENABLED"}}]}, hd)
                try: goals[gn] = d["results"][0]["resourceName"]; log("gmb goals :: created", gn)
                except Exception: log("gmb goals :: GOAL CREATE FAILED", br, "::", str(d)[:400])
            ALIAS = {"hassan maamon": "Nasr City", "hassan maamoun": "Nasr City"}
            for r in q("SELECT campaign.id, campaign.name FROM campaign "
                       "WHERE campaign.name LIKE '%GMB%' AND campaign.status = 'ENABLED'"):
                c = r.get("campaign", {}); nm2 = (c.get("name") or ""); low = nm2.lower()
                br = next((b for b in BR if b.lower() in low),
                          next((b for k2, b in ALIAS.items() if k2 in low), None))
                g3 = goals.get("Store Sale %s (auto)" % (br or ""))
                if not (br and g3):
                    log("gmb goals :: no branch match for campaign", nm2[:50]); continue
                d = http_json(base + "/conversionGoalCampaignConfigs:mutate",
                              {"operations": [{"update": {
                                  "resourceName": "customers/%s/conversionGoalCampaignConfigs/%s" % (cid, c.get("id")),
                                  "goalConfigLevel": "CAMPAIGN", "customConversionGoal": g3},
                                  "updateMask": "goalConfigLevel,customConversionGoal"}]}, hd)
                log("gmb goals ::", nm2[:40], "->", br,
                    "ok" if not d.get("error") else "FAILED :: " + str(d.get("error"))[:300])
        # ---- gated: demote 'Clicks to call' from PRIMARY (stops E0-value calls bidding)
        if os.environ.get("GOOGLE_DEMOTE_CALLS", "off").lower() == "on":
            for r in q("SELECT conversion_action.resource_name, conversion_action.name, "
                       "conversion_action.primary_for_goal FROM conversion_action "
                       "WHERE conversion_action.status = 'ENABLED'"):
                ca = r.get("conversionAction", {})
                if "call" in (ca.get("name") or "").lower() and ca.get("primaryForGoal"):
                    d = http_json(base + "/conversionActions:mutate",
                                  {"operations": [{"update": {"resourceName": ca.get("resourceName"),
                                                              "primaryForGoal": False},
                                                   "updateMask": "primaryForGoal"}]}, hd)
                    log("demote calls ::", ca.get("name"),
                        "ok" if not d.get("error") else "FAILED :: " + str(d.get("error"))[:300])
    except Exception as e:
        log("gmb branch sales :: fail ::", str(e)[:200])

def _offline_contacts(days, online=False):
    """Recent buyers -> [(email_lower, phone_digits_egypt)] -- normalised RAW pairs.
    Each platform hashes to its own spec (Meta: digits; Google: E.164 with +).
    online=True also folds in web/marketplace buyers from sale.order, so the list is
    "bought anywhere" rather than in-store only."""
    cutoff = (END - datetime.timedelta(days=days)).isoformat()
    ANON = anon_partner_ids()
    def distinct_partners(model, domain):
        """v9.8.2: ask Odoo for DISTINCT partners (read_group), not every order row.
        A full-history backfill is ~1.9M POS orders but only ~200k customers. Paging the
        raw rows took ~190 round-trips and blew the 50-minute job timeout, so the backfill
        never landed; grouping collapses it to ~12 pages. No row cap, so nothing truncates."""
        seen, off, PAGE = set(), 0, 20000
        while True:
            page = oexec(model, "read_group", [domain, ["partner_id"], ["partner_id"]],
                         {"lazy": False, "limit": PAGE, "offset": off})
            if not page: break
            for r in page:
                pid = (r.get("partner_id") or [None])[0]
                if pid and pid not in ANON: seen.add(pid)
            off += len(page)
            if len(page) < PAGE: break
        return seen

    pids = distinct_partners("report.pos.order",
                             [["partner_id", "!=", False], ["date", ">=", cutoff]])
    npos = len(pids)
    if online:
        # Marketplace teams (Noon 10, Jumia 11, Amazon 12, Homzmart 21) are excluded on
        # purpose: on those orders the "customer" is the marketplace company itself, not
        # the shopper -- e.g. "Noon E Commerce S.A.E" carries it@ourkids-eg.com and sits
        # on thousands of orders. Uploading that would put our own staff address into a
        # buyer audience that 31 live ad sets now EXCLUDE. Shopify (13) is the real
        # online channel and is kept.
        pids |= distinct_partners("sale.order",
                                  [["state", "in", ["sale", "done"]], ["partner_id", "!=", False],
                                   ["team_id", "not in", [10, 11, 12, 21]],
                                   ["date_order", ">=", cutoff + " 00:00:00"]])
        log("online buyers ::", len(pids) - npos, "added on top of", npos, "in-store")
    out = []
    pl = list(pids)
    for i in range(0, len(pl), 2000):
        for r in (oexec("res.partner", "read", [pl[i:i + 2000]], {"fields": ["email", "phone", "mobile"]}) or []):
            em, ph = _norm_contact(r)
            if em or ph: out.append((em, ph))
    # v8.9 dedup: many partner records share an email/phone (Odoo duplicate customers);
    # send each identity ONCE per run.
    out = sorted(set(out))
    log("buyer contacts ::", len(pids), "buyers in", days, "d ::", len(out), "unique identities")
    return out

# v9.7: the buyer list is no longer in-store only, so the name changed. Both platforms
# find their list BY NAME, so we match the old name too -- otherwise the first run after
# a rename cannot find the list and silently builds a duplicate from scratch.
BUYERS_LIST_NAME = "Ourkids All Buyers (auto)"
BUYERS_LIST_ALIASES = (BUYERS_LIST_NAME, "Ourkids Offline Buyers (auto)")
BUYERS_LIST_DESC = "Auto-updated by the dashboard collector: everyone who ever bought, online or in-store (Odoo)"

# In-store buyers only. NOTE the name says "from ads", but Meta cannot export who saw an
# ad -- there is no ad-exposure audience source, and an offline purchase cannot be written
# as a pixel rule. So this list is every branch buyer; the "saw an ad" half has to be added
# at ad-set level by narrowing this list with an engagement audience.
# "offline buyers from ads" (120249215962380621) is NOT built here. It is a Meta rule
# audience over dataset 770014046405609 -- Purchase + action_source=physical_store -- which
# Meta refreshes itself from the offline CAPI events. Nothing to upload from Odoo.

def _buyer_window(created):
    """Days of history to scan. Normal runs top up the last few days; a fresh list gets
    the full backfill; BUYERS_BACKFILL_DAYS forces a one-off wider re-scan."""
    return int(os.environ.get("BUYERS_BACKFILL_DAYS") or 0) or (730 if created else 4)

def _gads_hdr():
    """OAuth access token + headers for the Google Ads REST API (same creds as the pull)."""
    dev = os.environ.get("GOOGLE_DEVELOPER_TOKEN", "")
    if not dev: return None, None
    data = urllib.parse.urlencode({"client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
        "grant_type": "refresh_token"}).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
          headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=60) as r:
        at = json.loads(r.read()).get("access_token")
    hd = {"Authorization": "Bearer " + at, "developer-token": dev, "Content-Type": "application/json"}
    lc = os.environ.get("GOOGLE_LOGIN_CID", "")
    if lc: hd["login-customer-id"] = lc
    return at, hd

def sync_google_audience():
    """v8.8: same offline-buyer segment on GOOGLE -- a Customer Match user list kept
    fresh every run (create once, 730d backfill, then 4d top-ups). Emails/phones are
    SHA256-hashed to Google's spec (email lowercase; phone E.164 with +) before anything
    leaves the job. NOTE this is Customer Match (audiences); true Store-Sales CONVERSION
    uploads need Google to allowlist the account -- logged here the day that changes.
    Kill switch: GOOGLE_OFFLINE_AUDIENCE=off."""
    if os.environ.get("GOOGLE_OFFLINE_AUDIENCE", "on").lower() == "off": return
    cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    if not cid: log("google audience :: skipped, no customer id"); return
    import hashlib
    try:
        at, hd = _gads_hdr()
        if not at: log("google audience :: skipped, no google creds"); return
        name = BUYERS_LIST_NAME
        base = "https://googleads.googleapis.com/v21/customers/%s" % cid
        d = http_json(base + "/googleAds:searchStream",
                      {"query": "SELECT user_list.id, user_list.name, user_list.resource_name FROM user_list"}, hd)
        rn = None
        for b2 in (d if isinstance(d, list) else [d]):
            for row in (b2.get("results") or []):
                ul = row.get("userList") or {}
                if ul.get("name") in BUYERS_LIST_ALIASES: rn = ul.get("resourceName")
        created = False
        if not rn:
            d = http_json(base + "/userLists:mutate",
                          {"operations": [{"create": {"name": name,
                            "description": BUYERS_LIST_DESC,
                            "membershipLifeSpan": 540,
                            "crmBasedUserList": {"uploadKeyType": "CONTACT_INFO"}}}]}, hd)
            try: rn = d["results"][0]["resourceName"]
            except Exception:
                log("google audience :: CREATE FAILED ::", str(d)[:220]); return
            created = True
            log("google audience :: created", rn)
        # v9.4: Ads API refuses Customer Match for this dev token (verified live) ->
        # Data Manager audienceMembers:ingest against the same user list.
        lid = rn.split("/")[-1]
        if not created:
            # v9.4.1: the list may exist but still be near-empty (it was created while
            # uploads were blocked). Google's own size number decides the backfill.
            try:
                d2 = http_json(base + "/googleAds:searchStream",
                               {"query": "SELECT user_list.size_for_display FROM user_list "
                                         "WHERE user_list.resource_name = '%s'" % rn}, hd)
                sz = 0
                for b3 in (d2 if isinstance(d2, list) else [d2]):
                    for row2 in (b3.get("results") or []):
                        sz = int((row2.get("userList") or {}).get("sizeForDisplay") or 0)
                if sz < 5000:
                    created = True
                    log("google audience :: list size", sz, "of ~59k offline buyers :: forcing the full 730d backfill")
            except Exception as e2:
                log("google audience :: size check failed ::", str(e2)[:120])
        contacts = _offline_contacts(_buyer_window(created), online=True)
        members = []
        for em, ph in contacts:
            ids = []
            if em: ids.append({"emailAddress": hashlib.sha256(em.encode()).hexdigest()})
            if ph: ids.append({"phoneNumber": hashlib.sha256(("+" + ph).encode()).hexdigest()})
            if ids: members.append({"userData": {"userIdentifiers": ids},
                                    "consent": {"adUserData": "CONSENT_GRANTED",
                                                "adPersonalization": "CONSENT_GRANTED"}})
        if not members: log("google audience :: nothing to send"); return
        sent = 0
        for i in range(0, len(members), 5000):
            d = http_json(_DM + "/audienceMembers:ingest",
                          {"destinations": [_dm_dest(lid)], "encoding": "HEX",
                           "termsOfService": {"customerMatchTermsOfServiceStatus": "ACCEPTED"},
                           "audienceMembers": members[i:i + 5000]}, hd)
            if d.get("error"):
                if _dm_scope_block(d):
                    log("google audience :: RE-AUTH NEEDED -- the Google token lacks the Data Manager "
                        "scope. Run: bash google_reauth.sh (one time), then bash wire_ourkids.sh.")
                else:
                    log("google audience :: DM ingest failed ::", str(d.get("error"))[:600])
                return
            sent += len(members[i:i + 5000])
        log("google audience ::", rn, ":: window", 730 if created else 4, "d :: hashed members sent via "
            "Data Manager", sent, "(first full backfill)" if created else "")
    except Exception as e:
        log("google audience :: fail ::", str(e)[:180])

def _sync_meta_list(tok, acct, name, aliases, desc, online, tag):
    """Keep ONE Meta Customer List fresh. Emails/phones come from Odoo (read-only), are
    normalised and SHA256-HASHED before anything leaves this job. Raw PII is never logged
    and never written to the repo. Idempotent: finds the list by name (old names too, so a
    rename never spawns a duplicate); creates it once; each 3h run tops up the last few
    days, a fresh list gets the full backfill."""
    import hashlib
    aud = None; created = False
    d = http_json("%s/%s/customaudiences?%s" % (GRAPH, acct, urllib.parse.urlencode(
        {"fields": "id,name", "limit": 500, "access_token": tok})))
    for r in (d.get("data") or []):
        if r.get("name") in aliases: aud = r["id"]; break
    if not aud:
        d = http_json("%s/%s/customaudiences?%s" % (GRAPH, acct, urllib.parse.urlencode(
            {"name": name, "subtype": "CUSTOM", "customer_file_source": "USER_PROVIDED_ONLY",
             "description": desc, "access_token": tok})), data={})
        aud = d.get("id")
        if not aud:
            log(tag, ":: CREATE FAILED ::", str(d)[:200],
                ":: likely the Custom Audience ToS -- accept it once in Ads Manager > Audiences, then this runs itself")
            return
        created = True
        log(tag, ":: created", aud)
    days = _buyer_window(created)
    contacts = _offline_contacts(days, online=online)
    rowsn = []
    for em, ph in contacts:
        he = hashlib.sha256(em.encode()).hexdigest() if em else ""
        hp = hashlib.sha256(ph.encode()).hexdigest() if ph else ""
        if he or hp: rowsn.append([he, hp])
    sent = 0
    for i in range(0, len(rowsn), 5000):
        d = http_json("%s/%s/users" % (GRAPH, aud),
                      data={"payload": {"schema": ["EMAIL_SHA256", "PHONE_SHA256"],
                                        "data": rowsn[i:i + 5000]}, "access_token": tok})
        if d.get("error"):
            log(tag, ":: push failed ::", str(d.get("error"))[:180]); break
        sent += len(rowsn[i:i + 5000])
    log(tag, "::", aud, ":: window", days, "d :: customers", len(contacts),
        ":: hashed rows sent", sent, "(first full backfill)" if created else "")

def sync_offline_audience(tok):
    """Keep the Meta customer list of everyone who ever bought (online + in-store) fresh.
    In-store buyers ALSO reach Meta directly as offline CAPI Purchase events, which is what
    the "offline buyers from ads" rule audience is built on -- this list adds the history
    and the unmatched people the dataset never saw. Kill switch: OFFLINE_AUDIENCE=off."""
    if not tok or os.environ.get("OFFLINE_AUDIENCE", "on").lower() == "off": return
    acct = meta_accounts(tok)[0]
    _sync_meta_list(tok, acct, BUYERS_LIST_NAME, BUYERS_LIST_ALIASES, BUYERS_LIST_DESC,
                    True, "all-buyers audience")
    # v9.8: NO second Odoo-fed branch list. Meta already holds the in-store buyers -- the
    # CAPI job sends them hashed into the dataset -- so "offline buyers from ads" is a
    # rule audience over that dataset (Purchase + action_source=physical_store), which
    # Meta refreshes itself. Re-uploading the same people from Odoo would be duplicate
    # work, and the name would collide with that audience on lookup.

def merge_fallback(win, shop, goog, tik, meta):
    """If a live source returned nothing for a day, use the committed fallback pull."""
    p = os.path.join(DOCS, "fallback_ad.json")
    if not os.path.exists(p): return
    fb = json.load(open(p)); fdx = {d: i for i, d in enumerate(fb.get("dates", []))}
    def fill(dct, k, fk):
        for d in win:
            if d in fdx and not dct[k].get(d):
                dct[k][d] = fb.get(fk, [0] * len(fdx))[fdx[d]]
    for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]: fill(shop, k, k)
    fill(goog, "gspend", "gspend"); fill(goog, "gecomrev", "gecomrev"); fill(goog, "gconv", "gconv")
    fill(tik, "tspend", "tspend"); fill(tik, "ttValue", "ttValue"); fill(tik, "tpur", "tpur"); fill(tik, "ttOffValue", "ttOffValue"); fill(tik, "ttOffPur", "ttOffPur")
    for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "mpur"]: fill(meta, k, k)
    log("fallback merged")

# ------------------------------------------------------------------ ANNOTATIONS
EVENTS = [("2024-11-29", "Black Friday"), ("2025-02-28", "Ramadan starts"),
          ("2025-03-30", "Eid al-Fitr"), ("2025-06-06", "Eid al-Adha"),
          ("2025-08-01", "Back-to-school season"), ("2025-11-28", "Black Friday"),
          ("2026-02-18", "Ramadan starts"), ("2026-03-20", "Eid al-Fitr"),
          ("2026-05-27", "Eid al-Adha")]
def annotations(fin):
    ds = drange(datetime.date.fromisoformat(fin["start"]), END)
    rev, ref = fin["rev"], fin["refund"]
    ann = [{"d": d, "t": t, "k": "event"} for d, t in EVENTS if fin["start"] <= d <= ds[-1]]
    spikes, drops = [], []
    for i in range(28, len(rev)):
        base = sum(rev[i - 28:i]) / 28.0
        if base < 30: continue
        if rev[i] >= 2.0 * base:
            spikes.append((rev[i] / base, i))
        elif rev[i] <= 0.4 * base and rev[i] >= 0:
            drops.append((rev[i] / base, i))
    for _, i in sorted(spikes, reverse=True)[:6]:
        base = int(sum(rev[i - 28:i]) / 28.0)
        ann.append({"d": ds[i], "t": "Sales spike: E£ %s vs E£ %s daily average" % ("{:,}".format(rev[i] * 1000), "{:,}".format(base * 1000)), "k": "spike"})
    for _, i in sorted(drops)[:4]:
        base = int(sum(rev[i - 28:i]) / 28.0)
        ann.append({"d": ds[i], "t": "Sales drop: E£ %s vs E£ %s daily average" % ("{:,}".format(rev[i] * 1000), "{:,}".format(base * 1000)), "k": "drop"})
    nz = [x for x in ref if x > 0]
    if nz:
        med = statistics.median(nz)
        big = sorted(((ref[i], i) for i in range(len(ref)) if ref[i] > 5 * med and ref[i] > 100), reverse=True)[:4]
        for v, i in big:
            ann.append({"d": ds[i], "t": "Refund spike: E£ %s in credit notes posted" % "{:,}".format(v * 1000), "k": "refund"})
    ann.sort(key=lambda a: a["d"])
    log("annotations", len(ann))
    return ann

# ------------------------------------------------------------------ BUILD
ATTR = {"order": ["default", "7dc", "1dc", "incr"],
        "labels": {"default": "Default · Meta 7d-click / 1d-view (LIVE)", "7dc": "7-day click (modeled)",
                   "1dc": "1-day click (modeled)", "incr": "Incremental \u2014 resolved at build time"},
        "meta": {"default": 1.0, "7dc": 0.94, "1dc": 0.78, "incr": 0.6},
        "google": {"default": 1.0, "7dc": 1.0, "1dc": 0.85, "incr": 0.68},
        "tt": {"default": 1.0, "7dc": 0.96, "1dc": 0.8, "incr": 0.55}}
SRC = {"revenue": "Odoo sale.order (state sale/done), amount_total, all 4 online channels, by date_order. GROSS.",
       "refund": "Odoo account.move out_refund, posted, by invoice_date. Returns/credit notes.",
       "netrev": "Gross Odoo revenue minus posted credit notes.",
       "gp": "Odoo sale.order margin (revenue minus cost).",
       "orders": "Odoo confirmed-order count.", "sessions": "Shopify online store sessions (ShopifyQL).",
       "cvr": "Shopify completed checkouts / sessions.", "aov": "Revenue / orders.",
       "spend": "Meta amount_spent + Google Ads cost + TikTok spend.",
       "broas": "(Google + Meta pixel + TikTok value) / spend. Attribution per toggle.",
       "mer": "Net Odoo revenue / total ad spend.", "cac": "Total ad spend / new customers.",
       "gpp": "Odoo margin / orders.", "cacgp": "CAC / GP-per-order.",
       "instoreMeta": "Meta's own offline_conversion.purchase VALUE from the in-store CAPI feed "
                      "(action_values). Not omni minus pixel \u2014 that subtraction also swept in "
                      "on-Meta (Shops) and app purchases, which are not store sales.",
       "instoreOnsite": "The residual: omni value minus pixel minus offline. On-Meta checkout "
                        "and in-app purchases. Reported separately so it is not counted as store revenue.",
       "channels": "Odoo sale.order revenue by crm.team, daily.",
       "branch": "Odoo accounting: income lines on each branch's POS sales journals, daily. This is REAL retail revenue.",
       "ins": "Computed automatically from Odoo daily sales, refunds, margin and the ads feed for the selected range."}

# ------------------------------------------------------------------ VENDOR / PRODUCT LTV
VC = {}          # vendor code -> customer economics, filled by pull_pos_customers
_TVC = [None]    # cached product_tmpl_id -> (vendor code, category, Cash/Consignment)
_PPC = [None]    # cached product_id -> product_tmpl_id
_VNC = [None]    # cached vendor code -> supplier name


def _page(model, domain, fields, size=10000, cap=400000):
    """Page a search_read in id order until exhausted."""
    out = []; off = 0
    while off < cap:
        pg = oexec(model, "search_read", [domain], {"fields": fields, "limit": size, "offset": off, "order": "id"})
        if not pg: break
        out += pg; off += len(pg)
        if len(pg) < size: break
    return out


def tmpl_vendor_map():
    """product_tmpl_id -> (vendor code, product category, Cash|Consignment|Service).
    vendor_num is the supplier code carried on every product template; it joins to res.partner.ref."""
    if _TVC[0] is not None: return _TVC[0]
    m = {}
    try:
        for r in _page("product.template", [], ["vendor_num", "categ_id", "x_studio_category_type"], 10000, 200000):
            v = (r.get("vendor_num") or "").strip()
            m[r["id"]] = (v, (r.get("categ_id") or [0, ""])[1], r.get("x_studio_category_type") or "")
        log("vendor map", len(m), "templates", sum(1 for x in m.values() if x[0]), "with a vendor code")
    except Exception as e:
        log("vendor map fail", str(e)[:160])
    _TVC[0] = m
    return m


def vendor_names():
    """vendor code -> supplier display name, from res.partner.ref."""
    if _VNC[0] is not None: return _VNC[0]
    n = {}
    try:
        for r in _page("res.partner", [["ref", "!=", False]], ["ref", "name"], 5000, 100000):
            k = (r.get("ref") or "").strip()
            if k and k not in n: n[k] = r.get("name") or k
    except Exception as e:
        log("vendor names fail", str(e)[:160])
    _VNC[0] = n
    return n


def prod_tmpl_map():
    """product_id -> product_tmpl_id (needed because sale.order.line cannot be grouped by template)."""
    if _PPC[0] is not None: return _PPC[0]
    m = {}
    try:
        for r in _page("product.product", [], ["product_tmpl_id"], 10000, 200000):
            m[r["id"]] = (r.get("product_tmpl_id") or [0])[0]
    except Exception as e:
        log("product map fail", str(e)[:160])
    _PPC[0] = m
    return m


def pos_cfg_branches():
    """POS config ids grouped by branch, read live so new tills map themselves."""
    out = {}
    try:
        for r in oexec("report.pos.order", "read_group", [[["date", ">=", "2024-08-01"]], ["price_total:sum"], ["config_id"]], {"lazy": False, "limit": 500}):
            c = r.get("config_id")
            if not c: continue
            br = next((b for k, b in POS_CFG if k in c[1]), None)
            if br: out.setdefault(br, []).append(c[0])
    except Exception as e:
        log("pos cfg fail", str(e)[:160])
    return out


def pull_vendor_inventory():
    """Stock on hand per vendor: units, cost value (qty x standard_price) and retail
    value (qty x list_price), from product.template.qty_available joined on vendor_num.
    Keyed by vendor code to join the vend rows (r['v'])."""
    inv = {}
    try:
        for r in _page("product.template", [["qty_available", ">", 0]],
                       ["vendor_num", "qty_available", "standard_price", "list_price"], 5000, 300000):
            v = (r.get("vendor_num") or "").strip()
            if not v:
                continue
            q = r.get("qty_available") or 0
            sp = r.get("standard_price") or 0
            lp = r.get("list_price") or 0
            d = inv.setdefault(v, {"u": 0.0, "c": 0.0, "rt": 0.0, "sk": 0})
            d["u"] += q
            d["c"] += q * sp
            d["rt"] += q * lp
            d["sk"] += 1
    except Exception as e:
        log("vendor inventory failed", type(e).__name__, str(e)[:120])
        return {}
    for v in inv:
        inv[v]["u"] = round(inv[v]["u"])
        inv[v]["c"] = round(inv[v]["c"])
        inv[v]["rt"] = round(inv[v]["rt"])
    log("vendor inventory", len(inv), "vendors with stock on hand")
    return inv


def pull_vendors():
    """Vendor and product economics across every branch and every online channel.

    Retail comes from report.pos.order grouped by product template, once per branch, so the
    branch split is an actual till-level sum and never an allocation. Online comes from
    sale.order.line on the Shopify / Noon / Amazon / Homzmart teams. Both are joined to the
    supplier through product.template.vendor_num -> res.partner.ref. Customer counts, lifetime
    gross profit and repeat rate per vendor are carried in from the POS customer crawl."""
    TV = tmpl_vendor_map()
    if not TV: return {}, {}
    NM = vendor_names()
    ven = {}; tmpl = {}
    def V(code):
        return ven.setdefault(code, {"v": code, "n": NM.get(code, code), "r": 0.0, "g": 0.0, "q": 0.0,
                                     "orev": 0.0, "ogp": 0.0, "oq": 0.0, "br": {}, "r90": 0.0, "p90": 0.0,
                                     "cash": 0.0, "cons": 0.0, "cat": {}})
    def T(tid):
        vt = TV.get(tid, ("", "", ""))
        return tmpl.setdefault(tid, {"t": tid, "n": "", "v": vt[0], "cat": vt[1], "ct": vt[2],
                                     "r": 0.0, "g": 0.0, "q": 0.0, "orev": 0.0, "ogp": 0.0})

    # ---- retail, one grouped call per branch ----
    BC = pos_cfg_branches()
    for br, ids in BC.items():
        try:
            g = oexec("report.pos.order", "read_group",
                      [[["date", ">=", "2024-08-01"], ["config_id", "in", ids]],
                       ["price_total:sum", "margin:sum", "product_qty:sum"], ["product_tmpl_id"]],
                      {"lazy": False, "limit": 200000})
        except Exception as e:
            log("vendor retail fail", br, str(e)[:120]); continue
        for r in g:
            t = r.get("product_tmpl_id")
            if not t: continue
            rv = float(r.get("price_total") or 0); gp = float(r.get("margin") or 0); qt = float(r.get("product_qty") or 0)
            row = T(t[0]); row["n"] = row["n"] or str(t[1]).strip()[:52]
            row["r"] += rv; row["g"] += gp; row["q"] += qt
            code = TV.get(t[0], ("", "", ""))[0]
            if not code: continue
            d = V(code)
            d["r"] += rv; d["g"] += gp; d["q"] += qt
            b = d["br"].setdefault(br, [0.0, 0.0]); b[0] += rv; b[1] += gp
            ct = TV[t[0]][2]
            if ct == "Consignment": d["cons"] += rv
            else: d["cash"] += rv
            cg = TV[t[0]][1]
            if cg: d["cat"][cg] = d["cat"].get(cg, 0.0) + rv
        log("vendor retail", br, len(g), "templates")

    # ---- momentum: last 90 days vs the 90 before it ----
    for key, a, b in (("r90", END - datetime.timedelta(days=89), END),
                      ("p90", END - datetime.timedelta(days=179), END - datetime.timedelta(days=90))):
        try:
            g = oexec("report.pos.order", "read_group",
                      [[["date", ">=", a.isoformat()], ["date", "<=", b.isoformat() + " 23:59:59"]],
                       ["price_total:sum"], ["product_tmpl_id"]], {"lazy": False, "limit": 200000})
        except Exception as e:
            log("vendor window fail", key, str(e)[:120]); continue
        for r in g:
            t = r.get("product_tmpl_id")
            if not t: continue
            code = TV.get(t[0], ("", "", ""))[0]
            if code: V(code)[key] += float(r.get("price_total") or 0)

    # ---- online, grouped by variant then folded up to the template ----
    PP = prod_tmpl_map()
    try:
        g = oexec("sale.order.line", "read_group",
                  [[["order_id.state", "in", ["sale", "done"]], ["display_type", "=", False],
                    ["order_id.team_id.name", "in", ["Shopify", "Noon", "Amazon", "Homzmart"]],
                    ["order_id.date_order", ">=", "2024-08-01 00:00:00"]],
                   ["price_subtotal:sum", "margin:sum", "product_uom_qty:sum"], ["product_id"]],
                  {"lazy": False, "limit": 200000})
    except Exception as e:
        log("vendor online fail", str(e)[:160]); g = []
    for r in g:
        p = r.get("product_id")
        if not p: continue
        tid = PP.get(p[0])
        if not tid: continue
        rv = float(r.get("price_subtotal") or 0); gp = float(r.get("margin") or 0); qt = float(r.get("product_uom_qty") or 0)
        nm = str(p[1]).strip()[:52]
        if "discount" in nm.lower() or "shipping" in nm.lower(): continue
        row = T(tid); row["n"] = row["n"] or nm
        row["orev"] += rv; row["ogp"] += gp
        code = TV.get(tid, ("", "", ""))[0]
        if not code: continue
        d = V(code); d["orev"] += rv; d["ogp"] += gp; d["oq"] += qt
    log("vendor online", len(g), "variants")

    # ---- fold in customer economics and emit ----
    net = VC.get("__net__", {})
    rows = []
    for code, d in ven.items():
        tot = d["r"] + d["orev"]
        if tot < 20000: continue
        c = VC.get(code, {})
        cat = max(d["cat"].items(), key=lambda x: x[1])[0] if d["cat"] else ""
        rows.append({"v": code, "n": d["n"][:44],
                     "r": round(d["r"]), "g": round(d["g"]), "q": round(d["q"]),
                     "orev": round(d["orev"]), "ogp": round(d["ogp"]), "oq": round(d["oq"]),
                     "br": {b: [round(x[0]), round(x[1])] for b, x in d["br"].items()},
                     "r90": round(d["r90"]), "p90": round(d["p90"]),
                     "cash": round(d["cash"]), "cons": round(d["cons"]), "cat": cat,
                     "cust": c.get("cust", 0), "acq": c.get("acq", 0),
                     "ltgp": c.get("ltgp", 0), "rep": c.get("rep", 0), "opc": c.get("opc", 0)})
    rows.sort(key=lambda x: -(x["r"] + x["orev"]))
    rows = rows[:160]
    prows = sorted(tmpl.values(), key=lambda x: -(x["r"] + x["orev"]))[:200]
    prods = [{"n": (p["n"] or str(p["t"]))[:52], "t": p["t"], "v": p["v"], "vn": NM.get(p["v"], p["v"])[:36],
              "cat": p["cat"][:34], "ct": p["ct"],
              "r": round(p["r"]), "g": round(p["g"]), "q": round(p["q"]),
              "orev": round(p["orev"]), "ogp": round(p["ogp"])} for p in prows]
    covR = sum(d["r"] + d["orev"] for d in ven.values())
    allR = sum(t["r"] + t["orev"] for t in tmpl.values())
    vend = {"rows": rows, "net": net, "branches": sorted(BC.keys()),
            "cov": {"tmpl": len(TV), "tmplVend": sum(1 for x in TV.values() if x[0]),
                    "vend": len(ven), "shown": len(rows),
                    "revPct": round(covR / allR * 100, 1) if allR else 0,
                    "rev": round(allR)},
            "start": "2024-08-01", "end": END.isoformat()}
    log("vendors", len(ven), "shown", len(rows), "products", len(prods), "coverage", vend["cov"]["revPct"], "%")
    return vend, {"rows": prods, "start": "2024-08-01", "end": END.isoformat()}


# ---------------------------------------------------------------- SHIPPING & HANDLING ECONOMICS  (v6.3)
# Three separate money flows that everyone lumps together as "shipping", and which net
# out to a number nobody at OurKids had ever seen on one line:
#   1. what the customer PAYS US for delivery   -- sale.order.line on the shipping SKUs
#   2. what we PAY the couriers                 -- 31.01.08.08 Cargo / .09 Transportation
#   3. what the courier/gateway SKIMS on collection -- 31.01.08.31..37 + .17 card + .12 bank
#   4. what we get BACK as negotiated rebate    -- 32.00.00.18 Earned Shipping Discount
# "Free shipping" is not free: it is flow 2+3 with flow 1 set to zero. Netting them by
# month is the only way to see what the free-shipping promise actually costs.
SHIP_ACC = {
    "31.01.08.08.00": ("Courier & cargo paid",       "paid"),
    "31.01.08.09.00": ("Transportation paid",        "paid"),
    "31.01.08.33.00": ("Aramex collection fees",     "coll"),
    "31.01.08.34.00": ("Bosta collection fees",      "coll"),
    "31.01.08.31.00": ("Noon collection fees",       "coll"),
    "31.01.08.32.00": ("Amazon collection fees",     "coll"),
    "31.01.08.35.00": ("PayMob collection fees",     "coll"),
    "31.01.08.36.00": ("Symbel collection fees",     "coll"),
    "31.01.08.37.00": ("Fawry collection fees",      "coll"),
    "31.01.08.17.00": ("Credit card fees",           "hand"),
    "31.01.08.12.00": ("Bank charges",               "hand"),
    "31.01.08.10.00": ("Packing, wrapping & bags",   "hand"),
    "32.00.00.18.00": ("Earned shipping discount",   "gain"),
}

def pull_shipping():
    """Shipping P&L by month: collected from customers, paid to couriers, collection and
    handling fees skimmed on the way, and rebates earned back. All ACTUAL Odoo postings."""
    out = {"rev": {}, "acc": {}, "grp": {}, "n": {}, "err": ""}
    try:
        prods = oexec("product.product", "search_read",
                      [["|", ["default_code", "=", "shopifyshippingproduct"],
                             ["name", "in", ["Bosta Delivery", "POS SHIPPING"]]]],
                      {"fields": ["name", "default_code"], "limit": 50})
        pids = [p["id"] for p in prods]
        pnm = {}
        for p in prods:
            nm = "Shopify shipping charged" if p.get("default_code") == "shopifyshippingproduct" else str(p["name"])[:40]
            pnm[p["id"]] = nm
        # read_group cannot group on a related date, so walk month by month. ~24 cheap calls.
        m0 = datetime.date.fromisoformat("2024-08-01")
        while m0 <= END:
            nxt = (m0.replace(day=28) + datetime.timedelta(days=6)).replace(day=1)
            mon = m0.strftime("%Y-%m")
            if pids:
                for r in ogroup("sale.order.line",
                                [["order_id.state", "in", ["sale", "done"]],
                                 ["product_id", "in", pids],
                                 ["order_id.date_order", ">=", m0.isoformat() + " 00:00:00"],
                                 ["order_id.date_order", "<", nxt.isoformat() + " 00:00:00"]],
                                ["price_subtotal"], ["product_id"]):
                    nm = pnm.get((r.get("product_id") or [0])[0], "Shipping charged")
                    out["rev"].setdefault(nm, {})
                    out["rev"][nm][mon] = out["rev"][nm].get(mon, 0) + round(r.get("price_subtotal") or 0)
                    out["n"][mon] = out["n"].get(mon, 0) + int(r.get("__count") or 0)
            m0 = nxt
        acc = oexec("account.account", "search_read", [[["code", "in", list(SHIP_ACC.keys())]]],
                    {"fields": ["code"], "limit": 60})
        amap = {a["id"]: a["code"] for a in acc}
        if amap:
            g = oexec("account.move.line", "read_group",
                      [[["account_id", "in", list(amap.keys())], ["parent_state", "=", "posted"],
                        ["date", ">=", "2024-08-01"], ["date", "<=", END.isoformat()]],
                       ["balance"], ["account_id", "date:month"]], {"lazy": False})
            for r in g:
                code = amap.get(r["account_id"][0], "")
                label, bucket = SHIP_ACC.get(code, (code, "hand"))
                try: mon = datetime.datetime.strptime(str(r["date:month"]), "%B %Y").strftime("%Y-%m")
                except Exception: continue
                # expense accounts post debit-positive; the gain account posts credit-negative.
                # Flip the gain so every number on this card reads as "money moving in our favour".
                v = round(r["balance"]) * (-1 if bucket == "gain" else 1)
                out["acc"].setdefault(label, {})
                out["acc"][label][mon] = out["acc"][label].get(mon, 0) + v
                out["grp"].setdefault(bucket, {})
                out["grp"][bucket][mon] = out["grp"][bucket].get(mon, 0) + v
        for nm, mm in out["rev"].items():
            for mon, v in mm.items():
                out["grp"].setdefault("rev", {})
                out["grp"]["rev"][mon] = out["grp"]["rev"].get(mon, 0) + v
        log("shipping", "rev skus", len(out["rev"]), "accounts", len(out["acc"]),
            "months", len(out["grp"].get("rev", {})))
    except Exception as e:
        out["err"] = str(e)[:200]; log("shipping fail", str(e)[:200])
    return out


# ---------------------------------------------------------------- SHIPPING OUTCOMES  (v7.6)
# The question this answers: what does the free-delivery promise actually cost, month by
# month, against what customers actually pay for delivery -- and how do cancelled, returned
# and delivered orders sit inside that.
#
# Two facts had to be dug out of the ledger before any of this was true:
#
#  1. BOSTA COST LIVES IN TWO ACCOUNTS, NOT ONE. From Jan 2026 it is posted to
#     31.01.08.34.00 "Bosta collection fees". BEFORE Jan 2026 the identical postings were
#     coded to 31.01.08.17.00 "Credit Card Expenses" -- 3.5m EGP of courier cost sitting in
#     an account named after card fees. They are identifiable because accounting writes the
#     memo in Arabic as "عموله بوسطه عن تحصيله <date>". Taking only the memo-matched lines
#     out of .17 and adding them to .34 gives one continuous cost series from Aug 2024.
#     Without this the dashboard showed ZERO courier cost for the first 17 months.
#
#  2. "EARNED SHIPPING DISCOUNT" (32.00.00.18.00) IS NOT A REBATE. It is the ledger side of
#     the same delivery money the customer already paid on the shipping SKU -- the two
#     series track within 3% every single month. The old card added it as a separate gain,
#     which double-counted every pound of shipping revenue. It is kept here only as a
#     cross-check (colAcc) and is never added to anything.
#
# Order outcomes come from sale.order on the four online teams:
#     cancelled  = state cancel            -- never shipped, costs nothing
#     returned   = state sale/done AND is_return_total  -- shipped and came back
#     kept       = state sale/done AND NOT is_return_total
# CAVEAT CARRIED IN THE DATA: before Jun 2025 the house convention booked a return as a
# cancellation, so "returned" reads ~0 until then. retFrom marks where the flag became real.
SHIP2_TEAMS = ["Shopify", "Noon", "Amazon", "Homzmart"]
SHIP2_MEMO = "\u0628\u0648\u0633\u0637\u0647"   # "Bosta" as accounting spells it in the memo.
# Returns booked as cancellations before this month (house convention change).
SHIP2_RETFROM = "2025-06"
# Matched on the courier name ALONE, not on the full phrase: the phrasing of the memo drifted
# over time ("عموله بوسطه عن تحصيله" vs other wordings), and a two-word pattern silently
# dropped Feb 2025 entirely -- 83,876 EGP of real cost reported as zero. The single word is
# safe here because it is only ever applied inside 31.01.08.17.00, where every memo-matched
# line reconciles to the Bosta settlement batches.
_MONNAME = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}

def _monkey(v):
    """Odoo read_group returns 'March 2026'; we want '2026-03'."""
    try:
        a, b = str(v).split()
        return "%s-%02d" % (b, _MONNAME[a])
    except Exception:
        return None

def pull_ship2():
    out = {"m": [], "conf": [], "kept": [], "ret": [], "canc": [],
           "keptV": [], "retV": [], "cancV": [], "paidN": [], "freeN": [],
           "col": [], "colAcc": [], "cost": [], "costN": [],
           "start": "2024-08", "retFrom": "2025-06", "err": ""}
    try:
        m0 = datetime.date(2024, 8, 1)
        mons = []
        while m0 <= END:
            mons.append(m0.strftime("%Y-%m"))
            m0 = (m0.replace(day=28) + datetime.timedelta(days=6)).replace(day=1)
        idx = {m: i for i, m in enumerate(mons)}
        Z = lambda: [0] * len(mons)
        kept, ret, canc = Z(), Z(), Z()
        keptV, retV, cancV = Z(), Z(), Z()
        paidN, col, colAcc, cost, costN = Z(), Z(), Z(), Z(), Z()

        # ---- order outcomes: ONE call, grouped three ways at once
        g = oexec("sale.order", "read_group",
                  [[["team_id.name", "in", SHIP2_TEAMS],
                    ["date_order", ">=", "2024-08-01 00:00:00"]],
                   ["amount_total"], ["date_order:month", "state", "is_return_total"]],
                  {"lazy": False})
        for r in g:
            mo = _monkey(r.get("date_order:month"))
            if mo not in idx: continue
            i = idx[mo]; n = int(r.get("__count") or 0); v = round(r.get("amount_total") or 0)
            st = r.get("state")
            if st == "cancel":
                canc[i] += n; cancV[i] += v
            elif st in ("sale", "done"):
                if r.get("is_return_total"): ret[i] += n; retV[i] += v
                else: kept[i] += n; keptV[i] += v

        # ---- what the customer actually paid us for delivery, per month
        prods = oexec("product.product", "search_read",
                      [["|", ["default_code", "=", "shopifyshippingproduct"],
                             ["name", "in", ["Bosta Delivery", "POS SHIPPING"]]]],
                      {"fields": ["name"], "limit": 50})
        pids = [p["id"] for p in prods]
        if pids:
            d0 = datetime.date(2024, 8, 1)
            while d0 <= END:
                nx = (d0.replace(day=28) + datetime.timedelta(days=6)).replace(day=1)
                mo = d0.strftime("%Y-%m")
                rg = ogroup("sale.order.line",
                            [["order_id.state", "in", ["sale", "done"]],
                             ["order_id.team_id.name", "in", SHIP2_TEAMS],
                             ["product_id", "in", pids], ["price_subtotal", ">", 0],
                             ["order_id.date_order", ">=", d0.isoformat() + " 00:00:00"],
                             ["order_id.date_order", "<", nx.isoformat() + " 00:00:00"]],
                            ["price_subtotal"], [])
                if rg and mo in idx:
                    paidN[idx[mo]] = int(rg[0].get("__count") or 0)
                    col[idx[mo]] = round(rg[0].get("price_subtotal") or 0)
                d0 = nx

        # ---- ledger cross-check on collected (never added, only compared)
        a18 = oexec("account.account", "search_read", [[["code", "=", "32.00.00.18.00"]]],
                    {"fields": ["id"], "limit": 1})
        if a18:
            for r in oexec("account.move.line", "read_group",
                           [[["account_id", "=", a18[0]["id"]], ["parent_state", "=", "posted"],
                             ["date", ">=", "2024-08-01"]], ["balance"], ["date:month"]],
                           {"lazy": False}):
                mo = _monkey(r.get("date:month"))
                if mo in idx: colAcc[idx[mo]] = -round(r.get("balance") or 0)

        # ---- the real courier bill: account .34 in full, plus the memo-matched lines in .17
        acc = oexec("account.account", "search_read",
                    [[["code", "in", ["31.01.08.34.00", "31.01.08.17.00"]]]],
                    {"fields": ["code"], "limit": 5})
        amap = {a["id"]: a["code"] for a in acc}
        for aid, code in amap.items():
            dom = [["account_id", "=", aid], ["parent_state", "=", "posted"],
                   ["date", ">=", "2024-08-01"]]
            if code == "31.01.08.17.00":
                dom.append(["name", "ilike", SHIP2_MEMO])
            for r in oexec("account.move.line", "read_group",
                           [dom, ["debit"], ["date:month"]], {"lazy": False}):
                mo = _monkey(r.get("date:month"))
                if mo in idx:
                    cost[idx[mo]] += round(r.get("debit") or 0)
                    costN[idx[mo]] += int(r.get("__count") or 0)

        conf = [kept[i] + ret[i] for i in range(len(mons))]
        freeN = [max(conf[i] - paidN[i], 0) for i in range(len(mons))]
        out.update({"m": mons, "conf": conf, "kept": kept, "ret": ret, "canc": canc,
                    "keptV": keptV, "retV": retV, "cancV": cancV,
                    "paidN": paidN, "freeN": freeN, "col": col, "colAcc": colAcc,
                    "cost": cost, "costN": costN,
                    "lagM": mons[-1] if mons else "", "retFrom": SHIP2_RETFROM})
        log("ship2", len(mons), "months, collected", sum(col), "cost", sum(cost),
            "net", sum(col) - sum(cost))
    except Exception as e:
        out["err"] = str(e)[:200]; log("ship2 fail", str(e)[:200])
    return out


# ---------------------------------------------------------------- SALARY DETAIL  (v6.3)
# "who took what" -- every posting on the 31.01.01.* payroll accounts, not a monthly total.
# The hard part: partner_id is EMPTY on these lines, so the payee does not exist as a field.
# What does exist is the Arabic memo, which is written to a house convention:
#     "<accountant who paid>: رواتب <department or branch> من الخزنه"
#     "<accountant who paid>: فرق مرتب <person name> فرع <branch>"
# So the payee/department has to be parsed out of the memo, and the branch cross-checked
# against the analytic distribution. Both are emitted, and every raw line is shipped so
# nothing is taken on trust -- the table on screen IS the ledger.
SAL_STRIP = ["رواتب", "مرتبات", "فرق مرتب",
             "فرق مرتبات", "مرتب", "من الخزنه",
             "من الخزينه", "من الخزنة"]

def _sal_who(memo):
    """Strip the paying accountant's name and the boilerplate, leaving the thing that was paid for."""
    s = str(memo or "").strip()
    if ":" in s: s = s.split(":", 1)[1]
    for w in SAL_STRIP: s = s.replace(w, " ")
    s = " ".join(s.split())
    return s[:60] or "(no description)"

def pull_salaries():
    """Every payroll posting since Aug-2024, line by line, with branch and payee resolved."""
    out = {"acc": [], "br": [], "who": [], "rows": [], "mon": {}, "err": ""}
    try:
        acc = oexec("account.account", "search_read", [[["code", "=like", "31.01.01.%"]]],
                    {"fields": ["code", "name"], "limit": 40})
        amap = {a["id"]: str(a["name"]) for a in acc}
        if not amap: raise RuntimeError("no 31.01.01.* accounts found")
        lines = []
        off = 0
        while True:
            page = oexec("account.move.line", "search_read",
                         [[["account_id", "in", list(amap.keys())], ["parent_state", "=", "posted"],
                           ["date", ">=", "2024-08-01"], ["date", "<=", END.isoformat()]]],
                         {"fields": ["account_id", "balance", "date", "name", "analytic_distribution", "move_id"],
                          "limit": 2000, "offset": off, "order": "date asc"})
            lines += page
            if len(page) < 2000: break
            off += 2000
            if off > 40000: break
        accs = sorted(set(amap.values()))
        ai = {n: i for i, n in enumerate(accs)}
        brs = sorted(set(ANA_BR.values())) + ["HQ / unallocated"]
        bi = {n: i for i, n in enumerate(brs)}
        widx = {}
        for l in lines:
            memo = str(l.get("name") or "")
            an = amap.get(l["account_id"][0], "?")
            mon = str(l["date"])[:7]
            # branch: analytic distribution first (it is structured data), memo keyword second
            br = None
            for aid, pct in (l.get("analytic_distribution") or {}).items():
                b = ANA_BR.get(str(aid))
                if b: br = b; break
            if not br:
                for kw, b in BRANCH_AR:
                    if kw in memo: br = b; break
            if not br: br = "HQ / unallocated"
            who = _sal_who(memo)[:70]
            # The payee label is interned. Arabic survives json as \uXXXX escapes at six
            # bytes a character, so shipping 4,800 raw memos would have added ~900KB to a
            # 290KB payload. One shared table of ~1,800 labels plus an index per row costs
            # a fraction of that and loses nothing -- every line still names its payee.
            if who not in widx:
                widx[who] = len(out["who"]); out["who"].append(who)
            amt = round(l.get("balance") or 0)
            out["rows"].append([str(l["date"]), ai[an], bi[br], amt, widx[who]])
            out["mon"].setdefault(mon, {})
            out["mon"][mon][an] = out["mon"][mon].get(an, 0) + amt
        out["acc"] = accs; out["br"] = brs
        out["rows"].sort(key=lambda r: r[0])
        log("salaries", len(out["rows"]), "lines", len(out["who"]), "payees", len(out["mon"]), "months")
    except Exception as e:
        out["err"] = str(e)[:200]; log("salaries fail", str(e)[:200])
    return out


def build():
    ts = datetime.datetime.now(CAIRO).strftime("%Y-%m-%d %H:%M Cairo")
    log("collector v9.7 (Meta buyer list = online + in-store, ever; POS row cap raised so backfills stop truncating)")
    win = drange(AD_START, END)
    def safe(fn, *a):
        try: return fn(*a)
        except Exception as e:
            log(fn.__name__, "FAILED", str(e)[:200]); return None
    fin = safe(pull_odoo)
    bl = safe(pull_branches)
    prod = safe(pull_products) or []
    meta = safe(pull_meta, win) or {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur", "instoreNC", "mimp", "mclk", "moffv", "instoreOnsite"]}
    shop = safe(pull_shopify, win) or {k: {d: 0.0 for d in win} for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]}
    goog = safe(pull_google, win) or {k: {d: 0.0 for d in win} for k in ["gspend", "gecomrev", "gconv", "gimp", "gclk"]}
    tik = safe(pull_tiktok, win) or {k: {d: 0.0 for d in win} for k in ["tspend", "ttValue", "tpur", "ttOffValue", "ttOffPur", "timp", "tclk"]}
    shc = safe(pull_shop_channel, [FIN_START]) or {"srev": {}, "sgp": {}, "sord": {}, "sref": {}}
    safe(pull_meta_reach, win, os.environ.get("META_ACCESS_TOKEN", "").strip())
    safe(pull_cvr_routing)
    _ch = safe(pull_cohorts) or {"coh": [], "nr": {}}
    coh, nrm = _ch.get("coh", []), _ch.get("nr", {})
    # v7.6: overwrite new/returning orders AND revenue with customer-level Odoo actuals.
    # ShopifyQL cannot split revenue by customer type at all, so this is the only honest
    # source; the ShopifyQL counts above are the fallback if Odoo is down.
    _nrd = _ch.get("nrd") or {}
    _nrdOK = 0
    for _d in win:
        _c = _nrd.get(_d)
        if not _c: continue
        _nrdOK += 1
        shop["newcust"][_d] = _c["nord"]; shop["retcust"][_d] = _c["rord"]
        shop["ncrev"][_d] = round(_c["nrev"]); shop["rcrev"][_d] = round(_c["rrev"])
    if _nrdOK: log("new/returning from Odoo customer actuals, days", _nrdOK)
    pos = safe(pull_pos_branches) or {}
    prev = {}
    try:
        pd0 = open(os.path.join(DOCS, "data.js")).read()
        prev = json.loads(pd0[pd0.index("window.O=") + 9: pd0.index(";\nwindow.F=")])
    except Exception: pass
    # v7.1 RESILIENCE: a broken source must NEVER zero the chart. If a Shopify day came back
    # empty (fetch gap or an API change like the ShopifyQL rename) but the previous payload
    # had it, carry the last-known values forward instead of writing a cliff of zeros.
    try:
        SHK = ["sessions", "atcRatio", "checkoutRatio", "cvr"] if _nrdOK else \
              ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]
        pad = prev.get("ad") or {}
        if pad.get("start"):
            _pd0 = datetime.date.fromisoformat(pad["start"])
            def _pm(key):
                a = pad.get(key) or []
                return {(_pd0 + datetime.timedelta(days=i)).isoformat(): v for i, v in enumerate(a)}
            pmaps = {k: _pm(k) for k in SHK}
            healed = 0
            for d in list(shop.get("sessions", {})):
                if not shop["sessions"].get(d) and pmaps["sessions"].get(d):
                    for k in SHK:
                        pv = pmaps[k].get(d)
                        if pv:
                            shop[k][d] = pv
                    healed += 1
            if healed:
                log("shopify carry-forward", healed, "days (fetch gap healed from last good payload)")
    except Exception as e:
        log("carry-forward skipped", str(e)[:120])
    # v9.8: also force a heavy crawl when the stored cube is still the OLD 3-number shape,
    # so the returns-excluded trio (and the Cohorts "Exclude returns" switch that depends on
    # it) appears on the next run instead of silently waiting for the 03:00 UTC window.
    def _cube_old(pv):
        try:
            for sc in ((pv.get("cube") or {}).get("scopes") or {}).values():
                for c in (sc or {}).values():
                    m = (c or {}).get("m") or []
                    if m and m[0]: return len(m[0]) < 6
        except Exception: pass
        return False
    heavy = os.environ.get("FORCE_CRAWL") == "1" or datetime.datetime.utcnow().hour < 3 or not (prev.get("bnr")) or not (prev.get("bun")) or not (prev.get("dec")) or not (prev.get("xchan")) or not ((prev.get("jour") or {}).get("cat")) or not (prev.get("mcross")) or not (prev.get("cube")) or not (((prev.get("cube") or {}).get("scopes") or {}).get("ALL STORES")) or _cube_old(prev) or not any(
        r.get("ct") for rs in (prev.get("dec") or {}).values() for r in (rs or []))
    if heavy:
        _bc = safe(pull_pos_customers) or ({}, {}, {}, {})
        bnr, bstat, bcoh, bun = _bc if isinstance(_bc, tuple) and len(_bc) == 4 else ({}, {}, {}, {})
        if not bnr: bnr, bstat, bcoh, bun = prev.get("bnr", {}), prev.get("bstat", {}), prev.get("bcoh", {}), prev.get("bun", {})
    else:
        bnr, bstat, bcoh, bun = prev.get("bnr", {}), prev.get("bstat", {}), prev.get("bcoh", {}), prev.get("bun", {})
        log("pos customers carried forward (heavy crawl runs on first sync of the day)")
    if heavy:
        _vd = safe(pull_vendors) or ({}, {})
        vend, prodv = _vd if isinstance(_vd, tuple) and len(_vd) == 2 else ({}, {})
        if not vend.get("rows"): vend, prodv = prev.get("vend", {}), prev.get("prodv", {})
    else:
        vend, prodv = prev.get("vend", {}), prev.get("prodv", {})
    if heavy:
        safe(pull_shop_lines)
        safe(pull_meta_driven_cross)
    vinv = (safe(pull_vendor_inventory) if heavy else None) or prev.get("vinv", {})
    # v6.5 -- deciles / lag / unregistered revenue / vendor+product monthly, with carry-forward
    dec = XTRA.get("dec") or prev.get("dec", {})
    lag = XTRA.get("lag") or prev.get("lag", {})
    bunr = {b: {m: round(v) for m, v in ms.items()} for b, ms in XTRA.get("bunr", {}).items()} or prev.get("bunr", {})
    mreach = XTRA.get("mreach") or prev.get("reach", {})
    xchan = XTRA.get("xchan") or prev.get("xchan", {})
    treach = XTRA.get("treach") or prev.get("treach", {})
    vmon = XTRA.get("vmon", {})
    if vmon and vend.get("rows"):
        for vr in vend["rows"]:
            mm = vmon.get(vr["v"])
            if mm: vr["mon"] = {m: [round(x[0]), round(x[1])] for m, x in mm.items()}
    elif vend.get("rows") and prev.get("vend", {}).get("rows"):
        pv = {r["v"]: r.get("mon") for r in prev["vend"]["rows"] if r.get("mon")}
        for vr in vend["rows"]:
            if vr["v"] in pv: vr["mon"] = pv[vr["v"]]
    pmon = XTRA.get("pmon", {})
    if pmon and prodv.get("rows"):
        for pr in prodv["rows"]:
            mm = pmon.get(pr.get("t"))
            if mm: pr["mon"] = {m: [round(x[0]), round(x[1])] for m, x in mm.items()}
    elif prodv.get("rows") and prev.get("prodv", {}).get("rows"):
        pv = {r["n"]: r.get("mon") for r in prev["prodv"]["rows"] if r.get("mon")}
        for pr in prodv["rows"]:
            if pr["n"] in pv: pr["mon"] = pv[pr["n"]]
    _mtok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    mads = safe(pull_meta_ads, _mtok) or []
    bev = safe(pull_budget_events, _mtok) or []
    objd = safe(pull_obj_daily, _mtok, bev) or {}
    cre = safe(pull_campaign_reach, _mtok) or {}
    safe(sync_offline_audience, _mtok)
    safe(sync_google_audience)
    safe(sync_gmb_branch_sales)
    gattr = safe(pull_google_attr) or {}
    gads = safe(pull_google_ads) or prev.get("gads", [])
    tads = safe(pull_tiktok_ads) or prev.get("tads", [])
    bcost = safe(pull_branch_costs) or {}
    ship = safe(pull_shipping) or {}
    if not ship.get("grp"): ship = prev.get("ship", ship)
    ship2 = safe(pull_ship2) or {}
    if not ship2.get("m"): ship2 = prev.get("ship2", ship2)
    sal = safe(pull_salaries) or {}
    if not sal.get("rows"): sal = prev.get("sal", sal)
    _er = safe(pull_expenses)
    exp, rentB = _er if isinstance(_er, tuple) else ({}, {})
    if not fin:
        # Odoo is the spine -- without it there are no actuals to write. Raise rather than
        # return quietly: main() will stamp the reason into data.js and status.json so the
        # failure is visible in the repo instead of looking like a run that did nothing.
        raise RuntimeError("Odoo pull returned nothing (pull_odoo failed) -- previous data.js kept")
    merge_fallback(win, shop, goog, tik, meta)
    def arr(dct, k, dec=0):
        m = dct.get(k, {}) if dct else {}
        return [round(m.get(d, 0), 2) if dec else int(round(m.get(d, 0))) for d in win]
    ad = {"start": win[0], "n": len(win),
          "mspend": arr(meta, "mspend"), "gspend": arr(goog, "gspend"), "tspend": arr(tik, "tspend"),
          "sessions": arr(shop, "sessions"), "gecomrev": arr(goog, "gecomrev"), "gconv": arr(goog, "gconv"),
          "mecomrev": arr(meta, "mecomrev"), "ttValue": arr(tik, "ttValue"),
          "metaOmniValue": arr(meta, "metaOmniValue"), "instoreMeta": arr(meta, "instoreMeta"),
          "instoreOnsite": arr(meta, "instoreOnsite"),
          "metaOfflinePur": arr(meta, "metaOfflinePur"), "mpur": arr(meta, "mpur"),
          "instoreNC": arr(meta, "instoreNC"),
          "newcust": arr(shop, "newcust"), "retcust": arr(shop, "retcust"),
          "ncrev": arr(shop, "ncrev"), "rcrev": arr(shop, "rcrev"), "tpur": arr(tik, "tpur"),
          "ttOffValue": arr(tik, "ttOffValue"), "ttOffPur": arr(tik, "ttOffPur"),
          "mimp": arr(meta, "mimp"), "mclk": arr(meta, "mclk"), "moffv": arr(meta, "moffv"),
          "gimp": arr(goog, "gimp"), "gclk": arr(goog, "gclk"),
          "timp": arr(tik, "timp"), "tclk": arr(tik, "tclk"),
          "atcRatio": arr(shop, "atcRatio", 1), "checkoutRatio": arr(shop, "checkoutRatio", 1),
          "cvr": arr(shop, "cvr", 1)}
    ad["spend"] = [ad["mspend"][i] + ad["gspend"][i] + ad["tspend"][i] for i in range(len(win))]
    if MEAS["base"] > 0:
        ATTR["meta"]["7dc"] = round(min(1.2, MEAS["w7"] / MEAS["base"]), 3)
        ATTR["meta"]["1dc"] = round(min(1.2, MEAS["w1"] / MEAS["base"]), 3)
        ATTR["labels"]["7dc"] = "7-day click (measured: %.0f%% of live)" % (ATTR["meta"]["7dc"] * 100)
        ATTR["labels"]["1dc"] = "1-day click (measured: %.0f%% of live)" % (ATTR["meta"]["1dc"] * 100)
        log("meta attribution measured", ATTR["meta"]["7dc"], ATTR["meta"]["1dc"])
    # v8.1: the in-store leg gets its OWN measured ladder. Applying the website
    # coefficients to store revenue was the second half of the same mistake.
    ob = MEAS["off_base"]
    if ob > 0:
        ATTR["metaOff"] = {"default": 1.0,
                           "7dc": round(min(1.2, MEAS["off_w7"] / ob), 3),
                           "1dc": round(min(1.2, MEAS["off_w1"] / ob), 3),
                           "incr": ATTR["meta"]["incr"]}
    ib = MEAS["base"] + ob
    ii = MEAS["incr_pix"] + MEAS["incr_off"]
    if MEAS["incr_ok"] and ib > 0 and ii > 0:
        ATTR["meta"]["incr"] = round(ii / ib, 3)
        ATTR["labels"]["incr"] = "Incremental \u2014 Meta ACTUAL (%.0f%% of live)" % (ATTR["meta"]["incr"] * 100)
        if ob > 0:
            ATTR["metaOff"]["incr"] = round(MEAS["incr_off"] / ob, 3)
        if MEAS["base"] > 0:
            ATTR["meta"]["incr"] = round(MEAS["incr_pix"] / MEAS["base"], 3)
        log("meta INCREMENTAL actual :: web", ATTR["meta"]["incr"],
            ":: in-store", (ATTR.get("metaOff") or {}).get("incr"),
            ":: raw incr", int(ii), "of live", int(ib))
    else:
        ATTR["labels"]["incr"] = ("Incremental \u2014 MODELLED, NOT MEASURED (Meta did not return "
                                  "its incremental column: %s)" % (MEAS["incr_err"] or "no rows"))
        log("meta INCREMENTAL unavailable ::", MEAS["incr_err"] or "no rows",
            ":: dashboard will label the option as modelled")
    if ob > 0:
        log("meta offline attribution measured :: 7dc", ATTR["metaOff"]["7dc"],
            ":: 1dc", ATTR["metaOff"]["1dc"], ":: incr", ATTR["metaOff"]["incr"],
            ":: live offline value", int(ob))
    fwin = drange(datetime.date.fromisoformat(fin["start"]), END)
    sh = {"rev": [int(round(shc["srev"].get(d, 0) / 1000.0)) for d in fwin],
          "gp": [int(round(shc["sgp"].get(d, 0) / 1000.0)) for d in fwin],
          "ref": [int(round(shc["sref"].get(d, 0) / 1000.0)) for d in fwin],
          "ord": [int(shc["sord"].get(d, 0)) for d in fwin]}
    def _lastnz(m):
        ds = [d for d, v in (m or {}).items() if v]
        return max(ds) if ds else None
    _ttok = bool(os.environ.get("TIKTOK_TOKEN", "").strip())
    freshmap = {"odoo": END.isoformat(), "shopify": _lastnz(shop.get("sessions")),
                "meta": _lastnz(meta.get("mspend")), "google": _lastnz(goog.get("gspend")),
                "tiktok": (_lastnz(tik.get("tspend")) if _ttok else "off")}
    def _isstale(k, v):
        # v7.8: STALE now means the pipe is broken, not that the account is idle.
        # If the API answered and simply had no spend to report, that is a business
        # fact, not a sync failure -- it gets logged as idle and left off the alarm.
        if v == "off": return False
        if not v: return not SRCOK.get(k)
        try: fresh = (today - datetime.date.fromisoformat(v)).days <= 2
        except Exception: return True
        if fresh: return False
        return not SRCOK.get(k)
    stalelist = [k for k, v in freshmap.items() if _isstale(k, v)]
    idlelist = [k for k, v in freshmap.items()
                if v not in ("off", None) and k not in stalelist and SRCOK.get(k)
                and (today - datetime.date.fromisoformat(v)).days > 2]
    for k in idlelist:
        log("IDLE source: %s -- API answered fine, no activity recorded since %s. "
            "Not a sync failure; nothing was spent/tracked." % (k, freshmap[k]))
    log("freshness", freshmap)
    log("STALE sources: " + (",".join(stalelist) if stalelist else "none - all fresh to date"))
    jour = XTRA.get("jour") or {}
    if not jour.get("cat") and (prev.get("jour") or {}).get("cat"):
        pj = prev["jour"]; pj.setdefault("scopes", {}).update(jour.get("scopes") or {}); jour = pj
    online = {"cur": "EGP", "lastSync": ts, "fin": fin, "ad": ad, "bl": bl or {}, "prod": prod,
              "shop": sh, "coh": coh, "nr": nrm, "exp": exp, "rentB": rentB, "rentDx": dict(RENT_DX) or prev.get("rentDx", {}), "pos": pos, "bcost": bcost, "bnr": bnr, "bstat": bstat, "bcoh": bcoh, "bun": bun,
              "bmeta": {b: {"v": [round(ms.get(d, {}).get("v", 0.0)) for d in win],
                            "p": [round(ms.get(d, {}).get("p", 0.0)) for d in win],
                            "nc": [round(ms.get(d, {}).get("nc", 0.0)) for d in win]}
                        for b, ms in MBR.items()},
              "vend": vend, "prodv": prodv, "ship": ship, "ship2": ship2, "sal": sal, "vinv": vinv,
              "dec": dec, "lag": lag, "bunr": bunr, "reach": mreach, "treach": treach, "xchan": xchan,
              "mads": mads, "gads": gads, "tads": tads,
              "madsW": XTRA.get("madsW") or prev.get("madsW"), "bev": bev, "cre": cre, "jour": jour,
              "cvr": XTRA.get("cvr") or prev.get("cvr") or {},
              "metaCC": XTRA.get("metaCC") or prev.get("metaCC") or {},
              "mcross": XTRA.get("mcross") or prev.get("mcross") or {},
              "cube": (lambda _n, _p: {"scopes": {**(_p.get("scopes") or {}), **(_n.get("scopes") or {})},
                                        "ven": (_n.get("ven") or _p.get("ven") or {}),
                                        "cat": (_n.get("cat") or _p.get("cat") or {})})(
                          XTRA.get("cube") or {}, prev.get("cube") or {}),
              "gattr": gattr or prev.get("gattr") or {},
              "objD": objd or prev.get("objD") or {},
              "partial": END.isoformat(), "fullEnd": FULLEND.isoformat(), "today": today.isoformat(),
              "macc": {a: {m: {k: round(v) for k, v in mm.items()} for m, mm in ms.items()} for a, ms in MACC.items()},
              "ann": annotations(fin), "attr": ATTR, "src": SRC, "fresh": freshmap, "stale": stalelist, "idle": idlelist, "srcok": {k: bool(v) for k, v in SRCOK.items()}, "aw": [win[0], win[-1]]}
    offp = os.path.join(DOCS, "offline.json")
    off = json.load(open(offp)) if os.path.exists(offp) else json.loads(OFFLINE_JSON)
    off["meta"]["offlineValue"] = int(round(sum(meta.get("instoreMeta", {}).values()))) or off["meta"].get("offlineValue", 0)
    off["meta"]["offlinePur"] = int(round(sum(meta.get("metaOfflinePur", {}).values()))) or off["meta"].get("offlinePur", 0)
    out = "window.O=" + json.dumps(online, separators=(",", ":"), ensure_ascii=True) + ";\n"
    out += "window.F=" + json.dumps(off, separators=(",", ":"), ensure_ascii=True) + ";"
    open(os.path.join(DOCS, "data.js"), "w").write(out)
    log("WROTE data.js", len(out), "bytes  synced", ts)

OFFLINE_JSON = r'''{"currency":"EGP","brand":"OurKids","branches":[{"name":"Dokki","payroll":247027,"hc":25,"aov":1328.4,"revEst":3857585,"rentEst":308607,"opexEst":192879},{"name":"Mall of Arabia","payroll":195636,"hc":17,"aov":1286.0,"revEst":3055060,"rentEst":244405,"opexEst":152753},{"name":"New Cairo","payroll":192211,"hc":16,"aov":1329.3,"revEst":3001576,"rentEst":240126,"opexEst":150079},{"name":"Zayed","payroll":181843,"hc":17,"aov":991.9,"revEst":2839668,"rentEst":227173,"opexEst":141983},{"name":"Nasr City","payroll":171890,"hc":19,"aov":1303.0,"revEst":2684242,"rentEst":214739,"opexEst":134212},{"name":"October","payroll":149101,"hc":13,"aov":1206.0,"revEst":2328368,"rentEst":186269,"opexEst":116418},{"name":"Smouha","payroll":139685,"hc":14,"aov":1050.0,"revEst":2181327,"rentEst":174506,"opexEst":109066}],"company":{"payrollTotal":2906175,"branchPayroll":1277393,"warehousePayroll":420305,"ecomPayroll":372076,"hqPayroll":783651,"envelope":52750,"gpPct":0.266,"refundRate":0.175,"overheadPoolDefault":1203956,"aggRetailMonthly":19947826},"meta":{"offlineValue":1016656,"offlinePur":664,"window":"25 Jun \u2013 24 Jul 2026"},"attr":{"order":["default","7dc","1dc","incr"],"labels":{"default":"Default 7DC/1DV (LIVE)","7dc":"7-day click (modeled)","1dc":"1-day click (modeled)","incr":"Incremental \u2014 MODELLED (no live Meta pull)"},"meta":{"default":1.0,"7dc":0.94,"1dc":0.78,"incr":0.6},"metaOff":{"default":1.0,"7dc":0.42,"1dc":0.24,"incr":0.17}},"notes":{"revenue":"Branch revenue is an EDITABLE ESTIMATE (payroll-weighted split of the ERP-audit E\u00a3458.8M since Aug-2024 \u2248 19.95M/mo). Real POS revenue is walled off from the read-only Odoo account (audit S-01). Type real per-branch numbers to make breakeven exact.","rent":"Rent + opex are EDITABLE placeholders (8% / 5% of revenue). Enter your real lease + running costs.","payroll":"Payroll is EXACT \u2014 Excel 'OurKids payroll by function', June 2026.","gp":"Contribution margin uses net GP% 26.6% (Odoo margin, recent) and refund rate 17.5% (ERP audit S-03).","newret":"Per-branch new/returning split needs POS access (walled). Online new/returning shown on the main dashboard."},"bltg":{"asOf":"2026-07-22","perCustomer":{"October":1231,"Dokki":1168,"New Cairo":1084,"Zayed":1059,"Nasr City":948,"Smouha":810,"Mall of Arabia":807}}}'''

def _dataface():
    """What is actually sitting in data.js right now, so status.json can report it
       without anyone having to open the file."""
    try:
        t = open(os.path.join(DOCS, "data.js"), encoding="utf-8").read()
        o = json.loads(t[t.index("window.O=") + 9: t.index(";\nwindow.F=")])
        return {"bytes": len(t), "lastSync": o.get("lastSync"), "windowEnd": (o.get("aw") or [None, None])[1],
                "partial": o.get("partial"), "today": o.get("today"),
                "fresh": o.get("fresh"), "stale": o.get("stale")}
    except Exception as e:
        return {"bytes": 0, "error": str(e)[:200]}


def _flag_stale(msg):
    """The run could not refresh the numbers. Do NOT touch lastSync -- that would be a lie.
       Stamp the attempt and the reason INTO data.js instead, so the dashboard can say
       'these numbers are from Saturday and here is why' rather than silently serving
       two-day-old figures under a clock that looks like today."""
    try:
        fp = os.path.join(DOCS, "data.js")
        t = open(fp, encoding="utf-8").read()
        i = t.index("window.O=") + 9; j = t.index(";\nwindow.F=")
        o = json.loads(t[i:j])
        o["syncError"] = str(msg)[:300]
        o["lastAttempt"] = datetime.datetime.now(CAIRO).strftime("%Y-%m-%d %H:%M Cairo")
        open(fp, "w", encoding="utf-8").write("window.O=" + json.dumps(o, separators=(",", ":"), ensure_ascii=True) + t[j:])
        log("stale flag written into data.js:", str(msg)[:120])
    except Exception as e:
        log("could not write stale flag", str(e)[:120])


def main():
    """Never exit non-zero and never exit silently.

       The workflow used to run `python ourkids_live.py` bare. Any uncaught exception
       killed the job, the commit step never ran, and the dashboard carried on serving
       whatever data.js was last committed -- under a clock reading only "5:29 PM", which
       looks like today. Two days of that went unnoticed. So: every run, success or
       failure, writes docs/ourkids/status.json and exits 0. The commit step then always
       has something to push, and the repo itself becomes the run log."""
    t0 = datetime.datetime.now(datetime.timezone.utc)
    ok, err = True, ""
    try:
        build()
    except Exception as e:
        ok = False
        err = "%s: %s" % (type(e).__name__, e)
        log("BUILD FAILED", err[:300])
        try:
            import traceback
            for ln in traceback.format_exc().strip().split("\n")[-12:]: log("  " + ln)
        except Exception: pass
        _flag_stale(err)
    t1 = datetime.datetime.now(datetime.timezone.utc)
    face = _dataface()
    fresh = False
    try:
        we = face.get("windowEnd")
        fresh = bool(we) and (today - datetime.date.fromisoformat(we)).days <= 1
    except Exception: pass
    st = {"ok": ok, "error": err[:300], "fresh": fresh,
          "startedUtc": t0.strftime("%Y-%m-%d %H:%M:%S UTC"),
          "finishedUtc": t1.strftime("%Y-%m-%d %H:%M:%S UTC"),
          "seconds": int((t1 - t0).total_seconds()),
          "runner": os.environ.get("GITHUB_RUN_ID", "local"),
          "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "-"),
          "todayCairo": today.isoformat(),
          "data": face, "log": LOGBUF[-140:]}
    try:
        open(os.path.join(DOCS, "status.json"), "w", encoding="utf-8").write(json.dumps(st, indent=1))
        log("WROTE status.json  ok=%s fresh=%s %ss" % (ok, fresh, st["seconds"]))
    except Exception as e:
        log("could not write status.json", str(e)[:120])
    sys.exit(0)


if __name__ == "__main__":
    main()
