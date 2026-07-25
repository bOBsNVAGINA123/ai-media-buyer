#!/usr/bin/env python3
"""
OurKids live dashboard builder.
Pulls every source from env secrets and writes docs/ourkids/data.js — the exact
window.O / window.F the dashboard reads. Runs inside GitHub Actions on a schedule,
laptop off, nobody online. No assistant, no MCP.

Secrets it reads (GitHub Actions env):
  ODOO_SERVER ODOO_DB ODOO_LOGIN ODOO_APIKEY          (Odoo — falls back to odoo_read.py if present)
  META_ACCESS_TOKEN   META_ACCOUNT_ID (default act_336343742536460)
  SHOPIFY_STORE  SHOPIFY_TOKEN  SHOPIFY_API_VERSION
  GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REFRESH_TOKEN GOOGLE_DEVELOPER_TOKEN
  GOOGLE_LOGIN_CID  GOOGLE_CUSTOMER_ID
  TIKTOK_TOKEN  TIKTOK_ADVERTISER_ID                  (optional — skipped if unset)
Every source is defensive: if one fails it logs to stderr, that block falls back to
zeros/last-good, and the rest still run. The build never crashes the workflow.
"""
import os, sys, io, json, time, datetime, urllib.request, urllib.parse, urllib.error, re

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs", "ourkids")
os.makedirs(DOCS, exist_ok=True)
FIN_START = "2024-08-05"
today = datetime.date.today()
END = today - datetime.timedelta(days=1)                 # yesterday, last full day
AD_START = END - datetime.timedelta(days=59)             # 60-day ad window
def dates_between(a, b):
    out, d = [], a
    while d <= b:
        out.append(d.isoformat()); d += datetime.timedelta(days=1)
    return out
def log(*a): sys.stderr.write("[ourkids] " + " ".join(str(x) for x in a) + "\n")

# ----------------------------------------------------------------------------- HTTP
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

# ----------------------------------------------------------------------------- ODOO
def odoo_creds():
    s = os.environ.get("ODOO_SERVER", "").strip()
    if s:
        return s, os.environ["ODOO_DB"], os.environ["ODOO_LOGIN"], os.environ["ODOO_APIKEY"]
    p = os.path.join(ROOT, "odoo_read.py")                # fall back to the read-only helper's constants
    if os.path.exists(p):
        t = open(p).read()
        g = lambda k: re.search(k + r'\s*=\s*"([^"]+)"', t).group(1)
        return g("SERVER"), g("DB"), g("LOGIN"), g("APIKEY")
    raise RuntimeError("no Odoo credentials (set ODOO_SERVER/DB/LOGIN/APIKEY)")

_ODOO_UID = [None]
def odoo_rpc(service, method, args, server, key):
    payload = {"jsonrpc": "2.0", "method": "call",
               "params": {"service": service, "method": method, "args": args}}
    d = http_json(server + "/jsonrpc", payload)
    return d.get("result") if isinstance(d, dict) else None

def odoo_group(model, domain, fields, groupby, server, db, login, key):
    if _ODOO_UID[0] is None:
        _ODOO_UID[0] = odoo_rpc("common", "authenticate", [db, login, key, {}], server, key)
    uid = _ODOO_UID[0]
    return odoo_rpc("object", "execute_kw",
                    [db, uid, key, model, "read_group", [domain, fields, groupby], {"lazy": False}],
                    server, key) or []

def pull_odoo():
    server, db, login, key = odoo_creds()
    st = ["sale", "done"]
    # daily totals: revenue + margin + order count
    rows = odoo_group("sale.order",
        [["state", "in", st], ["date_order", ">=", FIN_START + " 00:00:00"]],
        ["amount_total", "margin"], ["date_order:day"], server, db, login, key)
    def dkey(s):
        return datetime.datetime.strptime(s, "%d %b %Y").date().isoformat()
    daily = {}
    for r in rows:
        try: d = dkey(r["date_order:day"])
        except Exception: continue
        daily[d] = {"rev": r.get("amount_total", 0) or 0, "gp": r.get("margin", 0) or 0,
                    "orders": r.get("__count", r.get("date_order:day_count", 0)) or 0}
    # daily refunds (posted credit notes)
    rf = odoo_group("account.move",
        [["move_type", "=", "out_refund"], ["state", "=", "posted"], ["invoice_date", ">=", FIN_START]],
        ["amount_total"], ["invoice_date:day"], server, db, login, key)
    refund = {}
    for r in rf:
        try: d = dkey(r["invoice_date:day"])
        except Exception: continue
        refund[d] = r.get("amount_total", 0) or 0
    # monthly channel split
    teams = {}
    for name in ["Shopify", "Noon", "Amazon", "Homzmart"]:
        mr = odoo_group("sale.order",
            [["state", "in", st], ["date_order", ">=", FIN_START + " 00:00:00"], ["team_id.name", "=", name]],
            ["amount_total"], ["date_order:month"], server, db, login, key)
        for r in mr:
            try:
                mk = datetime.datetime.strptime(r["date_order:month"], "%B %Y").strftime("%Y-%m")
            except Exception:
                continue
            teams.setdefault(name, {})[mk] = r.get("amount_total", 0) or 0
    # assemble consecutive daily arrays in thousands
    ds = dates_between(datetime.date.fromisoformat(FIN_START), END)
    rev = [int(round((daily.get(d, {}).get("rev", 0)) / 1000)) for d in ds]
    gp = [int(round((daily.get(d, {}).get("gp", 0)) / 1000)) for d in ds]
    orders = [int(round(daily.get(d, {}).get("orders", 0))) for d in ds]
    ref = [int(round((refund.get(d, 0)) / 1000)) for d in ds]
    months = sorted({d[:7] for d in ds})
    chM = {c: [int(round(teams.get(c, {}).get(m, 0))) for m in months] for c in ["Shopify", "Noon", "Amazon", "Homzmart"]}
    log("odoo days", len(ds), "last rev(k)", rev[-1] if rev else 0)
    return {"start": FIN_START, "n": len(ds), "k": 1000, "rev": rev, "gp": gp,
            "refund": ref, "orders": orders, "chMonths": months, "chM": chM}

# ----------------------------------------------------------------------------- META
GRAPH = "https://graph.facebook.com/v21.0"
def meta_get(path, params):
    tok = os.environ.get("META_ACCESS_TOKEN", "").strip()
    p = dict(params); p["access_token"] = tok
    return http_json("%s/%s?%s" % (GRAPH, path, urllib.parse.urlencode(p)), None)

def _av(actions, keys):
    for a in actions or []:
        if a.get("action_type") in keys:
            try: return float(a.get("value") or 0)
            except Exception: return 0.0
    return 0.0

def pull_meta(win):
    acct = os.environ.get("META_ACCOUNT_ID", "act_336343742536460").strip()
    ad = {k: {d: 0.0 for d in win} for k in ["mspend", "mecomrev", "metaOmniValue", "instoreMeta", "metaOfflinePur"]}
    d = meta_get("%s/insights" % acct, {
        "level": "account", "time_increment": 1,
        "time_range": json.dumps({"since": win[0], "until": win[-1]}),
        "fields": "spend,action_values,actions", "limit": 500})
    for row in (d.get("data") or []):
        day = row.get("date_start")
        if day not in ad["mspend"]:
            continue
        ad["mspend"][day] = float(row.get("spend") or 0)
        av = row.get("action_values") or []
        pixel = _av(av, ("offsite_conversion.fb_pixel_purchase",))
        omni = _av(av, ("omni_purchase",))
        ad["mecomrev"][day] = pixel
        ad["metaOmniValue"][day] = omni or pixel
        ad["instoreMeta"][day] = max(0.0, (omni or pixel) - pixel)
        ad["metaOfflinePur"][day] = _av(row.get("actions"), ("offline_conversion.purchase", "offline_conversion.fb_pixel_purchase"))
    log("meta days", sum(1 for v in ad["mspend"].values() if v))
    return ad

# ----------------------------------------------------------------------------- SHOPIFY
def shopify_ql(ql):
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    ver = os.environ.get("SHOPIFY_API_VERSION", "2025-01").strip()
    if not store or not tok: return None
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    q = 'query($ql:String!){ shopifyqlQuery(query:$ql){ tableData{ columns{ name } rows } } }'
    d = http_json("https://%s/admin/api/%s/graphql.json" % (host, ver),
                  {"query": q, "variables": {"ql": ql}},
                  {"X-Shopify-Access-Token": tok})
    td = (((d or {}).get("data") or {}).get("shopifyqlQuery") or {}).get("tableData")
    if not td:
        log("shopify ql failed", str(d)[:120]); return None
    cols = [c["name"] for c in td.get("columns", [])]
    return [dict(zip(cols, r)) for r in td.get("rows", [])]

def pull_shopify(win):
    nd = len(win)
    out = {k: {d: 0.0 for d in win} for k in ["sessions", "atcRatio", "checkoutRatio", "cvr", "newcust", "retcust"]}
    rows = shopify_ql("FROM sessions SHOW sessions, sessions_with_cart_additions, "
                      "sessions_that_reached_checkout, sessions_that_completed_checkout "
                      "TIMESERIES day SINCE -%dd UNTIL today" % (nd + 1))
    for r in (rows or []):
        day = str(r.get("day"))[:10]
        if day not in out["sessions"]: continue
        s = float(r.get("sessions") or 0); atc = float(r.get("sessions_with_cart_additions") or 0)
        chk = float(r.get("sessions_that_reached_checkout") or 0); comp = float(r.get("sessions_that_completed_checkout") or 0)
        out["sessions"][day] = s
        out["atcRatio"][day] = round(atc / s * 100, 2) if s else 0
        out["checkoutRatio"][day] = round(chk / s * 100, 2) if s else 0
        out["cvr"][day] = round(comp / s * 100, 2) if s else 0
    nr = shopify_ql("FROM orders SHOW orders GROUP BY customer_type TIMESERIES day SINCE -%dd UNTIL today" % (nd + 1))
    for r in (nr or []):
        day = str(r.get("day"))[:10]
        if day not in out["newcust"]: continue
        ct = str(r.get("customer_type") or "").lower(); o = float(r.get("orders") or 0)
        if "return" in ct: out["retcust"][day] += o
        else: out["newcust"][day] += o
    log("shopify sessions days", sum(1 for v in out["sessions"].values() if v))
    return out

# ----------------------------------------------------------------------------- GOOGLE ADS
def google_token():
    data = urllib.parse.urlencode({
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("GOOGLE_REFRESH_TOKEN", ""),
        "grant_type": "refresh_token"}).encode()
    try:
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
              headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("access_token")
    except Exception as e:
        log("google token", str(e)[:120]); return None

def pull_google(win):
    out = {"gspend": {d: 0.0 for d in win}, "gecomrev": {d: 0.0 for d in win}}
    dev = os.environ.get("GOOGLE_DEVELOPER_TOKEN", ""); cid = os.environ.get("GOOGLE_CUSTOMER_ID", "")
    login_cid = os.environ.get("GOOGLE_LOGIN_CID", "")
    if not (dev and cid):
        log("google skipped (no dev token / customer id)"); return out
    at = google_token()
    if not at: return out
    gql = ("SELECT segments.date, metrics.cost_micros, metrics.conversions_value "
           "FROM customer WHERE segments.date BETWEEN '%s' AND '%s'" % (win[0], win[-1]))
    hd = {"Authorization": "Bearer " + at, "developer-token": dev, "Content-Type": "application/json"}
    if login_cid: hd["login-customer-id"] = login_cid
    url = "https://googleads.googleapis.com/v18/customers/%s/googleAds:searchStream" % cid
    try:
        req = urllib.request.Request(url, data=json.dumps({"query": gql}).encode(), headers=hd)
        with urllib.request.urlopen(req, timeout=90) as resp:
            batches = json.loads(resp.read())
    except Exception as e:
        log("google query", str(e)[:160]); return out
    for b in (batches if isinstance(batches, list) else [batches]):
        for row in b.get("results", []):
            day = row.get("segments", {}).get("date")
            if day not in out["gspend"]: continue
            out["gspend"][day] += float(row.get("metrics", {}).get("costMicros", 0)) / 1e6
            out["gecomrev"][day] += float(row.get("metrics", {}).get("conversionsValue", 0))
    log("google days", sum(1 for v in out["gspend"].values() if v))
    return out

# ----------------------------------------------------------------------------- TIKTOK (optional)
def pull_tiktok(win):
    out = {"tspend": {d: 0.0 for d in win}, "ttValue": {d: 0.0 for d in win}}
    tok = os.environ.get("TIKTOK_TOKEN", "").strip(); adv = os.environ.get("TIKTOK_ADVERTISER_ID", "").strip()
    if not (tok and adv):
        log("tiktok skipped (no token/advertiser)"); return out
    params = {"advertiser_id": adv, "report_type": "BASIC", "data_level": "AUCTION_ADVERTISER",
              "dimensions": json.dumps(["stat_time_day"]),
              "metrics": json.dumps(["spend", "complete_payment", "total_complete_payment_rate"]),
              "start_date": win[0], "end_date": win[-1], "page_size": 1000}
    url = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/?" + urllib.parse.urlencode(params)
    d = http_json(url, None, {"Access-Token": tok})
    for row in (((d or {}).get("data") or {}).get("list") or []):
        day = str(row.get("dimensions", {}).get("stat_time_day", ""))[:10]
        if day not in out["tspend"]: continue
        m = row.get("metrics", {})
        out["tspend"][day] = float(m.get("spend") or 0)
        out["ttValue"][day] = float(m.get("complete_payment") or 0)
    log("tiktok days", sum(1 for v in out["tspend"].values() if v))
    return out

# ----------------------------------------------------------------------------- ASSEMBLE
ATTR = {"order": ["default", "7dc", "1dc", "incr"],
        "labels": {"default": "Default · Meta 7d-click / 1d-view (LIVE)", "7dc": "7-day click (modeled)",
                   "1dc": "1-day click (modeled)", "incr": "Incremental (modeled · not a lift test)"},
        "meta": {"default": 1.0, "7dc": 0.94, "1dc": 0.78, "incr": 0.6},
        "google": {"default": 1.0, "7dc": 1.0, "1dc": 0.85, "incr": 0.68},
        "tt": {"default": 1.0, "7dc": 0.96, "1dc": 0.8, "incr": 0.55}}
SRC = {"revenue": "Odoo sale.order (state sale/done), amount_total, all 4 online channels, by date_order. GROSS.",
       "refund": "Odoo account.move move_type=out_refund, posted, by invoice_date. Returns/credit notes.",
       "netrev": "Gross Odoo revenue minus Odoo posted credit notes (out_refund).",
       "gp": "Odoo sale.order margin field (revenue minus purchase_price cost).",
       "orders": "Odoo confirmed-order count for history; Shopify orders for ad-window.",
       "sessions": "Shopify online store sessions (ShopifyQL).", "cvr": "Shopify completed checkouts / sessions.",
       "aov": "Revenue / orders.", "spend": "Meta amount_spent + Google Ads cost + TikTok spend.",
       "broas": "(Google conv value + Meta pixel value + TikTok complete-payment) / spend. Attribution per toggle.",
       "mer": "Net Odoo revenue / total ad spend.", "cac": "Total ad spend / new customers.",
       "gpp": "Odoo margin / orders.", "cacgp": "CAC / GP-per-order.",
       "instoreMeta": "Meta omni value minus pixel value = offline/in-store attributed.",
       "channels": "Odoo sale.order revenue by crm.team over selected range."}

def build():
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    win = dates_between(AD_START, END)
    # sources, each defensive
    def safe(fn, *a):
        try: return fn(*a)
        except Exception as e:
            log(fn.__name__, "FAILED", str(e)[:160]); return None
    fin = safe(pull_odoo)
    meta = safe(pull_meta, win) or {}
    shop = safe(pull_shopify, win) or {}
    goog = safe(pull_google, win) or {}
    tik = safe(pull_tiktok, win) or {}
    if not fin:
        log("FATAL: Odoo failed, keeping previous data.js"); return
    def arr(dct, k):
        m = dct.get(k, {}) if dct else {}
        return [round(m.get(d, 0), 2) if k in ("atcRatio", "checkoutRatio", "cvr") else int(round(m.get(d, 0))) for d in win]
    ad = {"start": win[0], "n": len(win),
          "spend": [int(round((meta.get("mspend", {}).get(d, 0)) + (goog.get("gspend", {}).get(d, 0)) + (tik.get("tspend", {}).get(d, 0)))) for d in win],
          "mspend": arr(meta, "mspend"), "gspend": arr(goog, "gspend"), "tspend": arr(tik, "tspend"),
          "sessions": arr(shop, "sessions"), "gecomrev": arr(goog, "gecomrev"),
          "mecomrev": arr(meta, "mecomrev"), "ttValue": arr(tik, "ttValue"),
          "metaOmniValue": arr(meta, "metaOmniValue"), "instoreMeta": arr(meta, "instoreMeta"),
          "newcust": arr(shop, "newcust"), "retcust": arr(shop, "retcust"),
          "atcRatio": arr(shop, "atcRatio"), "checkoutRatio": arr(shop, "checkoutRatio"), "cvr": arr(shop, "cvr")}
    online = {"cur": "EGP", "lastSync": ts, "fin": fin, "ad": ad, "attr": ATTR, "src": SRC,
              "aw": [win[0], win[-1]]}
    # static offline block (payroll exact from Excel; branch revenue/rent editable in-app)
    off_path = os.path.join(DOCS, "offline.json")
    if os.path.exists(off_path):
        off = json.load(open(off_path))
    else:
        off = json.loads(OFFLINE_JSON)
    off["meta"]["offlineValue"] = int(round(sum(meta.get("instoreMeta", {}).values()))) or off["meta"].get("offlineValue", 0)
    off["meta"]["offlinePur"] = int(round(sum(meta.get("metaOfflinePur", {}).values()))) or off["meta"].get("offlinePur", 0)
    out = "window.O=" + json.dumps(online, separators=(",", ":"), ensure_ascii=True) + ";\n"
    out += "window.F=" + json.dumps(off, separators=(",", ":"), ensure_ascii=True) + ";"
    open(os.path.join(DOCS, "data.js"), "w").write(out)
    log("WROTE", os.path.join(DOCS, "data.js"), len(out), "bytes  synced", ts)

OFFLINE_JSON = r'''{"currency":"EGP","brand":"OurKids","branches":[{"name":"Dokki","payroll":247027,"hc":25,"aov":1328.4,"revEst":3857585,"rentEst":308607,"opexEst":192879},{"name":"Mall of Arabia","payroll":195636,"hc":17,"aov":1286.0,"revEst":3055060,"rentEst":244405,"opexEst":152753},{"name":"New Cairo","payroll":192211,"hc":16,"aov":1329.3,"revEst":3001576,"rentEst":240126,"opexEst":150079},{"name":"Zayed","payroll":181843,"hc":17,"aov":991.9,"revEst":2839668,"rentEst":227173,"opexEst":141983},{"name":"Nasr City","payroll":171890,"hc":19,"aov":1303.0,"revEst":2684242,"rentEst":214739,"opexEst":134212},{"name":"October","payroll":149101,"hc":13,"aov":1206.0,"revEst":2328368,"rentEst":186269,"opexEst":116418},{"name":"Smouha","payroll":139685,"hc":14,"aov":1050.0,"revEst":2181327,"rentEst":174506,"opexEst":109066}],"company":{"payrollTotal":2906175,"branchPayroll":1277393,"warehousePayroll":420305,"ecomPayroll":372076,"hqPayroll":783651,"envelope":52750,"gpPct":0.266,"refundRate":0.175,"overheadPoolDefault":1203956,"aggRetailMonthly":19947826},"meta":{"offlineValue":1016656,"offlinePur":664,"window":"25 Jun \u2013 24 Jul 2026"},"attr":{"order":["default","7dc","1dc","incr"],"labels":{"default":"Default 7DC/1DV (LIVE)","7dc":"7-day click (modeled)","1dc":"1-day click (modeled)","incr":"Incremental (modeled)"},"meta":{"default":1.0,"7dc":0.94,"1dc":0.78,"incr":0.6}},"notes":{"revenue":"Branch revenue is an EDITABLE ESTIMATE (payroll-weighted split of the ERP-audit E\u00a3458.8M since Aug-2024 \u2248 19.95M/mo). Real POS revenue is walled off from the read-only Odoo account (audit S-01). Type real per-branch numbers to make breakeven exact.","rent":"Rent + opex are EDITABLE placeholders (8% / 5% of revenue). Enter your real lease + running costs.","payroll":"Payroll is EXACT \u2014 Excel 'OurKids payroll by function', June 2026.","gp":"Contribution margin uses net GP% 26.6% (Odoo margin, recent) and refund rate 17.5% (ERP audit S-03).","newret":"Per-branch new/returning split needs POS access (walled). Online new/returning shown on the main dashboard."}}'''

if __name__ == "__main__":
    build()
