"""OurKids ad bot -- data layer.

Pulls everything the daily rules need, read-only, from the same keys the dashboard
collector already uses (GitHub secrets): Meta Graph API, Odoo XML-RPC, Shopify Admin
GraphQL. Nothing here writes to any of them.
"""
import json, os, re, time, datetime as dt, urllib.parse, xmlrpc.client
import requests

GRAPH = "https://graph.facebook.com/v21.0"
ACT = os.environ.get("META_ACCOUNT_ID", "336343742536460").replace("act_", "")
TOK = os.environ.get("META_ACCESS_TOKEN", "")
# the five shops/warehouses that ship online orders + the online staging bin (user-confirmed
# 19 Oct 2026); transit bins are stock on a truck, not stock you can sell today
FULFIL_LOCS = {94: "Stock Warehouse", 18: "Dokki", 30: "October", 36: "Zayed", 42: "Nasr City", 88: "FULL/Shopify"}


def log(*a):
    print("[adbot]", *a, flush=True)


# ------------------------------------------------------------------ Meta
def g(path, params=None, tries=4):
    p = dict(params or {}); p["access_token"] = TOK
    for k in range(tries):
        r = requests.get(GRAPH + "/" + path.lstrip("/"), params=p, timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503) or "temporarily" in r.text:
            time.sleep(10 * (k + 1)); continue
        raise RuntimeError("Graph %s %s: %s" % (path, r.status_code, r.text[:300]))
    raise RuntimeError("Graph %s: retries exhausted" % path)


def g_all(path, params=None, cap=20000):
    out = []; d = g(path, params)
    while True:
        out += d.get("data", [])
        nxt = (d.get("paging") or {}).get("next")
        if not nxt or len(out) >= cap:
            return out
        r = requests.get(nxt, timeout=120)
        if r.status_code != 200:
            time.sleep(5); r = requests.get(nxt, timeout=120)
        d = r.json()


def token_scopes():
    """Which permissions the token carries -- Approve needs ads_management."""
    try:
        d = g("me/permissions")
        return sorted(x["permission"] for x in d.get("data", []) if x.get("status") == "granted")
    except Exception as e:
        return ["unknown: " + str(e)[:80]]


def _act(lst, key, win=None):
    for a in lst or []:
        if a.get("action_type") == key:
            return float((a.get(win) if win else a.get("value")) or 0)
    return 0.0


def insights_weekly(days=364):
    """Per ad per week: spend, impressions, outbound clicks, online purchases/value split
    into 7-day click and 1-day view, in-store (offline) purchases/value. Async report:
    a year of ad-level weeks is too big for a synchronous call."""
    until = dt.date.today() - dt.timedelta(days=1)
    since = until - dt.timedelta(days=days - 1)
    params = {
        "level": "ad", "time_increment": 7,
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,spend,impressions,reach,frequency,outbound_clicks,actions,action_values",
        "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
        "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}]),
        "limit": 500, "access_token": TOK, "async": "true"}
    r = requests.post(GRAPH + "/act_%s/insights" % ACT, data=params, timeout=120)
    if r.status_code != 200:
        raise RuntimeError("insights async start: " + r.text[:300])
    run = r.json()["report_run_id"]
    for k in range(240):
        st = g(run, {"fields": "async_status,async_percent_completion"})
        if st.get("async_status") == "Job Completed":
            break
        if st.get("async_status") in ("Job Failed", "Job Skipped"):
            raise RuntimeError("insights job " + st.get("async_status"))
        time.sleep(5)
    rows = g_all(run + "/insights", {"limit": 500}, cap=200000)
    out = []
    for x in rows:
        A, V = x.get("actions"), x.get("action_values")
        oc = sum(float(a.get("value") or 0) for a in (x.get("outbound_clicks") or []))
        out.append({
            "ad": x["ad_id"], "ad_name": x.get("ad_name", ""), "adset": x.get("adset_id"), "adset_name": x.get("adset_name", ""),
            "camp": x.get("campaign_id"), "camp_name": x.get("campaign_name", ""), "wk": x["date_start"],
            "sp": float(x.get("spend") or 0), "im": float(x.get("impressions") or 0), "oc": oc,
            "rch": float(x.get("reach") or 0), "fq": float(x.get("frequency") or 0),
            "pu7": _act(A, "offsite_conversion.fb_pixel_purchase", "7d_click"), "puV": _act(A, "offsite_conversion.fb_pixel_purchase", "1d_view"),
            "pv7": _act(V, "offsite_conversion.fb_pixel_purchase", "7d_click"), "pvV": _act(V, "offsite_conversion.fb_pixel_purchase", "1d_view"),
            "op7": _act(A, "offline_conversion.purchase", "7d_click"), "opV": _act(A, "offline_conversion.purchase", "1d_view"),
            "ov7": _act(V, "offline_conversion.purchase", "7d_click"), "ovV": _act(V, "offline_conversion.purchase", "1d_view"),
        })
    log("insights weekly rows", len(out), since, "->", until)
    return out


def adsets():
    return g_all("act_%s/adsets" % ACT, {"limit": 200, "fields":
        "id,name,campaign_id,effective_status,daily_budget,lifetime_budget,bid_strategy,bid_amount,created_time,"
        "targeting{custom_audiences,excluded_custom_audiences},promoted_object,optimization_goal"})


def campaigns():
    return g_all("act_%s/campaigns" % ACT, {"limit": 200, "fields":
        "id,name,effective_status,daily_budget,lifetime_budget,bid_strategy,objective,created_time"})


def audiences():
    return g_all("act_%s/customaudiences" % ACT, {"limit": 200, "fields": "id,name,subtype,approximate_count_lower_bound"})


def live_ads():
    return g_all("act_%s/ads" % ACT, {"limit": 200,
        "effective_status": json.dumps(["ACTIVE"]),
        "fields": "id,name,adset_id,campaign_id,created_time,effective_status,"
                  "creative{id,object_type,link_url,product_set_id,template_url,object_story_spec,asset_feed_spec}"})


def activities(days=365):
    """The account's change history (budgets, pauses, new ads/ad sets, targeting, bids),
    paged month by month so no window hits the row cap."""
    out = []; end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=days)
    a = start
    while a < end:
        b = min(a + dt.timedelta(days=31), end)
        try:
            out += g_all("act_%s/activities" % ACT, {"since": a.isoformat(), "until": b.isoformat(), "limit": 500,
                         "fields": "event_type,event_time,object_id,object_name,object_type,extra_data,actor_name"}, cap=50000)
        except Exception as e:
            log("activities", a, "failed:", str(e)[:120])
        a = b
    keep = [x for x in out if x.get("event_type") not in ("ad_account_billing_charge", "funding_event_successful", "funding_event_initiated")]
    log("activities", len(out), "rows,", len(keep), "non-billing,", start, "->", end)
    return keep


def creative_link(c):
    """The landing URL of a creative, from wherever Meta stores it."""
    c = c or {}
    if c.get("link_url"):
        return c["link_url"]
    oss = c.get("object_story_spec") or {}
    for k in ("link_data", "video_data", "template_data"):
        d = oss.get(k) or {}
        u = d.get("link") or ((d.get("call_to_action") or {}).get("value") or {}).get("link")
        if u:
            return u
        for ch in d.get("child_attachments") or []:
            if ch.get("link"):
                return ch["link"]
    afs = c.get("asset_feed_spec") or {}
    for lu in afs.get("link_urls") or []:
        if lu.get("website_url"):
            return lu["website_url"]
    return c.get("template_url") or ""


def product_set_items(ps_id, cap=3000):
    """Items in a catalogue product set -- what a DPA ad can show."""
    return g_all(ps_id + "/products", {"limit": 500,
        "fields": "retailer_id,gtin,availability,inventory,retailer_product_group_id,name"}, cap=cap)


# ------------------------------------------------------------------ Odoo
def odoo():
    s = os.environ["ODOO_SERVER"].strip().rstrip("/")
    db, user, key = os.environ["ODOO_DB"], os.environ["ODOO_LOGIN"], os.environ["ODOO_APIKEY"]
    uid = xmlrpc.client.ServerProxy(s + "/xmlrpc/2/common", allow_none=True).authenticate(db, user, key, {})
    m = xmlrpc.client.ServerProxy(s + "/xmlrpc/2/object", allow_none=True)
    def call(model, method, args, kw=None):
        return m.execute_kw(db, uid, key, model, method, args, kw or {})
    return call


def fulfillable_stock(call):
    """barcode -> units you can ship online today (on hand minus reserved, five locations + FULL/Shopify)."""
    rows = call("stock.quant", "read_group",
                [[["location_id", "in", list(FULFIL_LOCS)]], ["quantity", "reserved_quantity"], ["product_id"]],
                {"lazy": False})
    pq = {}
    for r in rows:
        pid = (r.get("product_id") or [0])[0]
        if pid:
            pq[pid] = float(r.get("quantity") or 0) - float(r.get("reserved_quantity") or 0)
    out = {}
    ids = list(pq)
    for i in range(0, len(ids), 2000):
        for p in call("product.product", "read", [ids[i:i + 2000]], {"fields": ["barcode", "default_code"]}):
            q = pq.get(p["id"], 0)
            for k in (p.get("barcode"), p.get("default_code")):
                if k:
                    out[str(k).strip()] = out.get(str(k).strip(), 0) + q
    log("fulfillable stock keys", len(out), "from", len(pq), "products")
    return out


def online_sales_weights(call, days=30):
    """barcode -> online revenue over the last N days (weights for the DPA index)."""
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = call("sale.order.line", "read_group",
                [[["order_id.team_id.name", "ilike", "shopify"], ["order_id.state", "in", ["sale", "done"]],
                  ["order_id.date_order", ">=", since + " 00:00:00"], ["display_type", "=", False]],
                 ["price_subtotal"], ["product_id"]], {"lazy": False})
    w = {}; ids = []
    for r in rows:
        pid = (r.get("product_id") or [0])[0]
        if pid:
            w[pid] = float(r.get("price_subtotal") or 0); ids.append(pid)
    out = {}
    for i in range(0, len(ids), 2000):
        for p in call("product.product", "read", [ids[i:i + 2000]], {"fields": ["barcode"]}):
            if p.get("barcode"):
                out[p["barcode"]] = out.get(p["barcode"], 0) + w.get(p["id"], 0)
    return out


# ------------------------------------------------------------------ Shopify
def shop_q(query, variables=None):
    store = os.environ["SHOPIFY_STORE"].strip()
    host = store if ".myshopify.com" in store else store + ".myshopify.com"
    for k in range(4):
        r = requests.post("https://%s/admin/api/2025-07/graphql.json" % host,
                          json={"query": query, "variables": variables or {}},
                          headers={"X-Shopify-Access-Token": os.environ["SHOPIFY_TOKEN"].strip()}, timeout=60)
        d = r.json() if r.status_code == 200 else {}
        if d.get("errors") and "THROTTLED" in json.dumps(d["errors"]):
            time.sleep(3 * (k + 1)); continue
        return d
    return {}


SORTS = {"best-selling": "BEST_SELLING", "price-ascending": "PRICE", "price-descending": "PRICE",
         "created-descending": "CREATED", "created-ascending": "CREATED", "title-ascending": "TITLE",
         "title-descending": "TITLE", "manual": "MANUAL"}
VAR = "variants(first:60){nodes{barcode sku inventoryQuantity}}"


def landing_products(url, n=12):
    """The first products a visitor sees on the ad's landing page, in display order:
    [{'title','handle','variants':[{barcode,sku}]}], plus what kind of page it is."""
    u = urllib.parse.urlparse(url or "")
    path = u.path.rstrip("/"); qs = urllib.parse.parse_qs(u.query)
    m = re.search(r"/products/([^/?#]+)$", path)
    if m:
        d = shop_q("query($h:String!){productByHandle(handle:$h){title handle %s}}" % VAR, {"h": m.group(1)})
        p = ((d.get("data") or {}).get("productByHandle"))
        return ("product", [p] if p else [])
    m = re.search(r"/collections/([^/?#]+)$", path)
    if m:
        sb = (qs.get("sort_by") or [""])[0]
        key = SORTS.get(sb, "COLLECTION_DEFAULT")
        rev = "true" if sb.endswith("descending") else "false"
        d = shop_q("query($h:String!){collectionByHandle(handle:$h){title products(first:%d, sortKey:%s, reverse:%s){nodes{title handle %s}}}}"
                   % (n, key, rev, VAR), {"h": m.group(1)})
        c = (d.get("data") or {}).get("collectionByHandle")
        return ("collection", (c or {}).get("products", {}).get("nodes", []) if c else [])
    return ("other", [])


def variant_barcodes():
    """Shopify variant id -> barcode/sku, for every variant (bulk export). The catalogue's
    retailer_id is the variant id, so this is how a DPA item reaches fulfillable stock."""
    q = 'mutation{bulkOperationRunQuery(query:"""{ productVariants { edges { node { legacyResourceId barcode sku } } } }"""){bulkOperation{id status} userErrors{message}}}'
    d = shop_q(q)
    errs = (((d.get("data") or {}).get("bulkOperationRunQuery") or {}).get("userErrors")) or []
    if errs:
        log("bulk start:", errs)
    url = None
    for k in range(120):
        c = (shop_q("{currentBulkOperation{status url errorCode objectCount}}").get("data") or {}).get("currentBulkOperation") or {}
        if c.get("status") == "COMPLETED":
            url = c.get("url"); break
        if c.get("status") in ("FAILED", "CANCELED", "EXPIRED"):
            log("bulk", c); break
        time.sleep(5)
    out = {}
    if url:
        for line in requests.get(url, timeout=300).text.splitlines():
            try:
                n = json.loads(line)
            except Exception:
                continue
            k = (n.get("barcode") or n.get("sku") or "").strip()
            if n.get("legacyResourceId") and k:
                out[str(n["legacyResourceId"])] = k
    log("variant barcodes", len(out))
    return out


def retailer_variant(rid):
    """'shopify_EG_123_456' / '456' -> '456' (the Shopify variant id)."""
    m = re.findall(r"\d{6,}", str(rid or ""))
    return m[-1] if m else ""
