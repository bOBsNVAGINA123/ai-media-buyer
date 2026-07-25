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
today = datetime.date.today()
END = today - datetime.timedelta(days=1)
AD_START = END - datetime.timedelta(days=59)
BL_START = END - datetime.timedelta(days=119)          # branches: 120d so compare works
def drange(a, b):
    out, d = [], a
    while d <= b:
        out.append(d.isoformat()); d += datetime.timedelta(days=1)
    return out
def log(*a): sys.stderr.write("[ourkids] " + " ".join(str(x) for x in a) + "\n")

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
    ds = drange(datetime.date.fromisoformat(FIN_START), END)
    k = lambda v: int(round(v / 1000))
    fin = {"start": FIN_START, "n": len(ds), "k": 1000,
           "rev": [k(daily.get(d, {}).get("rev", 0)) for d in ds],
           "gp": [k(daily.get(d, {}).get("gp", 0)) for d in ds],
           "refund": [k(refund.get(d, 0)) for d in ds],
           "orders": [int(daily.get(d, {}).get("orders", 0)) for d in ds],
           "chD": {c: [k(chD[c].get(d, 0)) for d in ds] for c in chD}}
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

def pull_meta(win):
    tok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    ad = {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur"]}
    for acct in meta_accounts(tok):
        p = {"level": "account", "time_increment": 1, "access_token": tok,
             "time_range": json.dumps({"since": win[0], "until": win[-1]}),
             "fields": "spend,action_values,actions", "limit": 500}
        d = http_json("%s/%s/insights?%s" % (GRAPH, acct, urllib.parse.urlencode(p)))
        for row in (d.get("data") or []):
            day = row.get("date_start")
            if day not in ad["mspend"]: continue
            ad["mspend"][day] += float(row.get("spend") or 0)
            av = row.get("action_values") or []
            pixel = _av(av, ("offsite_conversion.fb_pixel_purchase",))
            omni = _av(av, ("omni_purchase",)) or pixel
            ad["mecomrev"][day] += pixel; ad["metaOmniValue"][day] += omni
            ad["instoreMeta"][day] += max(0.0, omni - pixel)
            ad["metaOfflinePur"][day] += _av(row.get("actions"), ("offline_conversion.purchase",))
            ad["mpur"][day] += _av(row.get("actions"), ("offsite_conversion.fb_pixel_purchase",))
    log("meta days", sum(1 for v in ad["mspend"].values() if v))
    return ad

def shopify_ql(ql):
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    ver = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip()
    if not store or not tok: return None
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    q = 'query($ql:String!){ shopifyqlQuery(query:$ql){ ... on TableResponse { parseErrors tableData{ columns{ name } rows } } } }'
    d = http_json("https://%s/admin/api/%s/graphql.json" % (host, ver),
                  {"query": q, "variables": {"ql": ql}}, {"X-Shopify-Access-Token": tok})
    sq = (((d or {}).get("data") or {}).get("shopifyqlQuery") or {})
    td = sq.get("tableData")
    if not td:
        log("shopifyql fail:", str(sq.get("parseErrors") or d)[:200]); return None
    cols = [c["name"] for c in td.get("columns", [])]
    return [dict(zip(cols, r)) for r in td.get("rows", [])]

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

def pull_google(win):
    out = {"gspend": {d: 0.0 for d in win}, "gecomrev": {d: 0.0 for d in win}, "gconv": {d: 0.0 for d in win}}
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
    gql = ("SELECT segments.date, metrics.cost_micros, metrics.conversions_value, metrics.conversions FROM customer "
           "WHERE segments.date BETWEEN '%s' AND '%s'" % (win[0], win[-1]))
    hd = {"Authorization": "Bearer " + at, "developer-token": dev, "Content-Type": "application/json"}
    lc = os.environ.get("GOOGLE_LOGIN_CID", "")
    if lc: hd["login-customer-id"] = lc
    try:
        req = urllib.request.Request("https://googleads.googleapis.com/v18/customers/%s/googleAds:searchStream" % cid,
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
    log("google days", sum(1 for v in out["gspend"].values() if v))
    return out

def pull_tiktok(win):
    out = {"tspend": {d: 0.0 for d in win}, "ttValue": {d: 0.0 for d in win}}
    tok = os.environ.get("TIKTOK_TOKEN", "").strip(); adv = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
    if not (tok and adv): log("tiktok skipped"); return out
    p = {"advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_ADVERTISER",
         "dimensions": json.dumps(["stat_time_day"]),
         "metrics": json.dumps(["spend", "complete_payment"]),
         "start_date": win[0], "end_date": win[-1], "page_size": 1000}
    d = http_json("https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/?" + urllib.parse.urlencode(p),
                  None, {"Access-Token": tok})
    for row in (((d or {}).get("data") or {}).get("list") or []):
        day = str(row.get("dimensions", {}).get("stat_time_day", ""))[:10]
        if day not in out["tspend"]: continue
        m = row.get("metrics", {})
        out["tspend"][day] = float(m.get("spend") or 0)
        out["ttValue"][day] = float(m.get("complete_payment") or 0)
    log("tiktok days", sum(1 for v in out["tspend"].values() if v))
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
    fill(tik, "tspend", "tspend"); fill(tik, "ttValue", "ttValue")
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

def build():
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    win = drange(AD_START, END)
    def safe(fn, *a):
        try: return fn(*a)
        except Exception as e:
            log(fn.__name__, "FAILED", str(e)[:200]); return None
    fin = safe(pull_odoo)
    bl = safe(pull_branches)
    prod = safe(pull_products) or []
    meta = safe(pull_meta, win) or {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur", "mpur"]}
    shop = safe(pull_shopify, win) or {k: {d: 0.0 for d in win} for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust", "ncrev", "rcrev"]}
    goog = safe(pull_google, win) or {"gspend": {d: 0.0 for d in win}, "gecomrev": {d: 0.0 for d in win}, "gconv": {d: 0.0 for d in win}}
    tik = safe(pull_tiktok, win) or {"tspend": {d: 0.0 for d in win}, "ttValue": {d: 0.0 for d in win}}
    if not fin:
        log("FATAL: Odoo failed; keeping previous data.js"); return
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
          "newcust": arr(shop, "newcust"), "retcust": arr(shop, "retcust"),
          "ncrev": arr(shop, "ncrev"), "rcrev": arr(shop, "rcrev"),
          "atcRatio": arr(shop, "atcRatio", 1), "checkoutRatio": arr(shop, "checkoutRatio", 1),
          "cvr": arr(shop, "cvr", 1)}
    ad["spend"] = [ad["mspend"][i] + ad["gspend"][i] + ad["tspend"][i] for i in range(len(win))]
    online = {"cur": "EGP", "lastSync": ts, "fin": fin, "ad": ad, "bl": bl or {}, "prod": prod,
              "ann": annotations(fin), "attr": ATTR, "src": SRC, "aw": [win[0], win[-1]]}
    offp = os.path.join(DOCS, "offline.json")
    off = json.load(open(offp)) if os.path.exists(offp) else json.loads(OFFLINE_JSON)
    off["meta"]["offlineValue"] = int(round(sum(meta.get("instoreMeta", {}).values()))) or off["meta"].get("offlineValue", 0)
    off["meta"]["offlinePur"] = int(round(sum(meta.get("metaOfflinePur", {}).values()))) or off["meta"].get("offlinePur", 0)
    out = "window.O=" + json.dumps(online, separators=(",", ":"), ensure_ascii=True) + ";\n"
    out += "window.F=" + json.dumps(off, separators=(",", ":"), ensure_ascii=True) + ";"
    open(os.path.join(DOCS, "data.js"), "w").write(out)
    log("WROTE data.js", len(out), "bytes  synced", ts)

OFFLINE_JSON = r'''{"currency":"EGP","brand":"OurKids","branches":[{"name":"Dokki","payroll":247027,"hc":25,"aov":1328.4,"revEst":3857585,"rentEst":308607,"opexEst":192879},{"name":"Mall of Arabia","payroll":195636,"hc":17,"aov":1286.0,"revEst":3055060,"rentEst":244405,"opexEst":152753},{"name":"New Cairo","payroll":192211,"hc":16,"aov":1329.3,"revEst":3001576,"rentEst":240126,"opexEst":150079},{"name":"Zayed","payroll":181843,"hc":17,"aov":991.9,"revEst":2839668,"rentEst":227173,"opexEst":141983},{"name":"Nasr City","payroll":171890,"hc":19,"aov":1303.0,"revEst":2684242,"rentEst":214739,"opexEst":134212},{"name":"October","payroll":149101,"hc":13,"aov":1206.0,"revEst":2328368,"rentEst":186269,"opexEst":116418},{"name":"Smouha","payroll":139685,"hc":14,"aov":1050.0,"revEst":2181327,"rentEst":174506,"opexEst":109066}],"company":{"payrollTotal":2906175,"branchPayroll":1277393,"warehousePayroll":420305,"ecomPayroll":372076,"hqPayroll":783651,"envelope":52750,"gpPct":0.266,"refundRate":0.175,"overheadPoolDefault":1203956,"aggRetailMonthly":19947826},"meta":{"offlineValue":1016656,"offlinePur":664,"window":"25 Jun \u2013 24 Jul 2026"},"attr":{"order":["default","7dc","1dc","incr"],"labels":{"default":"Default 7DC/1DV (LIVE)","7dc":"7-day click (modeled)","1dc":"1-day click (modeled)","incr":"Incremental (modeled)"},"meta":{"default":1.0,"7dc":0.94,"1dc":0.78,"incr":0.6}},"notes":{"revenue":"Branch revenue is an EDITABLE ESTIMATE (payroll-weighted split of the ERP-audit E\u00a3458.8M since Aug-2024 \u2248 19.95M/mo). Real POS revenue is walled off from the read-only Odoo account (audit S-01). Type real per-branch numbers to make breakeven exact.","rent":"Rent + opex are EDITABLE placeholders (8% / 5% of revenue). Enter your real lease + running costs.","payroll":"Payroll is EXACT \u2014 Excel 'OurKids payroll by function', June 2026.","gp":"Contribution margin uses net GP% 26.6% (Odoo margin, recent) and refund rate 17.5% (ERP audit S-03).","newret":"Per-branch new/returning split needs POS access (walled). Online new/returning shown on the main dashboard."}}'''

if __name__ == "__main__":
    build()
