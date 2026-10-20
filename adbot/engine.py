"""OurKids ad bot -- daily rules.

Reads a year of Meta ad-week data, fulfillable stock and each live ad's landing page,
and writes the day's recommended actions. Every action carries the numbers behind it
and a confidence label: CONFIDENT when the ad set spent at least 3x its audience type's
CPA in the window, LOW DATA otherwise (still sent, never hidden).

Evidence for each threshold (measured 20 Oct 2026, see the review page):
  * raising spend 100%+ in a week moved CPA +18%, the same as ad sets held flat
  * cuts improved CPA only ~4% and cost about half the reach -> trim, not pause
  * 38% of ads in the bottom half on their first E£1k ended in the top half -> no kill before E£3k
  * +10% fulfillable stock -> CVR +11%, CPA -10%, ROAS +22% (420 days)
"""
import json, os, sys, statistics, datetime as dt, collections
import data as D

M_ON, M_ST = 0.2605, 0.3183               # delivered online margin / in-store margin
# incremental / 7-day-click ROAS by audience type, read from Ads Reporting 19 Sep-15 Oct 2026.
# Re-read monthly; these set how high a 7dc ROAS must be to break even on incremental.
INCR = {"new": 0.70, "retargeting": 0.88, "existing": 0.64}
SCALE_X, SCALE_UP, TRIM_DOWN = 1.3, 0.30, 0.20
KILL_SPEND, DEAD_SPEND, MIN_PUR_SCALE = 3000, 1500, 15

EXISTING_HINT = ("buyer", "purchas", "customer", "existing", "retention", "ru purchase", "all buyers")
RT_SUBTYPES = {"WEBSITE", "ENGAGEMENT", "VIDEO", "IG_BUSINESS", "FB_EVENT", "APP", "OFFLINE_CONVERSION", "BAG_OF_ACCOUNTS"}


def audience_type(adset, aud_by_id, camp_name):
    inc = ((adset.get("targeting") or {}).get("custom_audiences")) or []
    names = " ".join((a.get("name") or aud_by_id.get(a.get("id"), {}).get("name", "")) for a in inc).lower()
    subs = {aud_by_id.get(a.get("id"), {}).get("subtype", "") for a in inc}
    hint = (adset.get("name", "") + " " + camp_name).lower()
    if any(h in names for h in EXISTING_HINT) or "existing" in hint or "retention" in hint:
        return "existing"
    if subs & RT_SUBTYPES or "remarketing" in hint or "retarget" in hint:
        return "retargeting"
    return "new"


def agg(rows):
    s = collections.Counter()
    for r in rows:
        for k in ("sp", "im", "oc", "pu7", "puV", "pv7", "pvV", "op7", "opV", "ov7", "ovV"):
            s[k] += r[k]
    sp = s["sp"]; pur7 = s["pu7"] + s["op7"]
    return {
        "spend": round(sp), "purch7": round(pur7, 1), "purch_online7": round(s["pu7"], 1),
        "roas_online7": round(s["pv7"] / sp, 2) if sp else None,
        "roas_all7": round((s["pv7"] + s["ov7"]) / sp, 2) if sp else None,
        "roas_online_dflt": round((s["pv7"] + s["pvV"]) / sp, 2) if sp else None,
        "roas_all_dflt": round((s["pv7"] + s["pvV"] + s["ov7"] + s["ovV"]) / sp, 2) if sp else None,
        "cpa7": round(sp / pur7) if pur7 else None,
        "cpa_online7": round(sp / s["pu7"]) if s["pu7"] else None,
        "cvr": round(s["pu7"] / s["oc"] * 100, 2) if s["oc"] else None,
        "cpc": round(sp / s["oc"], 2) if s["oc"] else None,
        "aov": round(s["pv7"] / s["pu7"]) if s["pu7"] else None,
        "_v_on": s["pv7"], "_v_st": s["ov7"],
    }


def need7(m, typ):
    """7-day-click ROAS (online + in-store) at which this entity breaks even on incremental."""
    tot = (m["_v_on"] + m["_v_st"]) or 1
    st = m["_v_st"] / tot
    be = 1 / (M_ON * (1 - st) + M_ST * st)
    return round(be / INCR[typ], 2)


def stock_index(products, stock, n=12):
    """Landing-page stock: share of each product's sizes you can ship today, weighted by
    position (1/rank) so the first products count most. Also the first-4 detail."""
    if not products:
        return None
    num = den = 0.0; first = []
    for i, p in enumerate(products[:n]):
        vs = (p.get("variants") or {}).get("nodes") or []
        keys = [(v.get("barcode") or v.get("sku") or "").strip() for v in vs]
        keys = [k for k in keys if k]
        if not keys:
            continue
        share = sum(1 for k in keys if stock.get(k, 0) > 0) / len(keys)
        w = 1.0 / (i + 1); num += w * share; den += w
        if i < 4:
            first.append({"title": p.get("title", "")[:60], "sizes_in_stock": round(share * 100)})
    return {"index": round(num / den * 100) if den else None, "first4": first}


def main(out_path):
    rep = {"generated": dt.datetime.utcnow().isoformat() + "Z", "account": D.ACT}
    rep["token_scopes"] = D.token_scopes()
    rows = D.insights_weekly()
    sets = {a["id"]: a for a in D.adsets()}
    camps = {c["id"]: c for c in D.campaigns()}
    auds = {a["id"]: a for a in D.audiences()}
    ads = D.live_ads()
    call = D.odoo()
    stock = D.fulfillable_stock(call)
    weights = D.online_sales_weights(call)

    raw_path = os.path.join(os.path.dirname(os.path.abspath(out_path)) or ".", "adbot_raw.json")
    json.dump({"rows": rows, "sets": sets, "camps": camps, "auds": auds, "ads": ads,
               "stock_keys": len(stock)}, open(raw_path, "w"), default=str)
    weeks = sorted({r["wk"] for r in rows}); last = weeks[-1]; last4 = set(weeks[-4:])
    rep["weeks"] = [weeks[0], last, len(weeks)]
    by_set = collections.defaultdict(list); by_ad = collections.defaultdict(list)
    for r in rows:
        by_set[r["adset"]].append(r); by_ad[r["ad"]].append(r)

    # ---- audience type per ad set, and each type's own CPA (last 4 weeks, 7dc, all purchases)
    typ = {}
    for sid, s in sets.items():
        typ[sid] = audience_type(s, auds, camps.get(s.get("campaign_id"), {}).get("name", ""))
    for sid, L in by_set.items():
        if sid not in typ:
            typ[sid] = audience_type({"name": L[0]["adset_name"]}, auds, L[0]["camp_name"])
    tcpa = {}
    for t in INCR:
        R = [r for r in rows if r["wk"] in last4 and typ.get(r["adset"]) == t]
        m = agg(R); tcpa[t] = m["cpa7"]
        rep.setdefault("types", {})[t] = {k: v for k, v in m.items() if not k.startswith("_")}
    rep["type_cpa"] = tcpa

    # ---- landing pages and DPA product sets of live ads
    land_cache = {}; dpa_cache = {}; ad_stock = {}
    for a in ads:
        c = a.get("creative") or {}
        ps = c.get("product_set_id")
        if ps:
            if ps not in dpa_cache:
                try:
                    items = D.product_set_items(ps)
                    num = den = 0.0; n_in = 0
                    for it in items:
                        k = (it.get("gtin") or "").strip()
                        q = stock.get(k, 0) if k else 0
                        w = weights.get(k, 0) + 1.0
                        num += w * (q > 0); den += w; n_in += q > 0
                    dpa_cache[ps] = {"kind": "dpa", "items": len(items), "matched": sum(1 for it in items if (it.get("gtin") or "").strip() in stock),
                                     "in_stock_items": n_in, "index": round(num / den * 100) if den else None}
                except Exception as e:
                    dpa_cache[ps] = {"kind": "dpa", "error": str(e)[:120]}
            ad_stock[a["id"]] = dict(dpa_cache[ps], product_set=ps)
            continue
        url = D.creative_link(c)
        if url not in land_cache:
            try:
                kind, prods = D.landing_products(url)
                si = stock_index(prods, stock)
                land_cache[url] = dict(si or {}, kind=kind, url=url)
            except Exception as e:
                land_cache[url] = {"kind": "error", "url": url, "error": str(e)[:120]}
        ad_stock[a["id"]] = land_cache[url]
    rep["landing_pages"] = list(land_cache.values()); rep["dpa_sets"] = dpa_cache

    # ---- actions
    acts = []
    live_set_ids = {a["adset_id"] for a in ads}
    for sid in live_set_ids:
        L = by_set.get(sid, []); s = sets.get(sid, {}); t = typ.get(sid, "new")
        if not L:
            continue
        w7 = agg([r for r in L if r["wk"] == last]); w28 = agg([r for r in L if r["wk"] in last4])
        nd = need7(w28, t); conf = bool(tcpa.get(t)) and w7["spend"] >= 3 * tcpa[t]
        camp = camps.get(s.get("campaign_id"), {})
        budget_obj, budget = (sid, s.get("daily_budget")) if s.get("daily_budget") else (s.get("campaign_id"), camp.get("daily_budget"))
        base = {"level": "adset", "id": sid, "name": s.get("name") or L[0]["adset_name"], "campaign": camp.get("name") or L[0]["camp_name"],
                "audience": t, "confidence": "confident" if conf else "low data", "need7": nd,
                "last7": {k: v for k, v in w7.items() if not k.startswith("_")}, "last28": {k: v for k, v in w28.items() if not k.startswith("_")}}
        r7 = w7["roas_all7"] or 0
        if budget and r7 >= SCALE_X * nd and w7["purch7"] >= MIN_PUR_SCALE:
            new = int(int(budget) * (1 + SCALE_UP))
            acts.append(dict(base, kind="scale", why="7dc ROAS %.2f is %.1fx what it needs (%.2f)" % (r7, r7 / nd, nd),
                             change={"op": "budget", "object": budget_obj, "from": int(budget), "to": new}))
        elif budget and r7 < nd and w7["spend"] >= KILL_SPEND:
            new = int(int(budget) * (1 - TRIM_DOWN))
            acts.append(dict(base, kind="trim", why="7dc ROAS %.2f under the %.2f it needs" % (r7, nd),
                             change={"op": "budget", "object": budget_obj, "from": int(budget), "to": new}))
    for a in ads:
        L = by_ad.get(a["id"], []); t = typ.get(a["adset_id"], "new")
        life = agg(L); w28 = agg([r for r in L if r["wk"] in last4])
        nd = need7(life, t) if life["spend"] else None
        base = {"level": "ad", "id": a["id"], "name": a["name"], "adset": a["adset_id"],
                "campaign": camps.get(a.get("campaign_id"), {}).get("name", ""), "audience": t,
                "need7": nd, "lifetime": {k: v for k, v in life.items() if not k.startswith("_")},
                "last28": {k: v for k, v in w28.items() if not k.startswith("_")}, "stock": ad_stock.get(a["id"])}
        conf = bool(tcpa.get(t)) and life["spend"] >= 3 * tcpa[t]
        base["confidence"] = "confident" if conf else "low data"
        if life["spend"] >= DEAD_SPEND and life["purch7"] == 0:
            acts.append(dict(base, kind="pause", why="E£%s spent, no purchases" % life["spend"], change={"op": "status", "object": a["id"], "to": "PAUSED"}))
        elif nd and life["spend"] >= KILL_SPEND and (life["roas_all7"] or 0) < 0.5 * nd:
            acts.append(dict(base, kind="pause", why="7dc ROAS %.2f, under half of the %.2f it needs" % (life["roas_all7"] or 0, nd),
                             change={"op": "status", "object": a["id"], "to": "PAUSED"}))
        elif nd and w28["spend"] and (w28["roas_all7"] or 0) >= 1.5 * nd and conf:
            acts.append(dict(base, kind="winner", why="last 28 days 7dc ROAS %.2f, %.1fx what it needs" % (w28["roas_all7"], w28["roas_all7"] / nd),
                             change=None))
        st = ad_stock.get(a["id"]) or {}
        if w28["spend"] >= 1000 and st.get("index") is not None and st["index"] < 50:
            acts.append(dict(base, kind="stock", why="%s stock %s%%: shoppers land on sold-out sizes" % ("Catalogue" if st.get("kind") == "dpa" else "Landing page", st["index"]),
                             change={"op": "status", "object": a["id"], "to": "PAUSED"}))
    order = {"pause": 0, "stock": 1, "trim": 2, "scale": 3, "winner": 4}
    acts.sort(key=lambda x: (order[x["kind"]], x["confidence"] != "confident", -(x.get("last7") or x.get("last28") or {}).get("spend", 0)))
    rep["actions"] = acts
    rep["counts"] = collections.Counter(a["kind"] + " · " + a["confidence"] for a in acts)
    json.dump(rep, open(out_path, "w"), default=str, indent=1)
    log_summary(rep)


def log_summary(rep):
    D.log("token scopes:", rep["token_scopes"])
    D.log("weeks", rep["weeks"], "type CPA", rep["type_cpa"])
    D.log("landing pages", len(rep["landing_pages"]), "dpa sets", len(rep["dpa_sets"]))
    D.log("actions", dict(rep["counts"]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "adbot_report.json")
