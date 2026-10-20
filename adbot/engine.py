"""OurKids ad bot -- daily rules (v1, backtested).

Reads a year of Meta ad-week data, fulfillable stock (the five shipping locations +
FULL/Shopify), each live ad's landing page and each DPA product set, and writes the day's
recommended actions. Nothing here changes Meta: the phone app executes an action only
after it is approved.

Every rule below was replayed week by week over 52 weeks of this account
(21 Oct 2025 - 19 Oct 2026) against what happened next; HIT is the share of times it was
right. Two candidate rules failed that test and are deliberately absent:
  * "zero purchases at 3x CPA -> pause": only 44% stayed dead (n=18) -- would kill winners
  * "ROAS fell under 60% of the ad's own best weeks -> pause": 49%, a coin flip (n=305)

Confidence: CONFIDENT needs spend >= 3x the audience type's own CPA (trailing 4 weeks)
AND >= 10 purchases (7-day click). Anything less is still sent, labelled LOW DATA.
"""
import json, os, sys, datetime as dt, collections
import data as D
import expect as X
import math

M_ON, M_ST = 0.2605, 0.3183               # delivered online margin / in-store margin
M_ON_GROSS = 0.352                        # online before courier + PayMob (9.16% of revenue)
# incremental / 7-day-click ROAS by audience type, Ads Reporting 19 Sep-15 Oct 2026.
# Re-read monthly; this is what turns a 7-day-click ROAS into "pays on incremental".
INCR = {"new": 0.70, "retargeting": 0.88, "existing": 0.64}
COOLDOWN_DAYS = 3                         # never re-touch what was edited this recently
# The next E£1 of Meta returns this share of the current ROAS. From real Odoo webstore revenue vs Meta
# spend, week-to-week, season + stock + Google controlled (+10% spend -> +7.0% revenue, 95% CI 4.6-9.4%).
# The within-ad-set curve said 1.0 and is NOT used: Meta spends more in the weeks it finds buyers,
# so spend follows sales there as much as sales follow spend.
MARG_B, MARG_B_LO, MARG_B_HI = 0.70, 0.46, 0.94   # HI only for a trim's worst case (cut loses the most)
# Per audience (mid, pessimistic, optimistic). Meta's own value on 52 weeks of ad-set spend steps grew
# 1.02 per 1.00 for new, 0.81 retargeting, 1.04 existing; the real-revenue 0.70 is scaled by that ratio
# for retargeting (INFERRED: assumes the attribution gap is the same across audiences).
MARG = {"new": (0.70, 0.46, 0.94), "existing": (0.70, 0.46, 0.94), "retargeting": (0.56, 0.37, 0.74)}
# How much to trust last week's ROAS vs the last 4 weeks vs the audience type's 4 weeks, by purchases last week.
# Fitted on 992 ad-set-weeks predicting the next 2 weeks; cut forecast error 35% / 16% / 11% / 7%.
SHRINK = ((5, 0.35, 0.40, 0.25), (10, 0.05, 0.85, 0.10), (20, 0.35, 0.40, 0.25), (50, 0.55, 0.00, 0.45))
UTIL_MIN = 0.85                           # under this share of budget spent, more budget buys nothing
# A budget move is offered only when its chance of making money is at least P_MIN (a +100% step needs 90%).
# GATE "p_pays" counts online + in-store gross profit; "online_p_pays" would count the webstore alone. (Shavi to choose.)
GATE, P_MIN = "p_pays", 0.75

# rule -> (threshold, measured hit rate). Hit rates from adbot/backtest (365 days).
RULES = {
    "scale":      dict(roas_x=1 / 0.70, min_pur=10, hit=0.93, hit_txt="Profitable ad sets pushed +30-60% stayed profitable 96% of the time, +100%+ 92% (n=28, 26)"),
    "scale_low":  dict(roas_x=1 / 0.70, min_pur=5,  hit=0.85, hit_txt="With only 5-9 purchases: 85% held profitable (n=135)"),
    "trim":       dict(roas_x=1.0, cpa_x=3, hit=0.77, hit_txt="Ad sets under break-even after 3x CPA stayed under it 77% of the time over the next 2 weeks (n=703)"),
    "pause":      dict(roas_x=0.6, cpa_x=3, hit=0.79, hit_txt="79% kept losing for 4 more weeks (n=131)"),
    "winner":     dict(roas_x=2.0, cpa_x=10, hit=0.78, hit_txt="78% stayed at 1.2x+ break-even for 4 more weeks (n=303)"),
}
STOCK_LOW = 50                            # landing-page stock index (%) that triggers a stock alert
DPA_LOW = 70                              # catalogue set: Meta shows Shopify-wide stock, not what the five can ship
DEAD_HIT = (0.44, "Zero-purchase ads at 3x CPA stayed dead only 44% of the time (n=18) -- a weak signal")

EXISTING_HINT = ("buyer", "purchas", "customer", "existing", "retention", "ru purchase", "all buyers")
RT_SUBTYPES = {"WEBSITE", "ENGAGEMENT", "VIDEO", "IG_BUSINESS", "FB_EVENT", "APP", "OFFLINE_CONVERSION", "BAG_OF_ACCOUNTS"}


def audience_type(adset, aud_by_id, camp_name):
    """new / retargeting / existing. A lookalike is NEW people even when its seed is buyers."""
    inc = ((adset.get("targeting") or {}).get("custom_audiences")) or []
    hint = (adset.get("name", "") + " " + camp_name).lower()
    real = []
    for a in inc:
        meta = aud_by_id.get(a.get("id"), {})
        sub = meta.get("subtype", "")
        nm = (a.get("name") or meta.get("name") or "").lower()
        if sub == "LOOKALIKE" or "lla" in nm.split() or "lookalike" in nm or nm.startswith("lla"):
            continue
        real.append((sub, nm))
    if any(any(h in nm for h in EXISTING_HINT) for sub, nm in real) or "existing" in hint or "retention" in hint:
        return "existing"
    if any(sub in RT_SUBTYPES for sub, nm in real) or "remarketing" in hint or "retarget" in hint:
        return "retargeting"
    return "new"


def agg(rows):
    s = collections.Counter()
    for r in rows:
        for k in ("sp", "im", "oc", "rch", "pu7", "puV", "pv7", "pvV", "op7", "opV", "ov7", "ovV"):
            s[k] += r.get(k, 0)
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
        "instore_share": round(s["ov7"] / (s["pv7"] + s["ov7"]) * 100) if (s["pv7"] + s["ov7"]) else None,
        "_v_on": s["pv7"], "_v_st": s["ov7"],
    }


def need7(m, typ):
    """7-day-click ROAS (online + in-store) at which this breaks even on incremental,
    given its own online/in-store mix."""
    tot = (m["_v_on"] + m["_v_st"]) or 1
    st = m["_v_st"] / tot
    return round(1 / (M_ON * (1 - st) + M_ST * st) / INCR[typ], 2)


def clean(m):
    return {k: v for k, v in m.items() if not k.startswith("_")}


def stock_index(products, stock, n=12):
    """Landing-page stock: share of each product's sizes you can ship today, weighted 1/rank
    so the first products count most. Returns index, first-4 detail and the barcodes."""
    if not products:
        return None
    num = den = 0.0; first = []; keys_all = []
    for i, p in enumerate(products[:n]):
        vs = (p.get("variants") or {}).get("nodes") or []
        keys = [k for k in ((v.get("barcode") or v.get("sku") or "").strip() for v in vs) if k]
        if not keys:
            continue
        keys_all.append([i, keys])
        share = sum(1 for k in keys if stock.get(k, 0) > 0) / len(keys)
        w = 1.0 / (i + 1); num += w * share; den += w
        if i < 4:
            first.append({"title": (p.get("title") or "")[:60], "sizes_in_stock": round(share * 100)})
    return {"index": round(num / den * 100) if den else None, "first4": first, "_keys": keys_all}


def dpa_index(items, varmap, stock, weights):
    """Catalogue stock: share of the product set you can ship today, weighted by 180 days of
    online sales (+1 so unsold items still count). Coverage says how many items matched."""
    num = den = 0.0; matched = inn = 0
    for it in items:
        k = (it.get("gtin") or "").strip() or varmap.get(D.retailer_variant(it.get("retailer_id")), "")
        if not k:
            continue
        matched += 1
        q = stock.get(k, 0)
        w = weights.get(k, 0) + 1.0
        num += w * (q > 0); den += w; inn += q > 0
    return {"kind": "dpa", "items": len(items), "matched": matched, "in_stock_items": inn,
            "coverage": round(matched / len(items) * 100) if items else 0,
            "index": round(num / den * 100) if den else None,
            "index_unweighted": round(inn / matched * 100) if matched else None}


def recently_touched(acts, days=COOLDOWN_DAYS):
    cut = dt.datetime.utcnow() - dt.timedelta(days=days)
    out = {}
    for a in acts:
        try:
            t = dt.datetime.strptime(a["event_time"][:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            continue
        if t >= cut and a.get("event_type") in ("update_ad_set_budget", "update_campaign_budget", "update_ad_set_run_status",
                                                 "update_ad_run_status", "update_ad_set_target_spec", "update_ad_set_bid_strategy",
                                                 "update_ad_set_bidding", "update_campaign_run_status", "create_ad_set", "create_ad"):
            oid = str(a.get("object_id"))
            out[oid] = max(out.get(oid, t), t)
    return out


def build(rep):
    rep["token_scopes"] = D.token_scopes()
    rows = D.insights_weekly()
    sets = {a["id"]: a for a in D.adsets()}
    camps = {c["id"]: c for c in D.campaigns()}
    auds = {a["id"]: a for a in D.audiences()}
    ads = D.live_ads()
    if os.environ.get("ADBOT_RAW"):           # manual runs only: a year of ad-weeks for the driver analysis
        json.dump({"rows": rows, "sets": sets, "camps": camps, "auds": auds, "ads": ads}, open(os.environ["ADBOT_RAW"], "w"), default=str)
    acts = D.activities(days=7)
    touched = recently_touched(acts)
    call = D.odoo()
    stock = D.fulfillable_stock(call)
    weights = D.online_sales_weights(call, days=180)

    weeks = sorted({r["wk"] for r in rows}); last = weeks[-1]; last4 = set(weeks[-4:])
    rep["weeks"] = [weeks[0], last, len(weeks)]
    by_set = collections.defaultdict(list); by_ad = collections.defaultdict(list); by_camp = collections.defaultdict(list)
    for r in rows:
        by_set[r["adset"]].append(r); by_ad[r["ad"]].append(r); by_camp[r["camp"]].append(r)

    typ = {sid: audience_type(s, auds, camps.get(s.get("campaign_id"), {}).get("name", "")) for sid, s in sets.items()}
    for sid, L in by_set.items():
        typ.setdefault(sid, audience_type({"name": L[0]["adset_name"]}, auds, L[0]["camp_name"]))
    tcpa = {}; rep["types"] = {}
    for t in INCR:
        m = agg([r for r in rows if r["wk"] in last4 and typ.get(r["adset"]) == t])
        tcpa[t] = m["cpa7"]; rep["types"][t] = clean(m)
    rep["type_cpa"] = tcpa

    # ---- stock: landing pages and DPA product sets of live ads
    varmap = {}
    if any((a.get("creative") or {}).get("product_set_id") for a in ads):
        try:
            varmap = D.variant_barcodes()
        except Exception as e:
            D.log("variant map failed:", str(e)[:120])
    land, dpa, ad_stock = {}, {}, {}
    for a in ads:
        c = a.get("creative") or {}
        ps = c.get("product_set_id")
        if ps:
            if ps not in dpa:
                try:
                    dpa[ps] = dpa_index(D.product_set_items(ps, cap=20000), varmap, stock, weights)
                except Exception as e:
                    dpa[ps] = {"kind": "dpa", "error": str(e)[:120]}
            ad_stock[a["id"]] = dict(dpa[ps], product_set=ps)
            continue
        url = D.creative_link(c)
        if url not in land:
            try:
                kind, prods = D.landing_products(url)
                land[url] = dict(stock_index(prods, stock) or {}, kind=kind, url=url)
            except Exception as e:
                land[url] = {"kind": "error", "url": url, "error": str(e)[:120]}
        ad_stock[a["id"]] = {k: v for k, v in land[url].items() if not k.startswith("_")}
    rep["landing_pages"] = [{k: v for k, v in l.items() if k != "_keys"} for l in land.values()]
    rep["landing_keys"] = {l["url"]: l.get("_keys") for l in land.values() if l.get("_keys")}
    rep["dpa_sets"] = dpa
    rep["ad_stock"] = {aid: {k: v for k, v in st.items() if not k.startswith("_")} for aid, st in ad_stock.items()}

    out = []; skipped = []; budget_taken = set()

    def conf_of(m, t):
        tc = tcpa.get(t)
        return "confident" if (tc and m["spend"] >= 3 * tc and m["purch7"] >= 10) else "low data"

    accm = agg([r for r in rows if r["wk"] in last4]); accw = agg([r for r in rows if r["wk"] == last])
    ACC = {"on": (accm["_v_on"] / accm["spend"]) if accm["spend"] else 0, "st": (accm["_v_st"] / accm["spend"]) if accm["spend"] else 0,
           "spend_day": accw["spend"] / 7.0 or X.CAL["spend_ref"]}
    G = D.google_daily(); WARN = None
    if G:
        recent = [(d, g) for d, g in zip(G["days"], G["g"]) if d >= (dt.date.today() - dt.timedelta(days=8)).isoformat() and d < dt.date.today().isoformat()]
        dark = [d for d, g in recent if not g]
        if G.get("google_stale"):
            WARN = ("Google spend is unknown: the dashboard's Google feed is failing, so this run cannot tell whether Google is live. "
                    "If Google is dark, Meta's own ROAS reads about 63% high (real revenue did not clearly move on dark days).")
        elif dark:
            WARN = ("Google Ads spent nothing on %d of the last %d days (since %s). On Google-dark days Meta's own ROAS read 63%% higher "
                    "while real revenue did not clearly move, so this week's Meta numbers are inflated: check Google billing first."
                    % (len(dark), len(recent), dark[0]))
    SEASON = X.season_index(G.get("shop", {}), G.get("pos", {}), dt.date.today()) if G else (1.0, 1.0)
    MISS = D.missing_sizes(call, stock); MF = X.miss_factor(MISS["share"])
    SEASON = (round(SEASON[0] * MF, 2), SEASON[1])          # online return also scaled by missing sizes today
    rep["account"] = {"season_index": {"online": SEASON[0], "instore": SEASON[1]}, "missing_sizes": MISS, "missing_factor": MF, "roas_online7_4w": round(ACC["on"], 2), "roas_instore7_4w": round(ACC["st"], 2), "spend_day": round(ACC["spend_day"]),
                      "google_warning": WARN, "gate": GATE, "p_min": P_MIN}
    # ---- budget decisions on whoever owns the budget: ad set (ABO) or campaign (CBO)
    live_sets = {a["adset_id"] for a in ads}
    owners = {}
    for sid in live_sets:
        s = sets.get(sid, {})
        if s.get("daily_budget"):
            owners[("adset", sid)] = [sid]
        elif s.get("campaign_id") and camps.get(s["campaign_id"], {}).get("daily_budget"):
            owners.setdefault(("campaign", s["campaign_id"]), []).append(sid)
    for (lvl, oid), sids in owners.items():
        L = [r for sid in sids for r in by_set.get(sid, [])]
        if not L:
            continue
        ts = collections.Counter(typ.get(sid, "new") for sid in sids); t = ts.most_common(1)[0][0]
        w7 = agg([r for r in L if r["wk"] == last]); w28 = agg([r for r in L if r["wk"] in last4])
        if not w7["spend"]:
            continue
        nd = need7(w28 if w28["spend"] else w7, t)
        budget = int((sets[oid] if lvl == "adset" else camps[oid]).get("daily_budget") or 0)
        name = (sets.get(oid, {}).get("name") if lvl == "adset" else camps.get(oid, {}).get("name")) or L[0]["adset_name"]
        camp = camps.get(sets.get(oid, {}).get("campaign_id") if lvl == "adset" else oid, {}).get("name") or L[0]["camp_name"]
        raw7 = w7["roas_all7"] or 0
        wl, w4, wt = next(((a, b, c) for lo, a, b, c in reversed(SHRINK) if w7["purch7"] >= lo), (0.0, 0.6, 0.4))
        r7 = round(wl * raw7 + w4 * (w28["roas_all7"] or raw7) + wt * (rep["types"][t].get("roas_all7") or raw7), 2)
        x = r7 / nd if nd else 0
        MB, MB_LO, MB_HI = MARG[t]
        base = {"level": lvl, "id": oid, "name": name, "campaign": camp, "audience": t, "need7": nd, "x_need": round(x, 2),
                "type_cpa": tcpa.get(t), "last7": clean(w7), "last28": clean(w28), "confidence": conf_of(w7, t)}
        daily = w7["spend"] / 7.0; bud = budget / 100.0
        util = daily / bud if bud else None
        if not bud or w7["purch7"] < 3:
            continue
        mix = w28 if (w28["_v_on"] + w28["_v_st"]) else w7
        st_share = mix["_v_st"] / ((mix["_v_on"] + mix["_v_st"]) or 1)
        r_on, r_st = r7 * (1 - st_share), r7 * st_share
        sim = lambda k: X.simulate(daily, k, r_on, r_st, ACC["on"], ACC["st"], ACC["spend_day"], w7["purch7"],
                                   MB, (MB_HI - MB_LO) / 3.92, M_ON, M_ST, SEASON)
        ups = [sim(k) for k in (1.2, 1.5, 2.0)]; downs = [sim(k) for k in (0.8, 0.65, 0.5)]
        ok_up = [r for r in ups if r[GATE] >= P_MIN and (r["k"] < 2 or r[GATE] >= 0.9)]
        ok_dn = [r for r in downs if r[GATE] >= P_MIN]
        pick = max(ok_up, key=lambda r: r["net_p50"]) if ok_up else (max(ok_dn, key=lambda r: r["net_p50"]) if ok_dn else None)
        if not pick:
            continue
        up = pick["k"] > 1; rule = "scale" if up else "trim"
        if oid in touched or any(s in touched for s in sids):
            skipped.append({"id": oid, "name": name, "rule": rule, "why": "edited in the last %d days" % COOLDOWN_DAYS}); continue
        new = int(round(budget * pick["k"] / 100.0) * 100)
        bias = X.miss(w7["purch7"])[0]
        e = lambda v: ("+" if v >= 0 else "-") + "E£" + f"{abs(v):,}"
        head = ("%s budget E£%s -> E£%s/day (%+d%%). Expected net profit %s/day after the extra ad spend "
                "(80%% range %s to %s). Chance it makes money: %d%% (%s). Online alone: %s/day, chance %d%%; the shops add %s gross profit/day. "
                "Forecast ROAS %.2f (last week %.2f, 4 weeks %.2f, %s audiences %.2f, less the %d%% that past forecasts ran high), "
                "%d%% of it in-store. Season and sizes: online return at %d%% of normal (%d%% of in-demand sizes can't ship today vs 17%% typical), shops at %d%%."
                % ("Raise" if up else "Cut", f"{bud:,.0f}", f"{new / 100:,.0f}", round((pick["k"] - 1) * 100), e(pick["net_p50"]),
                   e(pick["net_p10"]), e(pick["net_p90"]), round(pick["p_pays"] * 100), X.label(pick["p_pays"]),
                   e(pick["online_p50"]), round(pick["online_p_pays"] * 100), e(pick["gp_instore"]),
                   r7, raw7, w28["roas_all7"] or 0, t, rep["types"][t].get("roas_all7") or 0, round((1 - math.exp(bias)) * 100), round(st_share * 100),
                   round(SEASON[0] * 100), round(MISS["share"] * 100), round(SEASON[1] * 100)))
        change = {"op": "budget", "object": oid, "from": budget, "to": new}
        notes = []
        if util is not None and util < UTIL_MIN:
            change = None
            notes.append(("It spent only %.0f%% of its E£%s/day budget last week, so a bigger budget would not be spent: "
                          "widen the audience or add creatives." if up else
                          "It spent only %.0f%% of its E£%s/day budget last week, so a lower budget changes nothing: pause its losing ads or the ad set.")
                         % (util * 100, f"{bud:,.0f}"))
        if WARN:
            notes.append(WARN)
        budget_taken.add(oid)
        out.append(dict(base, kind=rule, rule=rule, hit_rate=pick["p_pays"], hit_txt=X.CALIB_TXT,
                        why=head, note=" ".join(notes) or None, change=change,
                        sizing={"roas_expected": r7, "roas_last_week": raw7, "instore_share": round(st_share, 2), "budget_used": round(util * 100) if util is not None else None,
                                "step_pct": round((pick["k"] - 1) * 100), "pick": pick, "options": ups + downs}))

    # ---- all budget cards approved together: the account-level curve, not the sum of single cards
    moves = [a for a in out if a.get("change") and a["change"].get("op") == "budget"]
    if moves:
        s0 = ACC["spend_day"]
        ds = sum((a["change"]["to"] - a["change"]["from"]) / 100.0 * min(((a.get("sizing") or {}).get("budget_used") or 100) / 100.0, 1.0) for a in moves)
        pk = X.package(s0, s0 + ds, SEASON)
        rep["account"]["package"] = {"cards": len(moves), "spend_from": round(s0), "spend_to": round(s0 + ds), "net_day": pk,
                                     "sum_of_cards": sum(a["sizing"]["pick"]["net_p50"] for a in moves),
                                     "highest_week_on_record": 68541}
        D.log("package", rep["account"]["package"])

    # ---- ad decisions
    pause_by_set = collections.defaultdict(list); live_by_set = collections.defaultdict(list)
    for a in ads:
        live_by_set[a["adset_id"]].append(a["id"])
    for a in ads:
        L = by_ad.get(a["id"], []); t = typ.get(a["adset_id"], "new"); tc = tcpa.get(t)
        life = agg(L); w28 = agg([r for r in L if r["wk"] in last4])
        if not life["spend"] or not tc:
            continue
        nd = need7(life, t)
        st = ad_stock.get(a["id"]) or {}
        base = {"level": "ad", "id": a["id"], "name": a["name"], "adset": a["adset_id"],
                "adset_name": sets.get(a["adset_id"], {}).get("name", ""),
                "campaign": camps.get(a.get("campaign_id"), {}).get("name", ""), "audience": t, "need7": nd,
                "type_cpa": tc, "lifetime": clean(life), "last28": clean(w28), "stock": st}
        xl = (life["roas_all7"] or 0) / nd
        if life["spend"] >= RULES["pause"]["cpa_x"] * tc and xl < RULES["pause"]["roas_x"]:
            if a["id"] in touched:
                skipped.append({"id": a["id"], "name": a["name"], "rule": "pause", "why": "edited in the last %d days" % COOLDOWN_DAYS}); continue
            zero = life["purch7"] == 0
            act = dict(base, kind="pause", rule="pause", hit_rate=DEAD_HIT[0] if zero else RULES["pause"]["hit"],
                       hit_txt=DEAD_HIT[1] if zero else RULES["pause"]["hit_txt"], x_need=round(xl, 2),
                       confidence="low data" if zero else conf_of(life, t),
                       why="Lifetime ROAS %.2f is %.0f%% of the %.2f it needs, after E£%s (%.1fx this audience's CPA)."
                           % (life["roas_all7"] or 0, xl * 100, nd, f"{life['spend']:,}", life["spend"] / tc),
                       change={"op": "status", "object": a["id"], "to": "PAUSED"})
            out.append(act); pause_by_set[a["adset_id"]].append(act); continue
        x28 = (w28["roas_all7"] or 0) / nd if w28["spend"] else 0
        if w28["spend"] >= RULES["winner"]["cpa_x"] * tc and x28 >= RULES["winner"]["roas_x"]:
            s_ = sets.get(a["adset_id"], {})
            own = ("adset", a["adset_id"], s_.get("daily_budget")) if s_.get("daily_budget") else \
                  ("campaign", s_.get("campaign_id"), camps.get(s_.get("campaign_id"), {}).get("daily_budget"))
            ch = None; note = None
            if own[2] and own[1] not in budget_taken and own[1] not in touched and a["adset_id"] not in touched:
                b_ = int(own[2]); ch = {"op": "budget", "object": own[1], "from": b_, "to": int(round(b_ * 1.2 / 100.0) * 100)}
                budget_taken.add(own[1]); note = "Approving raises the %s budget that feeds this ad by 20%%." % ("ad set" if own[0] == "adset" else "campaign (CBO)")
            elif own[1] in budget_taken:
                note = "Its budget already has a push or trim proposed above."
            elif own[1] in touched or a["adset_id"] in touched:
                note = "Its budget or ad set was edited in the last %d days; leaving it to settle before another change." % COOLDOWN_DAYS
            elif not own[2]:
                note = "No daily budget to raise (lifetime budget or set elsewhere) -- raise it by hand or move the ad into a scaling ad set."
            out.append(dict(base, kind="winner", rule="winner", hit_rate=RULES["winner"]["hit"], hit_txt=RULES["winner"]["hit_txt"],
                            x_need=round(x28, 2), confidence=conf_of(w28, t), note=note,
                            why="Last 28 days ROAS %.2f is %.1fx the %.2f it needs, on E£%s and %d purchases."
                                % (w28["roas_all7"], x28, nd, f"{w28['spend']:,}", w28["purch7"]), change=ch))
        is_dpa = st.get("kind") == "dpa"
        low = st.get("index") is not None and st["index"] < (DPA_LOW if is_dpa else STOCK_LOW)
        if w28["spend"] >= tc and low and a["id"] not in touched:
            first = "; ".join("%s %d%%" % (p["title"][:28], p["sizes_in_stock"]) for p in (st.get("first4") or []))
            losing = x28 < 1
            if is_dpa:
                what = ("Catalogue set: %d%% shippable by sales, only %d%% of its %s items can ship from the five locations. "
                        "Meta reads Shopify-wide stock, so this ad can show products you cannot send."
                        % (st["index"], st.get("index_unweighted") or 0, f"{st.get('items', 0):,}"))
            else:
                what = "Landing page stock %d%% (sizes you can ship today, first products weighted most). %s" % (
                    st["index"], ("First products: " + first) if first else "")
            fix = (" It is also under break-even (%.0f%% of need), so pause it." % (x28 * 100)) if losing else \
                  (" Still profitable at %.1fx need: fix the page (move in-stock products to the top or restock), do not pause." % x28)
            out.append(dict(base, kind="stock", rule="stock", hit_rate=None,
                            hit_txt="Account-wide: +10% fulfillable stock = CPA 10% lower, CVR 11% higher (420 days)",
                            x_need=round(x28, 2), confidence=conf_of(w28, t), why=what + fix,
                            change={"op": "status", "object": a["id"], "to": "PAUSED"} if losing else None))
    # pausing every live ad in an ad set = pause the ad set instead
    for sid, acts_ in pause_by_set.items():
        if len(acts_) == len(live_by_set[sid]) and len(acts_) > 1:
            for x in acts_:
                x["note"] = "Every live ad in this ad set is flagged; pausing them all stops the ad set."

    order = {"pause": 0, "stock": 1, "trim": 2, "scale": 3, "winner": 4}
    out.sort(key=lambda x: (order[x["kind"]], x["confidence"] != "confident",
                            -((x.get("last7") or x.get("last28") or x.get("lifetime") or {}).get("spend") or 0)))
    rep["actions"] = out; rep["skipped"] = skipped
    rep["counts"] = dict(collections.Counter(a["kind"] + " · " + a["confidence"] for a in out))
    return rep


def main(out_path):
    rep = build({"generated": dt.datetime.utcnow().isoformat() + "Z", "account": D.ACT})
    json.dump(rep, open(out_path, "w"), default=str, indent=1)
    D.log("token scopes:", rep["token_scopes"])
    D.log("weeks", rep["weeks"], "type CPA", rep["type_cpa"])
    D.log("landing pages", len(rep["landing_pages"]), "dpa sets", {k: (v.get("coverage"), v.get("index")) for k, v in rep["dpa_sets"].items()})
    D.log("actions", rep["counts"], "skipped (cooldown)", len(rep["skipped"]))
    if os.environ.get("SUPABASE_SERVICE_KEY"):
        import notify
        notify.publish(rep)
    else:
        D.log("SUPABASE_SERVICE_KEY not set: report only, nothing sent")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "adbot_report.json")
