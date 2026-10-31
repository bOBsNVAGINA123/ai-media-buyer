"""Playmore data file for the dashboard's business switch -> docs/ourkids/playmore_data.js

Same shape as the OurKids data.js keys the P&L reads (fin, shop, ad, exp), so the same page code renders
Playmore. READ-ONLY on Odoo (search/read/read_group only) and on Meta (insights only).

Playmore specifics (surveyed 26 Oct 2026, Odoo 17, isi-play-more.odoo.com):
  * team "Shopify" = the webstore. Team "PlayMore" is wholesale, and ~99% of it is OURKIDS (partner 920):
    intercompany stock moves, with OurKids' returns booked as negative sale orders. Those are kept OUT of
    every total here and reported on their own (chD["OurKids (intercompany)"]), or they would swamp the store.
  * sales tax is 14% (account.tax "14% Sales"), so gross profit = Odoo margin (ex-VAT) and the P&L's
    government-charge line is 14/114 of VAT-inclusive revenue (the page reads O.biz for that).
  * no courier account exists in Playmore's books; delivery cost is missing, not zero.
"""
import os, json, time, datetime, xmlrpc.client, urllib.request, urllib.parse

URL = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
LOGIN, KEY = os.environ["PLAYMORE_ODOO_LOGIN"], os.environ["PLAYMORE_ODOO_APIKEY"]
META_TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
META_ACT = "2619321185136291"
OURKIDS_PARTNER = 920
FIN_START = "2024-04-01"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "ourkids", "playmore_data.js")
SRC = {}


def log(*a):
    print("[playmore]", *a, flush=True)


uid = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/common").authenticate(DB, LOGIN, KEY, {})
if not uid:
    raise SystemExit("Playmore Odoo login failed")
_M = xmlrpc.client.ServerProxy(URL + "/xmlrpc/2/object", allow_none=True)
READ_ONLY = {"search", "search_read", "read", "read_group", "search_count", "fields_get"}


def ox(model, method, args, kw=None):
    if method not in READ_ONLY:
        raise RuntimeError("read-only client: %s refused" % method)
    for k in range(4):
        try:
            return _M.execute_kw(DB, uid, KEY, model, method, args, kw or {})
        except Exception as e:
            if k == 3: raise
            log("retry", model, method, str(e)[:100]); time.sleep(5 * (k + 1))


def group(model, dom, fields, by):
    return ox(model, "read_group", [dom, fields, by], {"lazy": False})


def day(lbl):
    try: return datetime.datetime.strptime(str(lbl), "%d %b %Y").date().isoformat()
    except Exception:
        try: return datetime.date.fromisoformat(str(lbl)[:10]).isoformat()
        except Exception: return None


TODAY = datetime.date.today()
DAYS = [(datetime.date.fromisoformat(FIN_START) + datetime.timedelta(days=i)).isoformat()
        for i in range((TODAY - datetime.date.fromisoformat(FIN_START)).days + 1)]
k = lambda v: int(round((v or 0) / 1000))
OK = [["state", "in", ["sale", "done"]], ["date_order", ">=", FIN_START + " 00:00:00"]]
EXT = [["partner_id", "!=", OURKIDS_PARTNER]]          # everything except intercompany sales to OurKids


def by_day(model, dom, fields, by, f):
    out = {}
    for r in group(model, dom, fields, [by]):
        d = day(r.get(by))
        if d: out[d] = f(r)
    return out


# ---- sales: all external channels (fin) and the webstore (shop)
fin_d = by_day("sale.order", OK + EXT, ["amount_total", "margin"], "date_order:day",
               lambda r: (r.get("amount_total") or 0, r.get("margin") or 0, r.get("__count") or 0))
shop_d = by_day("sale.order", OK + [["team_id.name", "=", "Shopify"]], ["amount_total", "margin"], "date_order:day",
                lambda r: (r.get("amount_total") or 0, r.get("margin") or 0, r.get("__count") or 0))
qty_d = {}   # units per basket: Odoo 17 cannot group order lines by the order date; the P&L does not use it
ic_d = by_day("sale.order", OK + [["partner_id", "=", OURKIDS_PARTNER]], ["amount_total"], "date_order:day",
              lambda r: r.get("amount_total") or 0)
ch_d = {}
for team in ("Shopify", "PlayMore"):
    ch_d[team] = by_day("sale.order", OK + EXT + [["team_id.name", "=", team]], ["amount_total"], "date_order:day",
                        lambda r: r.get("amount_total") or 0)
# ---- refunds: posted credit notes to non-OurKids customers, by posting day (Playmore books few)
ref_d = by_day("account.move", [["move_type", "=", "out_refund"], ["state", "=", "posted"], ["invoice_date", ">=", FIN_START],
                                ["partner_id", "!=", OURKIDS_PARTNER]], ["amount_total"], "invoice_date:day",
               lambda r: r.get("amount_total") or 0)
sref_d = by_day("account.move", [["move_type", "=", "out_refund"], ["state", "=", "posted"], ["invoice_date", ">=", FIN_START],
                                 ["team_id.name", "=", "Shopify"]], ["amount_total"], "invoice_date:day",
                lambda r: r.get("amount_total") or 0)
# ---- RETURNS. Playmore books no credit note when goods come back: the return is only a stock move
# (incoming picking with return_id). Price each returned unit at its original sale line, so the P&L's
# refunds line is real. Dated on the day the goods came back. OurKids (intercompany) returns excluded.
ret_all, ret_shop = {}, {}
RET_ORD, RET_VAL = {}, {}
mv = ox("stock.move", "search_read", [[["picking_id.picking_type_code", "=", "incoming"], ["picking_id.return_id", "!=", False],
                                       ["state", "=", "done"], ["date", ">=", FIN_START], ["sale_line_id", "!=", False]]],
        {"fields": ["date", "quantity", "product_uom_qty", "sale_line_id"], "limit": 100000})
ids = list({m["sale_line_id"][0] for m in mv}); SL = {}
for i in range(0, len(ids), 1000):
    for l in ox("sale.order.line", "read", [ids[i:i + 1000]], {"fields": ["price_total", "product_uom_qty", "order_id"]}):
        SL[l["id"]] = l
oids = list({l["order_id"][0] for l in SL.values()}); OT = {}
for i in range(0, len(oids), 1000):
    for o in ox("sale.order", "read", [oids[i:i + 1000]], {"fields": ["team_id", "partner_id"]}):
        OT[o["id"]] = ((o.get("team_id") or [0, ""])[1], (o.get("partner_id") or [0])[0])
for m in mv:
    l = SL.get(m["sale_line_id"][0])
    if not l: continue
    team, partner = OT.get(l["order_id"][0], ("", 0))
    if partner == OURKIDS_PARTNER: continue
    v = l["price_total"] / (l["product_uom_qty"] or 1) * (m.get("quantity") or m.get("product_uom_qty") or 0)
    d = m["date"][:10]
    ret_all[d] = ret_all.get(d, 0) + v
    if team == "Shopify":
        ret_shop[d] = ret_shop.get(d, 0) + v
        RET_ORD.setdefault(d[:7], set()).add(l["order_id"][0]); RET_VAL[d[:7]] = RET_VAL.get(d[:7], 0) + v
log("returns priced:", len(mv), "moves, E£", round(sum(ret_all.values())))
for d, v in ret_all.items(): ref_d[d] = ref_d.get(d, 0) + v
for d, v in ret_shop.items(): sref_d[d] = sref_d.get(d, 0) + v

# ---- cancellations
cx_d = by_day("sale.order", [["state", "=", "cancel"], ["date_order", ">=", FIN_START + " 00:00:00"]] + EXT, ["amount_total"],
              "date_order:day", lambda r: (r.get("amount_total") or 0, r.get("__count") or 0))
cxs_d = by_day("sale.order", [["state", "=", "cancel"], ["date_order", ">=", FIN_START + " 00:00:00"], ["team_id.name", "=", "Shopify"]],
               ["amount_total"], "date_order:day", lambda r: (r.get("amount_total") or 0, r.get("__count") or 0))
SRC["odoo"] = True
log("odoo days", len(fin_d), "shop days", len(shop_d))

fin = {"start": FIN_START, "n": len(DAYS), "k": 1000,
       "rev": [k(fin_d.get(d, (0, 0, 0))[0]) for d in DAYS],
       "gp": [k(fin_d.get(d, (0, 0, 0))[1]) for d in DAYS],
       "refund": [k(ref_d.get(d, 0)) for d in DAYS], "refundO": [],
       "orders": [int(fin_d.get(d, (0, 0, 0))[2]) for d in DAYS],
       "qty": [int(round(qty_d.get(d, 0))) for d in DAYS],
       "chD": {"Shopify": [k(ch_d["Shopify"].get(d, 0)) for d in DAYS],
               "Wholesale (not OurKids)": [k(ch_d["PlayMore"].get(d, 0)) for d in DAYS],
               "OurKids (intercompany, excluded)": [k(ic_d.get(d, 0)) for d in DAYS]},
       "cxv": [k(cx_d.get(d, (0, 0))[0]) for d in DAYS], "cxn": [int(cx_d.get(d, (0, 0))[1]) for d in DAYS],
       "cxsv": [k(cxs_d.get(d, (0, 0))[0]) for d in DAYS], "cxsn": [int(cxs_d.get(d, (0, 0))[1]) for d in DAYS],
       "cxTeam": {}}
shop = {"rev": [k(shop_d.get(d, (0, 0, 0))[0]) for d in DAYS],
        "gp": [k(shop_d.get(d, (0, 0, 0))[1]) for d in DAYS],
        "ord": [int(shop_d.get(d, (0, 0, 0))[2]) for d in DAYS],
        "ref": [k(sref_d.get(d, 0)) for d in DAYS], "refO": []}

# ---- operating expenses by month and account (posted, 31.* = OpEx), and ad spend booked in Odoo
exp = {}
for r in group("account.move.line", [["parent_state", "=", "posted"], ["date", ">=", FIN_START], ["account_id.code", "=like", "31.%"]],
               ["balance"], ["date:month", "account_id"]):
    try: m = datetime.datetime.strptime(r["date:month"], "%B %Y").strftime("%Y-%m")
    except Exception: continue
    nm = (r.get("account_id") or [0, "?"])[1]
    nm = nm.split(" ", 1)[1] if " " in nm else nm
    exp.setdefault(m, {})[nm] = round((exp.get(m, {}).get(nm, 0)) + (r.get("balance") or 0))
log("opex months", len(exp))

# ---- Meta (Playmore ad account), daily, account level
ad = {"start": FIN_START, "n": len(DAYS)}
keys = ("mspend", "gspend", "tspend", "spend", "mpur", "mecomrev", "metaOmniValue", "instoreMeta", "mimp", "mclk", "moclk",
        "segNew", "segEng", "segExist", "segUnk", "segNewV", "segEngV", "segExistV", "sessions", "gecomrev", "gconv", "ttValue",
        "instoreOnsite", "metaOfflinePur", "instoreNC", "newcust", "retcust", "ncrev", "rcrev", "tpur", "ttOffValue", "ttOffPur",
        "moffv", "gimp", "gclk", "timp", "tclk", "ttShopRev", "ttShopOrd")
for kk in keys: ad[kk] = [0] * len(DAYS)
idx = {d: i for i, d in enumerate(DAYS)}
if META_TOKEN:
    try:
        url = "https://graph.facebook.com/v21.0/act_%s/insights?" % META_ACT + urllib.parse.urlencode({
            "level": "account", "time_increment": 1, "limit": 500,
            "time_range": json.dumps({"since": FIN_START, "until": TODAY.isoformat()}),
            "fields": "spend,impressions,clicks,outbound_clicks,actions,action_values", "access_token": META_TOKEN})
        n = 0
        while url:
            j = json.load(urllib.request.urlopen(url, timeout=120))
            for x in j.get("data", []):
                i = idx.get(x["date_start"])
                if i is None: continue
                act = {a["action_type"]: float(a["value"]) for a in x.get("actions") or []}
                val = {a["action_type"]: float(a["value"]) for a in x.get("action_values") or []}
                sp = float(x.get("spend") or 0)
                ad["mspend"][i] = ad["spend"][i] = round(sp)
                ad["mpur"][i] = int(act.get("offsite_conversion.fb_pixel_purchase", 0))
                ad["mecomrev"][i] = ad["metaOmniValue"][i] = round(val.get("offsite_conversion.fb_pixel_purchase", 0))
                ad["mimp"][i] = int(float(x.get("impressions") or 0)); ad["mclk"][i] = int(float(x.get("clicks") or 0))
                ad["moclk"][i] = int(sum(float(o.get("value") or 0) for o in x.get("outbound_clicks") or []))
                n += 1
            url = (j.get("paging") or {}).get("next")
        SRC["meta"] = n > 0
        log("meta days", n, "spend E£", sum(ad["mspend"]))
    except Exception as e:
        SRC["meta"] = False
        log("meta FAILED (token may not reach the Playmore ad account):", str(e)[:200])
else:
    SRC["meta"] = False; log("no META_ACCESS_TOKEN")

# ---- DELIVERY (ship2 + bosta, the shapes the P&L's delivery block reads).
# Actual from Odoo: Shopify orders per month, which paid a shipping line and how much, parcels sent
# (outgoing deliveries done) and parcels back (returns). NOT in Playmore's books: the courier bill
# (Bosta postings stopped Nov 2025, no fee ever booked), so cost stays 0 and the page prices the
# parcels at the measured Bosta rate per type, marked EST. Rates are OurKids' Bosta pull (same
# courier) until Playmore's own Bosta account is connected.
BOSTA_FEES = {"Send": {"mean": 70.33}, "Return to Origin": {"mean": 55.53}}
mon = lambda ts: ts[:7]
so = ox("sale.order", "search_read", [[["team_id.name", "=", "Shopify"], ["state", "in", ["sale", "done"]], ["date_order", ">=", FIN_START + " 00:00:00"],
                                         ["partner_id", "!=", OURKIDS_PARTNER]]], {"fields": ["date_order", "amount_total"], "limit": 200000})
OM = {o["id"]: mon(o["date_order"]) for o in so}
conf, confV = {}, {}
for o in so:
    m = OM[o["id"]]; conf[m] = conf.get(m, 0) + 1; confV[m] = confV.get(m, 0) + (o.get("amount_total") or 0)
shp = ox("sale.order.line", "search_read", [[["order_id", "in", list(OM)], ["product_id.detailed_type", "=", "service"],
                                              ["product_id.name", "ilike", "shipping"], ["price_subtotal", ">", 0]]],
         {"fields": ["order_id", "price_subtotal"], "limit": 200000})
paid, col = {}, {}
seen = set()
for l in shp:
    oid = l["order_id"][0]; m = OM.get(oid)
    if not m: continue
    col[m] = col.get(m, 0) + l["price_subtotal"]
    if oid not in seen: seen.add(oid); paid[m] = paid.get(m, 0) + 1
pk = ox("stock.picking", "search_read", [[["picking_type_code", "=", "outgoing"], ["state", "=", "done"], ["date_done", ">=", FIN_START],
                                          ["sale_id", "in", list(OM)]]], {"fields": ["date_done"], "limit": 200000})
sends = {}
for p_ in pk:
    m = mon(p_["date_done"]); sends[m] = sends.get(m, 0) + 1
months = sorted(set(conf) | set(sends))
ship2 = {"m": months, "conf": [conf.get(m, 0) for m in months], "ret": [len(RET_ORD.get(m, ())) for m in months],
         "kept": [max(conf.get(m, 0) - len(RET_ORD.get(m, ())), 0) for m in months],
         "retV": [round(RET_VAL.get(m, 0)) for m in months], "keptV": [round(max(confV.get(m, 0) - RET_VAL.get(m, 0), 0)) for m in months],
         "canc": [0] * len(months), "cancV": [0] * len(months),
         "paidN": [paid.get(m, 0) for m in months], "freeN": [max(conf.get(m, 0) - paid.get(m, 0), 0) for m in months],
         "col": [round(col.get(m, 0)) for m in months], "colAcc": [0] * len(months),
         "cost": [0] * len(months), "costN": [0] * len(months), "lagM": months[-1] if months else "", "retFrom": FIN_START}
bosta = {"pulled": TODAY.isoformat(), "fees": BOSTA_FEES, "feeSource": "OurKids Bosta rates (Playmore's own Bosta account not connected)",
         "months": {m: {"n": sends.get(m, 0) + len(RET_ORD.get(m, ())), "byType": {"Send": sends.get(m, 0), "Return to Origin": len(RET_ORD.get(m, ()))},
                        "sends": sends.get(m, 0), "ret": len(RET_ORD.get(m, ()))} for m in months}}
log("delivery: orders", sum(conf.values()), "paid shipping", sum(paid.values()), "collected E£", round(sum(col.values())),
    "sends", sum(sends.values()), "returned orders", sum(len(v) for v in RET_ORD.values()))

# ---- DAYS OF INVENTORY LEFT (DOR) + what to do with each product.
# On hand = quantity - reserved in every internal location (Go.PlayMore + Season). Sold = units on confirmed
# sale orders, split webstore / OurKids (intercompany: it empties Playmore's shelves too) / other wholesale;
# negative lines (OurKids sending stock back) are not sales. DOR = on hand / units per day (last 30 days).
# ponytail: flat 30/90-day rates, not units / days-in-stock; a product sold out for part of the window reads
# slower than it is. Add stock history if that matters.
import re
WIN = 30
since = (TODAY - datetime.timedelta(days=180)).isoformat() + " 00:00:00"
so180 = ox("sale.order", "search_read", [[["state", "in", ["sale", "done"]], ["date_order", ">=", since]]],
           {"fields": ["partner_id", "team_id", "date_order"], "limit": 200000})
chan, odate = {}, {}
for o in so180:
    pid = (o.get("partner_id") or [0])[0]
    chan[o["id"]] = "ourkids" if pid == OURKIDS_PARTNER else ("web" if (o.get("team_id") or [0, ""])[1] == "Shopify" else "other")
    odate[o["id"]] = o["date_order"][:10]
lines = ox("sale.order.line", "search_read", [[["order_id", "in", list(chan)], ["display_type", "=", False],
                                                 ["product_id.detailed_type", "=", "product"]]],
           {"fields": ["order_id", "product_id", "product_uom_qty"], "limit": 1000000})
d30, d90 = (TODAY - datetime.timedelta(days=30)).isoformat(), (TODAY - datetime.timedelta(days=90)).isoformat()
sold = {}
for l in lines:
    q = l.get("product_uom_qty") or 0
    if q <= 0: continue
    oid = l["order_id"][0]; p_ = l["product_id"][0]; c = chan.get(oid, "other"); dd = odate.get(oid, "")
    e = sold.setdefault(p_, {"web": 0.0, "ourkids": 0.0, "other": 0.0, "web90": 0.0, "all90": 0.0, "all180": 0.0, "last": ""})
    e["all180"] += q; e["last"] = max(e["last"], dd)
    if dd >= d90: e["all90"] += q; e["web90"] += q if c == "web" else 0
    if dd >= d30: e[c] += q
quants = group("stock.quant", [["location_id.usage", "=", "internal"]], ["quantity", "reserved_quantity"], ["product_id"])
onhand = {}
for r in quants:
    p_ = (r.get("product_id") or [0])[0]
    if p_: onhand[p_] = (r.get("quantity") or 0) - (r.get("reserved_quantity") or 0)
pids = list(set(onhand) | set(sold)); PR = {}
for i in range(0, len(pids), 1000):
    for p_ in ox("product.product", "read", [pids[i:i + 1000]], {"fields": ["product_tmpl_id", "display_name", "default_code", "barcode", "standard_price", "lst_price"]}):
        PR[p_["id"]] = p_

# webstore photo + link: goplaymore.com's public product feed, matched by variant SKU (= Odoo barcode or
# internal reference) and, failing that, by the style number the handle starts with.
SKU, STYLE = {}, {}
for pg in range(1, 12):
    try:
        req = urllib.request.Request("https://goplaymore.com/products.json?limit=250&page=%d" % pg, headers={"User-Agent": "Mozilla/5.0"})
        prods = json.load(urllib.request.urlopen(req, timeout=60)).get("products", [])
    except Exception as e:
        log("shop feed page", pg, "failed", str(e)[:80]); time.sleep(3); continue
    if not prods: break
    for pr in prods:
        img = (pr.get("images") or [{}])[0].get("src", "")
        info = {"url": "https://goplaymore.com/products/" + pr["handle"], "img": img, "live": any(v.get("available") for v in pr.get("variants", []))}
        for v in pr.get("variants", []):
            if v.get("sku"): SKU[str(v["sku"]).strip()] = info
        m = re.match(r"(\d{4,6})", pr["handle"])
        if m: STYLE.setdefault(m.group(1), info)

def season_of(code, name):
    m = re.search(r"\b([WS])(\d{2})\b", "%s %s" % (code or "", name or "")) or re.search(r"-([WS])(\d{2})-", code or "")
    return (m.group(1) + m.group(2)) if m else ""
yy = TODAY.year % 100
CUR = ("W%02d" % yy) if TODAY.month >= 9 else ("W%02d" % (yy - 1)) if TODAY.month <= 2 else ("S%02d" % yy)
NEXT = ("S%02d" % (yy + 1)) if CUR.startswith("W") else ("W%02d" % yy)

tm = {}
for p_, info in PR.items():
    t = (info.get("product_tmpl_id") or [0, info.get("display_name")])
    e = tm.setdefault(t[0], {"id": t[0], "name": t[1], "code": info.get("default_code") or "", "onhand": 0.0, "web": 0.0, "ourkids": 0.0, "other": 0.0,
                             "web90": 0.0, "all90": 0.0, "all180": 0.0, "last": "", "value": 0.0, "retail": 0.0, "sizes": 0, "sizesOut": 0,
                             "price": info.get("lst_price") or 0, "shop": None})
    oh = max(onhand.get(p_, 0), 0); sd = sold.get(p_, {})
    e["onhand"] += oh; e["value"] += oh * (info.get("standard_price") or 0); e["retail"] += oh * (info.get("lst_price") or 0)
    for c in ("web", "ourkids", "other", "web90", "all90", "all180"): e[c] += sd.get(c, 0)
    e["last"] = max(e["last"], sd.get("last", ""))
    if oh > 0 or sd: e["sizes"] += 1; e["sizesOut"] += 1 if oh <= 0 else 0
    if not e["shop"]:
        e["shop"] = SKU.get(str(info.get("barcode") or "").strip()) or SKU.get(str(info.get("default_code") or "").strip())
for e in tm.values():
    if not e["shop"]:
        m = re.search(r"\b(\d{5})\b", e["name"] or "")
        if m: e["shop"] = STYLE.get(m.group(1))

def advise(i):
    """One call per product, with the reason. Thresholds: 14 days = time to remake/reorder; 180 days = half a year of stock."""
    oh, out30, out90, d = i["onhand"], i["web"] + i["ourkids"] + i["other"], i["all90"], i["dor"]
    sea, old = i["season"], (i["season"] not in ("", CUR, NEXT))
    if oh <= 0 and out30 > 0:
        return "reorder", "Sold out and still selling: %d units in 30 days." % out30
    if d is not None and d < 14 and oh > 0:
        return "reorder", "Runs out in about %d days at %.1f units a day." % (d, i["perDay"])
    if oh <= 0:
        return "", ""
    if out90 == 0 and (old or not sea):
        return "liquidate", "No sale in 90 days%s: clear it (sale or outlet) before it ages further." % ((" and it is last season's (" + sea + ")") if old else "")
    if (d is None or d > 180) and i["ourkids"] == 0 and oh >= 20:
        return "send", "%d units here and OurKids took none in 30 days: offer them to the shops." % oh
    if (d is None or d > 180) and sea in (CUR, NEXT, ""):
        return "push", "In season but slow (%s): feature it on the webstore / in ads before the season ends." % ("no sales in 30 days" if d is None else "%d days of stock" % d)
    if d is None or d > 180:
        return "liquidate", "Over half a year of stock at today's pace%s." % ((" and it is last season's (" + sea + ")") if old else "")
    return "keep", "%d days of stock at today's pace." % d

items = []
for t, e in tm.items():
    out = e["web"] + e["ourkids"] + e["other"]
    if e["onhand"] <= 0 and out <= 0: continue
    perday = out / WIN
    i = {"id": e["id"], "name": e["name"], "code": e["code"], "onhand": round(e["onhand"]), "web": round(e["web"]), "ourkids": round(e["ourkids"]),
         "other": round(e["other"]), "web90": round(e["web90"]), "all90": round(e["all90"]), "last": e["last"], "perDay": round(perday, 2),
         "dor": round(e["onhand"] / perday) if perday > 0 else None, "value": round(e["value"]), "retail": round(e["retail"]),
         "sizes": e["sizes"], "sizesOut": e["sizesOut"], "price": round(e["price"]), "season": season_of(e["code"], e["name"]),
         "url": (e["shop"] or {}).get("url", ""), "img": (e["shop"] or {}).get("img", ""), "live": (e["shop"] or {}).get("live")}
    i["act"], i["why"] = advise(i)
    items.append(i)
tot_oh = sum(i["onhand"] for i in items); tot_out = sum(i["web"] + i["ourkids"] + i["other"] for i in items)
web_out = sum(i["web"] for i in items)
dor = {"asof": TODAY.isoformat(), "window": WIN, "season": CUR, "units": tot_oh, "value": sum(i["value"] for i in items), "retail": sum(i["retail"] for i in items),
       "sold": {"web": web_out, "ourkids": sum(i["ourkids"] for i in items), "other": sum(i["other"] for i in items)},
       "dor": round(tot_oh / (tot_out / WIN)) if tot_out else None, "dorWeb": round(tot_oh / (web_out / WIN)) if web_out else None,
       "odoo": URL + "/odoo/action-stock.product_template_action_product/", "items": sorted(items, key=lambda i: -i["value"])}
log("dor: products", len(items), "units", tot_oh, "cost", dor["value"], "DOR", dor["dor"], "web-only", dor["dorWeb"],
    "| matched to webstore", sum(1 for i in items if i["url"]), "| advice", {a: sum(1 for i in items if i["act"] == a) for a in ("reorder", "send", "push", "liquidate", "keep")})

last_sale = max((d for d in DAYS if fin_d.get(d)), default=TODAY.isoformat())
O = {"biz": "playmore", "cur": "EGP", "lastSync": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC",
     "today": TODAY.isoformat(), "aw": [FIN_START, TODAY.isoformat()], "gpBasis": "govt4b", "vat": 0.14,
     "fin": fin, "shop": shop, "ad": ad, "exp": exp, "pos": {}, "src": SRC, "ship2": ship2, "bosta": bosta, "dor": dor,
     "attr": {"order": ["default"], "labels": {"default": "Default 7DC/1DV (LIVE)"}, "meta": {"default": 1.0}, "google": {}, "tt": {}, "metaOff": {"default": 1.0}},
     "notes": {"returns": "Refunds = goods returned to stock (return pickings), priced at the original sale line; Playmore books no credit notes for them.",
               "intercompany": "Sales to OurKids (partner 920) are excluded from every total: they are stock moves between the two companies.",
               "courier": "Playmore's books have no courier account, so delivery cost is missing, not zero.",
               "vat": "Gross profit is Odoo's margin (ex-VAT); the 14% sales tax is the government-charge line."}}
F = {"currency": "EGP", "brand": "Playmore", "branches": [], "company": {}, "meta": {}, "attr": {}, "notes": {}}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("window.O=" + json.dumps(O, separators=(",", ":"), ensure_ascii=False) + ";\nwindow.F=" + json.dumps(F) + ";\n")
log("wrote", OUT, os.path.getsize(OUT), "bytes")
