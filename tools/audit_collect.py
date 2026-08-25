#!/usr/bin/env python3
"""Commercial Audit collector -- writes docs/ourkids/audit.json.

DELIBERATELY STANDALONE. It does not live inside ourkids_live.py and does not touch
data.js, because both of those files are rewritten wholesale by other sessions and any
edit to them gets lost. This owns exactly one output file that nothing else writes.

Produces the per-SKU joins that no daily series can carry:
  * listing  -- Shopify-vs-Odoo barcode join: which in-store SKUs have no live page
  * vendors  -- GMROI, goods-received recency (L7/L30/L90/L365), intake-vs-profit index
  * cats     -- category revenue / gross profit / intake / listing coverage

Run:  python3 tools/audit_collect.py
Env:  ODOO_SERVER ODOO_DB ODOO_LOGIN ODOO_APIKEY SHOPIFY_STORE SHOPIFY_TOKEN
"""
import datetime, json, os, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs", "ourkids")
WINDOWS = (7, 30, 90, 365)
PSEUDO = [23, 24, 88727, 226155]
LOG = []


def log(*a):
    m = "[audit] " + " ".join(str(x) for x in a)
    LOG.append(m)
    print(m, flush=True)


def http_json(url, data=None, headers=None, tries=4, timeout=180):
    body = json.dumps(data).encode() if data is not None else None
    last = None
    for i in range(tries):
        try:
            rq = urllib.request.Request(url, data=body, headers=dict(headers or {}, **{"Content-Type": "application/json"}))
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


# --------------------------------------------------------------------- odoo
_UID = [None]
def _creds():
    return (os.environ["ODOO_SERVER"].rstrip("/"), os.environ["ODOO_DB"],
            os.environ["ODOO_LOGIN"], os.environ["ODOO_APIKEY"])


def _rpc(service, method, args):
    sv = _creds()[0]
    out = http_json(sv + "/jsonrpc", {"jsonrpc": "2.0", "method": "call", "id": 1,
                                      "params": {"service": service, "method": method, "args": args}})
    if "error" in out:
        raise RuntimeError(str(out["error"])[:400])
    return out.get("result")


def oexec(model, method, args, kw=None):
    sv, db, lo, k = _creds()
    if _UID[0] is None:
        _UID[0] = _rpc("common", "authenticate", [db, lo, k, {}])
    return _rpc("object", "execute_kw", [db, _UID[0], k, model, method, args, kw or {}]) or []


def ogroup(model, domain, fields, groupby):
    return oexec(model, "read_group", [domain, fields, groupby], {"lazy": False})


# ------------------------------------------------------------------ shopify
def shopify_bulk(query):
    store = os.environ.get("SHOPIFY_STORE", "").strip()
    tok = os.environ.get("SHOPIFY_TOKEN", "").strip()
    if not store or not tok:
        log("SHOPIFY_STORE/SHOPIFY_TOKEN missing -- listing gap skipped")
        return []
    host = store if "." in store else store + ".myshopify.com"
    ver = os.environ.get("SHOPIFY_API_VERSION", "").strip() or "2025-07"
    url = "https://%s/admin/api/%s/graphql.json" % (host, ver)
    hd = {"X-Shopify-Access-Token": tok}
    mut = ('mutation { bulkOperationRunQuery(query: """%s""") '
           '{ bulkOperation { id status } userErrors { field message } } }' % query)
    d = http_json(url, {"query": mut}, hd)
    errs = (((d or {}).get("data") or {}).get("bulkOperationRunQuery") or {}).get("userErrors") or []
    if errs:
        log("bulk op refused", json.dumps(errs)[:200])
        return []
    for _ in range(72):
        time.sleep(5)
        q = http_json(url, {"query": "{ currentBulkOperation { status url errorCode objectCount } }"}, hd)
        cur = (((q or {}).get("data") or {}).get("currentBulkOperation") or {})
        if cur.get("status") == "COMPLETED":
            if not cur.get("url"):
                log("bulk op completed with no file")
                return []
            raw = urllib.request.urlopen(cur["url"], timeout=300).read().decode("utf-8")
            rows = [json.loads(x) for x in raw.split("\n") if x.strip()]
            log("bulk op returned", len(rows), "rows")
            return rows
        if cur.get("status") in ("FAILED", "CANCELED", "EXPIRED"):
            log("bulk op", cur.get("status"), cur.get("errorCode"))
            return []
    log("bulk op still running after 6 minutes -- giving up this cycle")
    return []


def shopify_live_barcodes():
    """{barcode: is_live}. Bulk JSONL child rows only carry the fields the query asked
       for -- this one does not ask for the variant id -- so never index o['id'] on a
       child. A child is anything carrying __parentId."""
    rows = shopify_bulk("""
    { products { edges { node {
        id status publishedAt
        variants { edges { node { sku barcode } } } } } } }""")
    prods, kids, live = {}, {}, {}
    for o in rows:
        oid = o.get("id") or ""
        if oid.startswith("gid://shopify/Product/"):
            prods[oid] = o
        elif o.get("__parentId"):
            kids.setdefault(o["__parentId"], []).append(o)
    for pid, node in prods.items():
        ok = node.get("status") == "ACTIVE" and bool(node.get("publishedAt"))
        for v in kids.get(pid, []):
            for k in {(v.get("sku") or "").strip(), (v.get("barcode") or "").strip()}:
                if k:
                    live[k] = live.get(k, False) or ok
    log("barcodes mapped", len(live), "live", sum(1 for v in live.values() if v))
    return live


# --------------------------------------------------------------------- pulls
def products():
    F = ["barcode", "default_code", "categ_id", "related_vendor_id",
         "qty_available", "standard_price", "name", "create_date"]
    out, off = {}, 0
    while True:
        r = oexec("product.product", "search_read", [[["active", "=", True]]],
                  {"fields": F, "limit": 2000, "offset": off, "order": "id"})
        if not r:
            break
        for p in r:
            out[p["id"]] = p
        off += len(r)
        if len(r) < 2000:
            break
    log("odoo products", len(out))
    return out


def sales(since):
    off = {r["product_id"][0]: r for r in ogroup(
        "report.pos.order", [["date", ">=", since + " 00:00:00"]],
        ["price_total", "margin", "product_qty"], ["product_id"]) if r.get("product_id")}
    on = {r["product_id"][0]: r for r in ogroup(
        "sale.order.line",
        [["order_id.state", "in", ["sale", "done"]], ["order_id.team_id.name", "=", "Shopify"],
         ["display_type", "=", False], ["product_id", "not in", PSEUDO],
         ["create_date", ">=", since + " 00:00:00"]],
        ["price_subtotal", "margin", "product_uom_qty"], ["product_id"]) if r.get("product_id")}
    return off, on


def receipts(since):
    dom = [["state", "=", "done"], ["date", ">=", since + " 00:00:00"],
           ["location_id.usage", "=", "supplier"]]
    out, off = [], 0
    while True:
        r = oexec("stock.move", "search_read", [dom],
                  {"fields": ["product_id", "product_qty", "price_unit", "date"],
                   "limit": 5000, "offset": off, "order": "id"})
        if not r:
            break
        out += r
        off += len(r)
        if len(r) < 5000:
            break
    log("receipt lines", len(out))
    return out


# ---------------------------------------------------------------------- build
def build(today):
    d365 = (today - datetime.timedelta(days=365)).isoformat()
    live = shopify_live_barcodes()
    P = products()
    recs = receipts(d365)

    vname = lambda p: (lambda v: (v[1] if isinstance(v, list) else str(v)) if v else "(no vendor)")(p.get("related_vendor_id"))
    cname = lambda p: (lambda c: (c[1] if isinstance(c, list) else "?") if c else "?")(p.get("categ_id"))
    base = lambda c: c.replace(" (Consignment)", "")

    inv = {}
    for p in P.values():
        q, c = p.get("qty_available") or 0, p.get("standard_price") or 0
        if q <= 0:
            continue
        a = inv.setdefault(vname(p), {"own": 0.0, "cons": 0.0, "units": 0.0, "skus": 0})
        a["cons" if "Consignment" in cname(p) else "own"] += q * c
        a["units"] += q
        a["skus"] += 1

    vrec, crec, last = {}, {}, {}
    for r in recs:
        if not r.get("product_id"):
            continue
        p = P.get(r["product_id"][0])
        if not p:
            continue
        val = (r.get("product_qty") or 0) * (r.get("price_unit") or 0)
        d = datetime.date.fromisoformat(str(r["date"])[:10])
        age, v, cat = (today - d).days, vname(p), base(cname(p))
        if v not in last or d > last[v]:
            last[v] = d
        for w in WINDOWS:
            if age <= w:
                vrec.setdefault(v, {}).setdefault(str(w), 0.0)
                vrec[v][str(w)] += val
                crec.setdefault(cat, {}).setdefault(str(w), 0.0)
                crec[cat][str(w)] += val

    listing, vsale, csale, vgp = {}, {}, {}, {}
    gaps, off365 = [], {}
    for w in WINDOWS:
        since = (today - datetime.timedelta(days=w)).isoformat()
        off, on = sales(since)
        if w == 365:
            off365 = off
        st = {"LIVE": [0, 0.0, 0.0], "UNPUB": [0, 0.0, 0.0], "ABSENT": [0, 0.0, 0.0]}
        cov = {}
        for pid, r in off.items():
            p = P.get(pid)
            if not p:
                continue
            bc = (p.get("barcode") or p.get("default_code") or "").strip()
            k = "LIVE" if live.get(bc) else ("UNPUB" if bc in live else "ABSENT")
            st[k][0] += 1; st[k][1] += r["price_total"]; st[k][2] += r["margin"]
            cat = base(cname(p))
            c = cov.setdefault(cat, {"rev": 0.0, "gap": 0.0, "skus": 0, "gapSkus": 0, "gapStock": 0.0})
            c["rev"] += r["price_total"]; c["skus"] += 1
            if k != "LIVE":
                c["gap"] += r["price_total"]; c["gapSkus"] += 1
                c["gapStock"] += (p.get("qty_available") or 0) * (p.get("standard_price") or 0)
            v = vsale.setdefault(vname(p), {}).setdefault(str(w), [0.0, 0.0, 0.0])
            v[0] += r["price_total"]; v[1] += r["margin"]
            if k != "LIVE":
                v[2] += r["price_total"]
            cc = csale.setdefault(cat, {}).setdefault(str(w), [0.0, 0.0, 0.0, 0.0])
            cc[0] += r["price_total"]; cc[1] += r["margin"]
        for pid, r in on.items():
            p = P.get(pid)
            if not p:
                continue
            cat = base(cname(p))
            cc = csale.setdefault(cat, {}).setdefault(str(w), [0.0, 0.0, 0.0, 0.0])
            cc[2] += r["price_subtotal"]; cc[3] += r["margin"]
            v = vsale.setdefault(vname(p), {}).setdefault(str(w), [0.0, 0.0, 0.0])
            v[0] += r["price_subtotal"]; v[1] += r["margin"]
        listing[str(w)] = {"state": {k: [v[0], round(v[1]), round(v[2])] for k, v in st.items()},
                           "cat": {k: {kk: round(vv) for kk, vv in v.items()} for k, v in cov.items()}}
        if w == 30:
            for pid, r in sorted(off.items(), key=lambda x: -x[1]["margin"]):
                p = P.get(pid)
                if not p:
                    continue
                bc = (p.get("barcode") or p.get("default_code") or "").strip()
                if live.get(bc):
                    continue
                try:
                    age = (today - datetime.date.fromisoformat(str(p.get("create_date"))[:10])).days
                except Exception:
                    age = 9999
                stock = p.get("qty_available") or 0
                gaps.append({"n": (p.get("name") or "")[:58], "b": bc, "c": cname(p), "v": vname(p),
                             "r": round(r["price_total"]), "g": round(r["margin"]),
                             "q": round(r["product_qty"]), "s": round(stock),
                             "sv": round(stock * (p.get("standard_price") or 0)), "a": age,
                             "st": "UNPUB" if bc in live else "ABSENT"})
    gaps.sort(key=lambda x: -(x["g"] * (1.35 if x["a"] <= 120 else 1.0) * (1 if x["s"] > 0 else .25)))

    vcat, catgp = {}, {}
    for pid, r in off365.items():
        p = P.get(pid)
        if not p:
            continue
        cat, v = base(cname(p)), vname(p)
        vcat.setdefault(v, {}).setdefault(cat, 0.0)
        vcat[v][cat] += r["price_total"]
        catgp[cat] = catgp.get(cat, 0.0) + r["margin"]
        vgp.setdefault(v, {}).setdefault(cat, 0.0)
        vgp[v][cat] += r["margin"]

    vendors = []
    for v, iv in inv.items():
        s = (vsale.get(v) or {}).get("365") or [0, 0, 0]
        rev, gp, own = s[0], s[1], iv["own"]
        cogsMo = (rev - gp) / 12 if rev > gp else 0
        gmroi = round(gp / own, 2) if own > 50000 else None
        cover = round(own / cogsMo, 1) if own > 50000 and cogsMo else None
        gm = round(gp / rev * 100, 1) if rev else 0
        rr, ld = vrec.get(v) or {}, last.get(v)
        mix = vcat.get(v) or {}
        cat = max(mix, key=mix.get) if mix else "?"
        ci = (crec.get(cat) or {}).get("90", 0)
        share = (rr.get("90", 0) / ci) if ci else 0
        gshare = ((vgp.get(v) or {}).get(cat, 0) / catgp[cat]) if catgp.get(cat) else 0
        idx = round(share / gshare, 2) if gshare > 0.001 and share > 0 else None
        model = "Consignment" if own < 50000 and rev > 200000 else "Owned stock"
        if model == "Consignment":
            verdict = "WIDEN — no capital at risk" if gm >= 20 else "RENEGOTIATE MARGIN"
        elif gmroi is None:
            verdict = "TOO SMALL TO RANK"
        elif gmroi < 1.0 or (cover and cover > 6):
            verdict = "STOP BUYING — work the stock down"
        elif gmroi >= 2.5 and cover and cover < 2.5:
            verdict = "BUY MORE — starved"
        elif gm < 20:
            verdict = "FIX MARGIN BEFORE REORDERING"
        elif gmroi < 2.0 and idx and idx > 1.4:
            verdict = "OVER-BOUGHT vs category — pause"
        else:
            verdict = "HOLD"
        vendors.append({"v": v, "cat": cat, "model": model, "verdict": verdict,
                        "own": round(own), "cons": round(iv["cons"]), "units": round(iv["units"]),
                        "skus": iv["skus"], "rev": round(rev), "gp": round(gp), "gm": gm,
                        "gmroi": gmroi, "cover": cover, "idx": idx,
                        "last": ld.isoformat() if ld else None,
                        "days": (today - ld).days if ld else None,
                        "r7": round(rr.get("7", 0)), "r30": round(rr.get("30", 0)),
                        "r90": round(rr.get("90", 0)), "r365": round(rr.get("365", 0)),
                        "gapRev": round(s[2]),
                        "rel": round(max(0, own - cogsMo * 3)) if verdict.startswith(("STOP", "OVER")) else 0,
                        "dep": round(max(0, cogsMo * 2.5 - own)) if verdict.startswith("BUY MORE") else 0})
    vendors.sort(key=lambda x: -x["rev"])
    log("vendors", len(vendors), "gap SKUs", len(gaps))

    return {"pulled": today.isoformat(),
            "generated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "windows": list(WINDOWS), "listing": listing,
            "gaps": gaps[:400], "gapsN": len(gaps), "vendors": vendors[:400],
            "catIntake": {k: {kk: round(vv) for kk, vv in v.items()} for k, v in crec.items()},
            "catSales": {k: {kk: [round(x) for x in vv] for kk, vv in v.items()} for k, v in csale.items()},
            "invTotal": {"own": round(sum(i["own"] for i in inv.values())),
                         "cons": round(sum(i["cons"] for i in inv.values()))}}


def main():
    t0 = time.time()
    today = datetime.date.today()
    out, err = None, ""
    try:
        out = build(today)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
        log("BUILD FAILED", err[:300])
        import traceback
        for ln in traceback.format_exc().strip().split("\n")[-10:]:
            log("  " + ln)
    fp = os.path.join(DOCS, "audit.json")
    if out:
        os.makedirs(DOCS, exist_ok=True)
        open(fp, "w", encoding="utf-8").write(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
        log("WROTE audit.json", os.path.getsize(fp), "bytes in %.0fs" % (time.time() - t0))
    else:
        log("audit.json left untouched -- last good file still served")
    open(os.path.join(DOCS, "audit_status.json"), "w", encoding="utf-8").write(json.dumps(
        {"ok": bool(out), "error": err[:300], "seconds": int(time.time() - t0),
         "finishedUtc": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
         "log": LOG[-80:]}, indent=1))
    sys.exit(0)


if __name__ == "__main__":
    main()
