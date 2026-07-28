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

MEAS = {"base": 0.0, "w7": 0.0, "w1": 0.0}

def _av(actions, keys):
    for a in actions or []:
        if a.get("action_type") in keys:
            try: return float(a.get("value") or 0)
            except Exception: return 0.0
    return 0.0
def meta_accounts(tok):
    ids = os.environ.get("META_ACCOUNT_IDS", "").strip()
    if ids:
        return [x if x.startswith("act_") else "act_" + x for x in ids.split(",") if x.strip()]
    d = http_json(GRAPH + "/me/adaccounts?" + urllib.parse.urlencode(
        {"fields": "name,account_status", "limit": 200, "access_token": tok}))
    out = [a["id"] for a in (d.get("data") or [])
           if "ourkid" in (a.get("name") or "").lower() and a.get("account_status") == 1]
    return out or [os.environ.get("META_ACCOUNT_ID", "act_336343742536460")]

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

MBR = {}                          # branch -> "YYYY-MM" -> {v, p, nc}
# v6.5: outputs that several pulls contribute to. vmon/pmon = vendor/product monthly [rev,gp];
# dec = customer deciles per scope; lag = inter-order gap histograms; bunr = unregistered revenue;
# mreach/treach = monthly deduplicated reach from Meta / TikTok.
XTRA = {}

def _decile(agg, ocnt):
    """agg: pid -> [rev, gp, qty, negrev, first_d, last_d, gross_retail, disc]. ocnt: pid -> orders.
    Returns 10 rows, D1 = top spenders by lifetime net revenue. Every number is a straight sum."""
    pids = sorted(agg.keys(), key=lambda p: -agg[p][0])
    n = len(pids)
    if n < 50: return []
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
        out.append({"c": len(chunk), "o": o, "r": round(r), "g": round(g), "q": round(q),
                    "ng": round(neg), "gr": round(gr), "dc": round(dc),
                    "rep": rep2, "ls": round(ls / len(chunk), 1) if chunk else 0})
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

def pull_meta(win):
    tok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    ad = {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur", "instoreNC", "mimp", "mclk", "moffv"]}
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
    for acct in meta_accounts(tok):
      an = anames.get(acct, acct)
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
            ad["instoreMeta"][day] += max(0.0, omni - pixel)
            ad["metaOfflinePur"][day] += _av(row.get("actions"), ("offline_conversion.purchase",))
            ad["moffv"][day] += _av(av, ("offline_conversion.purchase",))
            ad["mpur"][day] += _av(row.get("actions"), ("offsite_conversion.fb_pixel_purchase",))
            mo = day[:7]
            cv = _cc(av); ca = _cc(row.get("actions"))
            for cid, br in MCC_VAL.items():
                v = cv.get(cid, 0.0); pu = ca.get(cid, 0.0)
                if v or pu:
                    e = MBR.setdefault(br, {}).setdefault(day, {"v": 0.0, "p": 0.0, "nc": 0.0})
                    e["v"] += v; e["p"] += pu
            for cid, br in MCC_NC.items():
                n = ca.get(cid, 0.0)
                if n:
                    e = MBR.setdefault(br, {}).setdefault(day, {"v": 0.0, "p": 0.0, "nc": 0.0})
                    e["nc"] += n
            ad["instoreNC"][day] += ca.get(MCC_ALLNC, 0.0)
            mc = MACC.setdefault(an, {}).setdefault(mo, {"sp": 0, "pv": 0, "ov": 0})
            mc["sp"] += float(row.get("spend") or 0); mc["pv"] += pixel; mc["ov"] += omni
    log("meta days", sum(1 for v in ad["mspend"].values() if v),
        "| per-branch in-store days", sum(len(v) for v in MBR.values()),
        "| branches", len(MBR))
    return ad

def shopify_ql(ql):
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    ver = os.environ.get("SHOPIFY_API_VERSION", "2025-07").strip()
    if not store or not tok: return None
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    # v7.1: shopifyqlQuery now returns ShopifyqlQueryResponse (a plain object) -- the old
    # "... on TableResponse" union fragment was removed by Shopify and threw "No such type
    # TableResponse", zeroing every sessions/funnel day. Read the fields directly instead.
    q = 'query($ql:String!){ shopifyqlQuery(query:$ql){ parseErrors tableData{ columns{ name } rows } } }'
    d = http_json("https://%s/admin/api/%s/graphql.json" % (host, ver),
                  {"query": q, "variables": {"ql": ql}}, {"X-Shopify-Access-Token": tok})
    sq = (((d or {}).get("data") or {}).get("shopifyqlQuery") or {})
    td = sq.get("tableData")
    if not td:
        log("shopifyql fail:", str(sq.get("parseErrors") or d)[:200]); return None
    cols = [c["name"] for c in td.get("columns", [])]
    return [dict(zip(cols, r)) for r in td.get("rows", [])]

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
                      "TIMESERIES day SINCE -%dd UNTIL today" % (len(win) + 1))
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
    nr = shopify_ql("FROM orders SHOW orders, total_sales GROUP BY customer_type TIMESERIES day SINCE -%dd UNTIL today" % (len(win) + 1))
    for r in (nr or []):
        day = str(r.get("day"))[:10]
        if day not in out["newcust"]: continue
        ct = str(r.get("customer_type") or "").lower(); o = float(r.get("orders") or 0)
        ts = float(r.get("total_sales") or 0)
        if "return" in ct:
            out["retcust"][day] += o; out["rcrev"][day] += ts
        else:
            out["newcust"][day] += o; out["ncrev"][day] += ts
    log("shopify days", sum(1 for v in out["sessions"].values() if v))
    return out

GTOK = [None]
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
        log("cohorts", len(out), "customers", len(first), "nr months", len(nr))
        return {"coh": out, "nr": nr}
    except Exception as e:
        log("cohorts fail", str(e)[:150]); return {"coh": [], "nr": {}}

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
    TV = tmpl_vendor_map()
    VCS = {}; PFD = {}; PFV = {}
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
                    _vn = TV.get(_tid, ("", "", ""))[0]
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
        XTRA["dec"] = {sc: _decile(DA[sc], DOC.get(sc, {})) for sc in DA}
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
    TV = tmpl_vendor_map()
    agg = {}; oc = {}; oseen = set()
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
            _vn = TV.get(_tid, ("", "", ""))[0]
            _m7 = d[:7]
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
        off += len(page)
        if len(page) < 10000: break
    XTRA.setdefault("dec", {})["Shopify"] = _decile(agg, oc)
    log("shopify decile customers", len(agg), "orders", len(oseen))
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

def pull_meta_ads(tok):
    """Top ads last 30d with website + omni value and viewable thumbnails."""
    out = []
    if not tok:
        p2 = os.path.join(DOCS, "mads_fallback.json")
        if os.path.exists(p2):
            try: return json.load(open(p2))
            except Exception: pass
        return out
    try:
        c1 = END.isoformat(); c0 = (END - datetime.timedelta(days=29)).isoformat()
        rows = []
        for acct in meta_accounts(tok):
            p = {"level": "ad", "access_token": tok, "time_range": json.dumps({"since": c0, "until": c1}),
                 "fields": "ad_id,ad_name,spend,impressions,clicks,action_values,actions", "limit": 300}
            d = http_json("%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p)))
            rows += (d.get("data") or [])
        ads = []
        for r in rows:
            sp = float(r.get("spend") or 0)
            if sp < 1000: continue
            av = r.get("action_values") or []
            pv = _av(av, ("offsite_conversion.fb_pixel_purchase",))
            ov = _av(av, ("omni_purchase",)) or pv
            pur = _av(r.get("actions"), ("offsite_conversion.fb_pixel_purchase",))
            opur = _av(r.get("actions"), ("offline_conversion.purchase",))
            ads.append({"id": r.get("ad_id"), "n": (r.get("ad_name") or "")[:80], "sp": round(sp),
                        "pv": round(pv), "ov": round(ov), "pur": int(pur), "opur": int(opur),
                        "imp": int(float(r.get("impressions") or 0)),
                        "clk": int(float(r.get("clicks") or 0)),
                        "ofv": round(_av(av, ("offline_conversion.purchase",))),
                        "pf": "meta"})
        ads.sort(key=lambda a: -a["sp"]); ads = ads[:40]
        ids = [a["id"] for a in ads if a["id"]]
        for i in range(0, len(ids), 25):
            d = http_json("%s/?ids=%s&fields=creative.thumbnail_width(600).thumbnail_height(600){thumbnail_url,image_url,object_type},preview_shareable_link,effective_status,adset{name},campaign{name}&access_token=%s" % (GRAPH, ",".join(ids[i:i+25]), tok))
            for a in ads:
                info = (d or {}).get(a["id"]) or {}
                cr = info.get("creative") or {}
                th = cr.get("thumbnail_url")
                if th: a["th"] = th
                iu = cr.get("image_url")
                if iu: a["im"] = iu
                pl = info.get("preview_shareable_link")
                if pl: a["pl"] = pl
                es = info.get("effective_status")
                if es: a["st"] = str(es)[:32]
                cn = ((info.get("campaign") or {}).get("name"))
                if cn: a["cmp"] = str(cn)[:60]
                an = ((info.get("adset") or {}).get("name"))
                if an: a["as"] = str(an)[:60]
        log("meta ads", len(ads))
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
                   "1dc": "1-day click (modeled)", "incr": "Incremental (modeled · not a lift test)"},
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
       "instoreMeta": "Meta omni value minus pixel value = offline/in-store attributed.",
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
    log("collector v7.1 (shopifyql fix + deciles + vendor inventory)")
    win = drange(AD_START, END)
    def safe(fn, *a):
        try: return fn(*a)
        except Exception as e:
            log(fn.__name__, "FAILED", str(e)[:200]); return None
    fin = safe(pull_odoo)
    bl = safe(pull_branches)
    prod = safe(pull_products) or []
    meta = safe(pull_meta, win) or {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur", "instoreNC", "mimp", "mclk", "moffv"]}
    shop = safe(pull_shopify, win) or {k: {d: 0.0 for d in win} for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]}
    goog = safe(pull_google, win) or {k: {d: 0.0 for d in win} for k in ["gspend", "gecomrev", "gconv", "gimp", "gclk"]}
    tik = safe(pull_tiktok, win) or {k: {d: 0.0 for d in win} for k in ["tspend", "ttValue", "tpur", "ttOffValue", "ttOffPur", "timp", "tclk"]}
    shc = safe(pull_shop_channel, [FIN_START]) or {"srev": {}, "sgp": {}, "sord": {}, "sref": {}}
    safe(pull_meta_reach, win, os.environ.get("META_ACCESS_TOKEN", "").strip())
    _ch = safe(pull_cohorts) or {"coh": [], "nr": {}}
    coh, nrm = _ch.get("coh", []), _ch.get("nr", {})
    pos = safe(pull_pos_branches) or {}
    prev = {}
    try:
        pd0 = open(os.path.join(DOCS, "data.js")).read()
        prev = json.loads(pd0[pd0.index("window.O=") + 9: pd0.index(";\nwindow.F=")])
    except Exception: pass
    heavy = os.environ.get("FORCE_CRAWL") == "1" or datetime.datetime.utcnow().hour < 3 or not (prev.get("bnr")) or not (prev.get("bun")) or not (prev.get("dec")) or not (prev.get("xchan"))
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
    mads = safe(pull_meta_ads, os.environ.get("META_ACCESS_TOKEN", "").strip()) or []
    gads = safe(pull_google_ads) or prev.get("gads", [])
    tads = safe(pull_tiktok_ads) or prev.get("tads", [])
    bcost = safe(pull_branch_costs) or {}
    ship = safe(pull_shipping) or {}
    if not ship.get("grp"): ship = prev.get("ship", ship)
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
    fwin = drange(datetime.date.fromisoformat(fin["start"]), END)
    sh = {"rev": [int(round(shc["srev"].get(d, 0) / 1000.0)) for d in fwin],
          "gp": [int(round(shc["sgp"].get(d, 0) / 1000.0)) for d in fwin],
          "ref": [int(round(shc["sref"].get(d, 0) / 1000.0)) for d in fwin],
          "ord": [int(shc["sord"].get(d, 0)) for d in fwin]}
    online = {"cur": "EGP", "lastSync": ts, "fin": fin, "ad": ad, "bl": bl or {}, "prod": prod,
              "shop": sh, "coh": coh, "nr": nrm, "exp": exp, "rentB": rentB, "rentDx": dict(RENT_DX) or prev.get("rentDx", {}), "pos": pos, "bcost": bcost, "bnr": bnr, "bstat": bstat, "bcoh": bcoh, "bun": bun,
              "bmeta": {b: {"v": [round(ms.get(d, {}).get("v", 0.0)) for d in win],
                            "p": [round(ms.get(d, {}).get("p", 0.0)) for d in win],
                            "nc": [round(ms.get(d, {}).get("nc", 0.0)) for d in win]}
                        for b, ms in MBR.items()},
              "vend": vend, "prodv": prodv, "ship": ship, "sal": sal, "vinv": vinv,
              "dec": dec, "lag": lag, "bunr": bunr, "reach": mreach, "treach": treach, "xchan": xchan,
              "mads": mads, "gads": gads, "tads": tads,
              "partial": END.isoformat(), "fullEnd": FULLEND.isoformat(), "today": today.isoformat(),
              "macc": {a: {m: {k: round(v) for k, v in mm.items()} for m, mm in ms.items()} for a, ms in MACC.items()},
              "ann": annotations(fin), "attr": ATTR, "src": SRC, "aw": [win[0], win[-1]]}
    offp = os.path.join(DOCS, "offline.json")
    off = json.load(open(offp)) if os.path.exists(offp) else json.loads(OFFLINE_JSON)
    off["meta"]["offlineValue"] = int(round(sum(meta.get("instoreMeta", {}).values()))) or off["meta"].get("offlineValue", 0)
    off["meta"]["offlinePur"] = int(round(sum(meta.get("metaOfflinePur", {}).values()))) or off["meta"].get("offlinePur", 0)
    out = "window.O=" + json.dumps(online, separators=(",", ":"), ensure_ascii=True) + ";\n"
    out += "window.F=" + json.dumps(off, separators=(",", ":"), ensure_ascii=True) + ";"
    open(os.path.join(DOCS, "data.js"), "w").write(out)
    log("WROTE data.js", len(out), "bytes  synced", ts)

OFFLINE_JSON = r'''{"currency":"EGP","brand":"OurKids","branches":[{"name":"Dokki","payroll":247027,"hc":25,"aov":1328.4,"revEst":3857585,"rentEst":308607,"opexEst":192879},{"name":"Mall of Arabia","payroll":195636,"hc":17,"aov":1286.0,"revEst":3055060,"rentEst":244405,"opexEst":152753},{"name":"New Cairo","payroll":192211,"hc":16,"aov":1329.3,"revEst":3001576,"rentEst":240126,"opexEst":150079},{"name":"Zayed","payroll":181843,"hc":17,"aov":991.9,"revEst":2839668,"rentEst":227173,"opexEst":141983},{"name":"Nasr City","payroll":171890,"hc":19,"aov":1303.0,"revEst":2684242,"rentEst":214739,"opexEst":134212},{"name":"October","payroll":149101,"hc":13,"aov":1206.0,"revEst":2328368,"rentEst":186269,"opexEst":116418},{"name":"Smouha","payroll":139685,"hc":14,"aov":1050.0,"revEst":2181327,"rentEst":174506,"opexEst":109066}],"company":{"payrollTotal":2906175,"branchPayroll":1277393,"warehousePayroll":420305,"ecomPayroll":372076,"hqPayroll":783651,"envelope":52750,"gpPct":0.266,"refundRate":0.175,"overheadPoolDefault":1203956,"aggRetailMonthly":19947826},"meta":{"offlineValue":1016656,"offlinePur":664,"window":"25 Jun \u2013 24 Jul 2026"},"attr":{"order":["default","7dc","1dc","incr"],"labels":{"default":"Default 7DC/1DV (LIVE)","7dc":"7-day click (modeled)","1dc":"1-day click (modeled)","incr":"Incremental (modeled)"},"meta":{"default":1.0,"7dc":0.94,"1dc":0.78,"incr":0.6}},"notes":{"revenue":"Branch revenue is an EDITABLE ESTIMATE (payroll-weighted split of the ERP-audit E\u00a3458.8M since Aug-2024 \u2248 19.95M/mo). Real POS revenue is walled off from the read-only Odoo account (audit S-01). Type real per-branch numbers to make breakeven exact.","rent":"Rent + opex are EDITABLE placeholders (8% / 5% of revenue). Enter your real lease + running costs.","payroll":"Payroll is EXACT \u2014 Excel 'OurKids payroll by function', June 2026.","gp":"Contribution margin uses net GP% 26.6% (Odoo margin, recent) and refund rate 17.5% (ERP audit S-03).","newret":"Per-branch new/returning split needs POS access (walled). Online new/returning shown on the main dashboard."},"bltg":{"asOf":"2026-07-22","perCustomer":{"October":1231,"Dokki":1168,"New Cairo":1084,"Zayed":1059,"Nasr City":948,"Smouha":810,"Mall of Arabia":807}}}'''

def _dataface():
    """What is actually sitting in data.js right now, so status.json can report it
       without anyone having to open the file."""
    try:
        t = open(os.path.join(DOCS, "data.js"), encoding="utf-8").read()
        o = json.loads(t[t.index("window.O=") + 9: t.index(";\nwindow.F=")])
        return {"bytes": len(t), "lastSync": o.get("lastSync"), "windowEnd": (o.get("aw") or [None, None])[1],
                "partial": o.get("partial"), "today": o.get("today")}
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
          "data": face, "log": LOGBUF[-60:]}
    try:
        open(os.path.join(DOCS, "status.json"), "w", encoding="utf-8").write(json.dumps(st, indent=1))
        log("WROTE status.json  ok=%s fresh=%s %ss" % (ok, fresh, st["seconds"]))
    except Exception as e:
        log("could not write status.json", str(e)[:120])
    sys.exit(0)


if __name__ == "__main__":
    main()
