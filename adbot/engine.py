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
MAX_UP, MAX_UP_BIG = 1.5, 2.0              # one approval: +50%, or +100% when the room is 2x+ (held 96% / 92% in history)
MIN_STEP, MAX_DOWN = 0.10, 0.50
UTIL_MIN = 0.85                           # under this share of budget spent, more budget buys nothing

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


def profit_move(m, typ, k, b):
    """Expected change in E£/day if spend goes from today's to k x today's, on incremental revenue.
    Revenue scales k**b (next-pound curve). Returns online gross / online delivered / in-store GP, spend, net."""
    on, st = m["_v_on"] / 7.0 * INCR[typ], m["_v_st"] / 7.0 * INCR[typ]
    g = k ** b - 1; d = m["spend"] / 7.0 * (k - 1)
    gp_on, gp_st = on * M_ON * g, st * M_ST * g
    return {"rev_online": round(on * g), "rev_instore": round(st * g), "gp_online_gross": round(on * M_ON_GROSS * g),
            "gp_online_delivered": round(gp_on), "gp_instore": round(gp_st), "spend": round(d),
            "net": round(gp_on + gp_st - d), "net_online_only": round(gp_on - d)}


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
        r7 = w7["roas_all7"] or 0; x = r7 / nd if nd else 0
        base = {"level": lvl, "id": oid, "name": name, "campaign": camp, "audience": t, "need7": nd, "x_need": round(x, 2),
                "type_cpa": tcpa.get(t), "last7": clean(w7), "last28": clean(w28), "confidence": conf_of(w7, t)}
        # ---- how big a move: where does the NEXT pound stop paying?
        # next E£1 returns B x the average ROAS (B from real Odoo webstore revenue vs Meta spend,
        # week-to-week, season + stock controlled). Spend that makes the next pound break even:
        #   s*/s_now = (B * ROAS/need) ** (1 / (1 - B))
        def target(bb):
            return (bb * x) ** (1 / (1 - bb)) if x > 0 else 0
        # ponytail: constant-elasticity curve; history only has moves up to ~+100%, so the target is shown capped at 3x
        r_mid, r_safe = min(target(MARG_B), 3.0), min(target(MARG_B_LO), 3.0)
        daily = w7["spend"] / 7.0; bud = budget / 100.0
        util = daily / bud if bud else None
        rule = None
        if x * MARG_B >= 1 and w7["purch7"] >= 5:
            rule = "scale" if w7["purch7"] >= 10 else "scale_low"
        elif x < 1 and tcpa.get(t) and w7["spend"] >= RULES["trim"]["cpa_x"] * tcpa[t]:
            rule = "trim"
        if not rule:
            continue
        if oid in touched or any(s in touched for s in sids):
            skipped.append({"id": oid, "name": name, "rule": rule, "why": "edited in the last %d days" % COOLDOWN_DAYS}); continue
        up = rule != "trim"
        if up:
            step = min(r_mid, MAX_UP_BIG if r_mid >= 2 else MAX_UP) - 1
            step = max(step, MIN_STEP)
        else:
            step = max(min(r_mid, 1 - MIN_STEP), 1 - MAX_DOWN) - 1
        new = int(round(budget * (1 + step) / 100.0) * 100) if budget else 0
        R = RULES["scale" if rule == "scale" else rule]
        head = (("Last 7 days ROAS %.2f (online + in-store, 7-day click) is %.1fx the %.2f it needs; the next pound should return about %.0f%% of that, "
                 "so spend keeps paying up to about E£%s/day%s (at least E£%s/day on the most pessimistic read) from E£%s/day now."
                 % (r7, x, nd, MARG_B * 100, f"{bud * r_mid:,.0f}", "+" if r_mid >= 3 else "", f"{bud * r_safe:,.0f}", f"{bud:,.0f}")) if up else
                ("Last 7 days ROAS %.2f is only %.0f%% of the %.2f it needs on E£%s spend (%.1fx this audience's CPA); "
                 "the next pound breaks even nearer E£%s/day than E£%s/day."
                 % (r7, x * 100, nd, f"{w7['spend']:,}", w7["spend"] / tcpa[t], f"{bud * max(r_mid, 0.1):,.0f}", f"{bud:,.0f}")))
        change = {"op": "budget", "object": oid, "from": budget, "to": new} if budget else None
        note = None
        if util is not None and util < UTIL_MIN:
            change = None
            note = (("It spent only %.0f%% of its E£%s/day budget last week, so a bigger budget would not be spent. "
                     "It is limited by audience size or bid, not money: widen the audience or add creatives instead.") if up else
                    ("It spent only %.0f%% of its E£%s/day budget last week, so a lower budget changes nothing. "
                     "Pause its losing ads (see the pause cards) or the ad set itself.")) % (util * 100, f"{bud:,.0f}")
        k = new / budget if budget and change else 1.0
        if change:
            pm, pw = profit_move(w7, t, k, MARG_B), profit_move(w7, t, k, MARG_B_LO if up else MARG_B_HI)
            head += (" Expected per day: spend %+s, incremental revenue %+s online / %+s in-store, gross profit %+s online "
                     "(%+s after delivery) and %+s in-store, so net profit after ad spend %+s (worst case %+s); online alone %+s."
                     % tuple(f"E£{v:,}" for v in (pm["spend"], pm["rev_online"], pm["rev_instore"], pm["gp_online_gross"],
                                                    pm["gp_online_delivered"], pm["gp_instore"], pm["net"], pw["net"], pm["net_online_only"])))
        budget_taken.add(oid)
        out.append(dict(base, kind="scale" if up else "trim", rule=rule, hit_rate=R["hit"], hit_txt=R["hit_txt"],
                        why=head, note=note, change=change,
                        sizing={"next_pound_share": MARG_B, "target_daily": round(bud * r_mid), "safe_daily": round(bud * r_safe), "capped": r_mid >= 3,
                                "profit_day": pm if change else None, "profit_day_worst": pw if change else None,
                                "budget_used": round(util * 100) if util is not None else None, "step_pct": round(step * 100)}))

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
