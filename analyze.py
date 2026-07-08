#!/usr/bin/env python3
"""
AI MEDIA BUYER v4  |  Senior Meta Ads Growth Operator.

Runs on GitHub Actions (free cron) with ONLY a Meta token + Slack bot token.
Each account posts to its OWN channels:
  #meta-ourkids / #meta-playmore     compact daily action digest + 3-day pulse
  #ourkids-launches / #playmore-launches   new-launch performance by adset + best ad

Design: short, scannable, action-first. Ad names are hyperlinks straight to the
ad in Ads Manager. Every message states its exact time window. A winner must beat
the account cold ROAS, not a weak floor. Never a lone metric.

House style: line break after every period, no em dashes, CTR = Outbound CTR.
"""
import os, sys, json, time, argparse, datetime, statistics as st
import urllib.request, urllib.parse, urllib.error

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
DATA_PATH = os.path.join(DOCS, "data.json")
TOKEN = os.environ.get("META_ACCESS_TOKEN", "").strip()
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "").strip()

def load_json(p, d):
    try:
        with open(p, encoding="utf-8") as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return d
def save_json(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f: json.dump(o, f, ensure_ascii=False, indent=2)

CONFIG = load_json(os.path.join(ROOT, "config.json"), {})
TH = CONFIG.get("thresholds", {})
SL = CONFIG.get("slack", {})
CH = SL.get("channels", {})
MENTION = "<@%s>" % SL.get("mention_user_id", "") if SL.get("mention_user_id") else "@Ahmed"
TZ = CONFIG.get("timezone", "Africa/Cairo")

MIN_SPEND = TH.get("winner_min_spend", 1500)
MIN_PUR = TH.get("winner_min_purchases", 5)
ROAS_T = TH.get("roas_target", 2.0)
FAT_FREQ = TH.get("fatigue_freq_increase", 30)
FAT_CTR = TH.get("fatigue_ctr_drop", 20)
AN_ROAS = TH.get("anomaly_roas_drop", 25)
AN_CPA = TH.get("anomaly_cpa_spike", 30)

WARM_KW = ["retarget", " rt ", "rt_", "catalog", "dpa", "promocode", "promo code",
           "didn't purchase", "didnt purchase", "back2cart", "atc ", "atc_", "zombie",
           "existing", "evergreen", "ever green", "abandon", "viewed", "add to cart", "savewith"]
CAT_KW = ["catalog", "dpa"]

def _match(name, keys):
    n = (name or "").lower()
    return [(k, v) for k, v in CH.items() if k in keys and v]
def channel_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore") or CH.get("default")
    if "kids" in n: return CH.get("ourkids") or CH.get("default")
    return CH.get("default")
def channel_launch_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore_launch")
    if "kids" in n: return CH.get("ourkids_launch")
    return None
def channel_advisor_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore_advisor")
    if "kids" in n: return CH.get("ourkids_advisor")
    return None

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def money(v): return "{:,.0f}".format(v) if v is not None else "n/a"
def pct(new, old):
    if not old: return None
    return (new - old) / old * 100.0
def sp(x): return "n/a" if x is None else ("+" if x >= 0 else "") + "{:.0f}%".format(x)
def bw(gap):
    if gap is None: return "n/a"
    return "%d%% under group" % abs(gap) if gap <= 0 else "+%d%% over group" % gap
def fmt_day(d): return d.strftime("%b ") + str(d.day)
def vs_avg(ref, pct=False, is_money=False):
    """Raw side-by-side vs the account average. House rule: never percentage points, show both numbers."""
    if ref is None: return ""
    v = money(ref) if is_money else ("%s%%" % ref if pct else str(ref))
    return " vs %s avg" % v

DATES = {}
NUMID = ""  # numeric ad-account id for building Ads Manager links, set per account
STATUS = {}  # {ad_id: effective_status} for the account being rendered
STAT = {"ACTIVE": "ACTIVE", "PAUSED": "PAUSED", "ADSET_PAUSED": "ADSET PAUSED",
        "CAMPAIGN_PAUSED": "CAMPAIGN PAUSED", "IN_PROCESS": "IN REVIEW", "PENDING_REVIEW": "IN REVIEW",
        "PENDING_BILLING_INFO": "BILLING ISSUE", "DISAPPROVED": "DISAPPROVED", "WITH_ISSUES": "WITH ISSUES",
        "ARCHIVED": "ARCHIVED", "DELETED": "DELETED", "PREAPPROVED": "APPROVED"}
def statuslabel(c):
    s = c.get("status") or STATUS.get(c.get("ad_id"))
    return STAT.get(s, s or "UNKNOWN")
def safe(s): return (s or "").replace("|", " ").replace("<", " ").replace(">", " ").strip()[:70]
def nm(c):
    name = safe(c.get("ad_name"))
    if NUMID and c.get("ad_id"):
        u = "https://business.facebook.com/adsmanager/manage/ads?act=%s&selected_ad_ids=%s" % (NUMID, c["ad_id"])
        return "<%s|%s>" % (u, name or "(unnamed)")
    return "*%s*" % (name or "(unnamed)")
def aset_link(name, sid):
    name = safe(name)
    if NUMID and sid:
        u = "https://business.facebook.com/adsmanager/manage/adsets?act=%s&selected_adset_ids=%s" % (NUMID, sid)
        return "<%s|%s>" % (u, name or "(adset)")
    return "*%s*" % (name or "(adset)")


# ----------------------- Graph API -----------------------
def api_get(path, params):
    params = dict(params); params["access_token"] = TOKEN
    url = "%s/%s?%s" % (GRAPH, path, urllib.parse.urlencode(params))
    last = ""
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e.read().decode("utf-8", "ignore"); low = last.lower()
            if (e.code in (429, 500, 502, 503) or "throttl" in low or "reduce the amount" in low) and i < 4:
                time.sleep(min(90, 2 ** i * 6)); continue
            sys.stderr.write("[api] %s %s: %s\n" % (e.code, path, last[:200])); return {"error": last}
        except Exception as e:
            last = str(e); time.sleep(2 ** i)
    return {"error": last}

def get_accounts():
    cfg = CONFIG.get("accounts", {})
    if cfg.get("mode") == "manual":
        return [{"id": a["id"] if str(a["id"]).startswith("act_") else "act_" + str(a["id"]),
                 "name": a.get("name", ""), "currency": a.get("currency", "")} for a in cfg.get("include", [])]
    out, after = [], None
    while True:
        p = {"fields": "name,account_status,currency", "limit": 200}
        if after: p["after"] = after
        d = api_get("me/adaccounts", p)
        if "error" in d: break
        for a in d.get("data", []):
            if a.get("account_status") == 1:
                out.append({"id": a["id"], "name": a.get("name", a["id"]), "currency": a.get("currency", "")})
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after or not d.get("data"): break
    return out

AD_FIELDS = ",".join(["ad_id", "ad_name", "campaign_name", "adset_name", "adset_id", "objective",
    "spend", "impressions", "reach", "frequency", "cpm", "ctr", "inline_link_click_ctr",
    "outbound_clicks_ctr", "actions", "action_values", "video_play_actions",
    "video_p25_watched_actions", "video_p100_watched_actions", "video_thruplay_watched_actions"])
LITE_FIELDS = "spend,reach,purchase_roas,actions,action_values"

def get_insights(acct, tr, level="ad", fields=AD_FIELDS, extra=""):
    out, after = [], None
    ff = fields + ("," + extra if extra else "")
    while True:
        p = {"level": level, "fields": ff, "time_range": json.dumps(tr), "limit": 400}
        if after: p["after"] = after
        d = api_get("%s/insights" % acct, p)
        if "error" in d: break
        out += d.get("data", [])
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after: break
    return out

def get_ad_statuses(acct, max_pages=12):
    """{ad_id: effective_status} for every ad in the account (delivery state)."""
    out, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,effective_status", "limit": 500}
        if after: p["after"] = after
        d = api_get("%s/ads" % acct, p)
        if "error" in d: break
        for a in d.get("data", []): out[a["id"]] = a.get("effective_status")
        after = d.get("paging", {}).get("cursors", {}).get("after"); pages += 1
        if not after: break
    return out

def get_new_ad_ids(acct, since_date, max_pages=6):
    """Ad ids created on/after since_date (YYYY-MM-DD). Filtered client-side."""
    ids, after, pages = {}, None, 0
    while pages < max_pages:
        p = {"fields": "id,name,created_time,adset_id,adset_name", "limit": 300}
        if after: p["after"] = after
        d = api_get("%s/ads" % acct, p)
        if "error" in d: break
        for a in d.get("data", []):
            ct = (a.get("created_time") or "")[:10]
            if ct and ct >= since_date:
                ids[a["id"]] = {"adset": a.get("adset_name", ""), "adset_id": a.get("adset_id"), "created": ct}
        after = d.get("paging", {}).get("cursors", {}).get("after")
        pages += 1
        if not after: break
    return ids


# ----------------------- metrics -----------------------
def pick(rows, ordered):
    if not rows: return 0.0
    d = {a.get("action_type"): a.get("value") for a in rows}
    for t in ordered:
        if t in d: return f(d[t])
    return 0.0
def first(rows): return f(rows[0].get("value")) if rows else 0.0
PURCH = ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"]

ATC = ["add_to_cart", "omni_add_to_cart", "offsite_conversion.fb_pixel_add_to_cart"]

def metric(r):
    spend = f(r.get("spend")); impr = f(r.get("impressions")); reach = f(r.get("reach"))
    purch = pick(r.get("actions"), PURCH); rev = pick(r.get("action_values"), PURCH)
    lc = pick(r.get("actions"), ["link_click"]); atc = pick(r.get("actions"), ATC)
    v3 = first(r.get("video_play_actions")); p25 = first(r.get("video_p25_watched_actions"))
    octr = first(r.get("outbound_clicks_ctr")) or f(r.get("inline_link_click_ctr"))
    name = r.get("ad_name", ""); camp = r.get("campaign_name", ""); adset = r.get("adset_name", "")
    blob = (" %s %s %s " % (name, camp, adset)).lower()
    warm = any(k in blob for k in WARM_KW)
    if any(k in blob for k in CAT_KW): typ = "CATALOGUE"
    elif p25 > impr * 0.02: typ = "VIDEO"
    else: typ = "IMAGE"
    return {"ad_id": r.get("ad_id"), "ad_name": name, "campaign": camp, "adset": adset, "adset_id": r.get("adset_id"),
            "aud": "WARM" if warm else "COLD", "type": typ,
            "spend": round(spend, 2), "impr": int(impr), "reach": int(reach),
            "freq": round(f(r.get("frequency")) or (impr / reach if reach else 0), 2),
            "cpm": round(f(r.get("cpm")), 2), "cpmr": round(spend / reach * 1000, 2) if reach else 0.0,
            "ctr": round(f(r.get("ctr")), 2), "octr": round(octr, 2),
            "purch": round(purch, 1), "rev": round(rev, 2), "lc": round(lc, 1), "atc": round(atc, 1),
            "cpa": round(spend / purch, 2) if purch else None,
            "roas": round(rev / spend, 2) if spend else 0.0,
            "aov": round(rev / purch, 2) if purch else None,
            "cvr": round(purch / lc * 100, 2) if lc else 0.0,
            "cpc": round(spend / lc, 2) if lc else 0.0,
            "atc_rate": round(atc / lc * 100, 1) if lc else 0.0,
            "hook": round(v3 / impr * 100, 1) if impr else 0.0,
            "hold": round(p25 / impr * 100, 1) if impr else 0.0}

def med(xs): return round(st.median(xs), 2) if xs else None

def benchmarks(rows):
    B = {}
    for s in set("%s/%s" % (c["aud"], c["type"]) for c in rows):
        g = [c for c in rows if "%s/%s" % (c["aud"], c["type"]) == s and c["cpa"] and c["spend"] >= 500]
        if len(g) >= 2:
            B[s] = {"cpa_med": med([c["cpa"] for c in g]), "roas_med": med([c["roas"] for c in g]),
                    "cpmr_med": med([c["cpmr"] for c in g]), "cvr_med": med([c["cvr"] for c in g]),
                    "hook_med": med([c["hook"] for c in g]), "hold_med": med([c["hold"] for c in g]), "n": len(g)}
    return B

def label(c, B, prev, cold_roas):
    seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
    p = prev.get(c["ad_id"]) if prev else None
    c["d_roas"] = pct(c["roas"], p["roas"]) if p else None
    c["d_cpa"] = pct(c["cpa"], p["cpa"]) if (p and p.get("cpa") and c["cpa"]) else None
    c["d_freq"] = pct(c["freq"], p["freq"]) if p else None
    c["d_octr"] = pct(c["octr"], p["octr"]) if p else None
    c["d_cpm"] = pct(c["cpm"], p["cpm"]) if p else None
    c["prev"] = {k: p.get(k) for k in ("cpa", "roas", "freq", "octr", "spend", "cpm", "cpmr")} if p else None
    if c["spend"] >= MIN_SPEND and p and p.get("freq") and p.get("octr") \
       and c["d_freq"] is not None and c["d_freq"] > FAT_FREQ \
       and c["d_octr"] is not None and c["d_octr"] < -FAT_CTR:
        return "FATIGUE"
    if (c["spend"] < MIN_SPEND * 0.5) and (c["purch"] or 0) < MIN_PUR:
        return "UNDERFUNDED"
    if c["aud"] == "WARM":
        return "AUDIENCE-ASSISTED"
    if not b or not c["cpa"]:
        return "STEADY"
    strong_cpa = c["cpa"] <= b["cpa_med"] * 0.85
    beats_value = c["roas"] >= max(ROAS_T, cold_roas * 0.95)
    if c["spend"] >= MIN_SPEND and c["purch"] >= MIN_PUR and strong_cpa and beats_value and c["freq"] < 3.5:
        return "SCALE OPPORTUNITY"
    if c["spend"] >= MIN_SPEND and strong_cpa and not beats_value:
        return "EFFICIENT-LOW-ROAS"
    if c["spend"] >= MIN_SPEND and c["cpa"] > b["cpa_med"] * 1.3:
        return "BAD CREATIVE"
    if c["cpa"] <= b["cpa_med"]:
        return "PERFORMS"
    return "WATCH"

def agg(ms):
    spend = sum(c["spend"] for c in ms); rev = sum(c["rev"] for c in ms); purch = sum(c["purch"] for c in ms)
    reach = sum(c["reach"] for c in ms); impr = sum(c["impr"] for c in ms)
    lc = sum(c["lc"] for c in ms); atc = sum(c["atc"] for c in ms)
    return {"spend": round(spend), "rev": round(rev), "purch": int(purch), "reach": int(reach), "lc": round(lc),
            "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
            "cpm": round(spend / impr * 1000, 2) if impr else 0, "cpmr": round(spend / reach * 1000, 2) if reach else 0,
            "cvr": round(purch / lc * 100, 2) if lc else 0, "atc_rate": round(atc / lc * 100, 1) if lc else 0,
            "cpc": round(spend / lc, 2) if lc else 0,
            "aov": round(rev / purch) if purch else None,
            "ctr": round(sum(c["ctr"] * c["impr"] for c in ms) / impr, 2) if impr else 0}

def attribute(rows, prev, key):
    """Which single creative drove the account-level change in `key` the most (in EGP)."""
    best, bestv = None, 0
    for c in rows:
        pc = prev.get(c["ad_id"])
        if not pc or c["spend"] < 500: continue
        if key == "cvr":   impact = c["lc"] * (c["cvr"] - pc["cvr"]) / 100 * (c["aov"] or 0)   # revenue EGP
        elif key == "aov": impact = c["purch"] * ((c["aov"] or 0) - (pc["aov"] or 0))          # revenue EGP
        elif key == "cpc": impact = -c["lc"] * ((c["cpc"] or 0) - (pc["cpc"] or 0))            # cheaper clicks = +EGP saved
        else: impact = 0
        if abs(impact) > abs(bestv): best, bestv = c, impact
    return best, round(bestv)

def tag(d, up_good):
    """plain word for a % move; up_good=True means higher is better."""
    if d is None or abs(d) < 5: return "~flat"
    good = (d > 0) == up_good
    return ("up" if d > 0 else "down") + (" (good)" if good else " (drag)")

def diagnose(m, p, rows=None, prev=None, tf=None):
    """Revenue = Traffic x CVR x AOV, clicks priced by CPC.
    Report all 3 levers (CPC / CVR / AOV) with % change every time, name which moved, then dig to the ad."""
    if not p or not p.get("spend"): return "No comparable prior period to diagnose."
    if tf is None:
        tf = "%s→%s vs %s→%s" % (DATES["label"][0], DATES["label"][1], DATES["p_label"][0], DATES["p_label"][1])
    drev = pct(m["rev"], p["rev"]); droas = pct(m["roas"], p["roas"]); dspend = pct(m["spend"], p["spend"])
    dcpc = pct(m["cpc"], p["cpc"]); dcvr = pct(m["cvr"], p["cvr"]); daov = pct(m["aov"] or 0, p["aov"] or 0)
    head = "*%s* · Revenue %s (%s vs %s EGP) · Spend %s · ROAS %s (%s)." % (
        tf, sp(drev), money(m["rev"]), money(p["rev"]), sp(dspend), m["roas"], sp(droas))
    lines = [
        "   • CPC: %s → %s EGP (%s, %s)" % (p["cpc"], m["cpc"], sp(dcpc), tag(dcpc, False)),
        "   • CVR: %s%% → %s%% (%s, %s)" % (p["cvr"], m["cvr"], sp(dcvr), tag(dcvr, True)),
        "   • AOV: %s → %s EGP (%s, %s)" % (money(p["aov"] or 0), money(m["aov"] or 0), sp(daov), tag(daov, True))]
    # which single lever moved revenue the most (by |%|)
    levers = [("CVR", "cvr", dcvr, True, "conversion rate"),
              ("AOV", "aov", daov, True, "order value"),
              ("CPC", "cpc", dcpc, False, "click cost")]
    levers = [x for x in levers if x[2] is not None]
    if not levers or max(abs(x[2]) for x in levers) < 5:
        return head + "\n" + "\n".join(lines) + "\n   *Read:* all three held flat, revenue is steady, nothing to act on."
    name, key, d, up_good, word = max(levers, key=lambda x: abs(x[2]))
    prevv, curv = p[key], m[key]
    # EGP the lever is worth. CVR/AOV = revenue at same clicks; CPC = spend saved.
    if key == "cvr":   egp = round(m["lc"] * (curv - prevv) / 100 * (m["aov"] or 0)); egpw = "%s EGP of revenue at the same clicks" % money(egp)
    elif key == "aov": egp = round(m["purch"] * (curv - prevv));                       egpw = "%s EGP of revenue" % money(egp)
    else:              egp = round(-m["lc"] * (curv - prevv));                          egpw = "%s EGP of spend" % money(egp)
    # ROAS is what the 3 levers exactly control (ROAS = CVR x AOV / CPC). Revenue = that efficiency x spend.
    read = ("*%s* %s %s, the biggest of the three." % (name, "rose" if d > 0 else "fell", sp(d)) + IND +
            "Worth about *%s*." % egpw + IND +
            "That drove ROAS %s." % sp(droas) + IND +
            "Revenue %s because spend %s." % (sp(drev), sp(dspend)))
    out = [head] + lines + ["   *Read:* " + read]
    culprit, cimp = attribute(rows, prev, key) if (rows and prev) else (None, 0)
    if culprit and abs(cimp) > 0:
        pc = prev.get(culprit["ad_id"], {})
        u = "%" if key == "cvr" else " EGP"
        share = round(cimp / egp * 100) if egp else None
        sw = ("~%d%% of the swing" % abs(share)) if share is not None else "the single biggest cause"
        out.append("   *Deeper:* biggest single mover was %s, its %s went %s%s→%s%s on %s EGP spend (%s)." % (
            nm(culprit), name, pc.get(key), u, culprit.get(key), u, money(culprit["spend"]), sw))
    return "\n".join(out)

def analyze(acct, cur_rows, prev_rows, statuses=None):
    rows = [metric(r) for r in cur_rows]
    for c in rows: c["status"] = (statuses or {}).get(c["ad_id"])
    prev = {m["ad_id"]: m for m in (metric(r) for r in prev_rows)}
    B = benchmarks(rows)
    cold = [c for c in rows if c["aud"] == "COLD"]
    cold_s = sum(c["spend"] for c in cold); cold_r = sum(c["rev"] for c in cold)
    cold_roas = round(cold_r / cold_s, 2) if cold_s else 0
    # Pareto 80/20: mark the vital few that make up 80% of spend + a hard significance floor
    tot_spend = sum(c["spend"] for c in rows) or 1
    cum = 0
    for c in sorted(rows, key=lambda c: c["spend"], reverse=True):
        cum += c["spend"]; c["vital"] = cum <= 0.8 * tot_spend + 0.01
        c["significant"] = c["spend"] >= max(MIN_SPEND, 0.03 * tot_spend)
    _acc = agg(rows); acc_cpmr = _acc["cpmr"] or 1; acc_cvr = _acc["cvr"] or 1; acc_roas = _acc["roas"] or 1
    for c in rows:
        c["label"] = label(c, B, prev, cold_roas)
        b = B.get("%s/%s" % (c["aud"], c["type"]))
        c["gap"] = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100) if (b and c["cpa"]) else None
        c["seg_cpa_med"] = b["cpa_med"] if b else None
        # store account reference values so every block can show raw side-by-side (never percentage points)
        c["acc_cpmr"] = round(acc_cpmr); c["acc_cvr"] = round(acc_cvr, 2); c["acc_roas"] = round(acc_roas, 2)
        c["cpmr_vs_acc"] = round(pct(c["cpmr"], acc_cpmr)) if c["cpmr"] else None
        c["cvr_vs_acc"] = round(pct(c["cvr"], acc_cvr)) if c["cvr"] else None
        c["waste"] = round(c["purch"] * (c["cpa"] - b["cpa_med"])) if (c["aud"] == "COLD" and b and c["cpa"] and c["cpa"] > b["cpa_med"] and c["spend"] >= MIN_SPEND) else 0
        # most scalable = best blend of low CPMR, low CPA, high ROAS, high CVR (each vs its benchmark), with real spend behind it
        if c["label"] == "SCALE OPPORTUNITY" and b:
            r_roas = c["roas"] / (cold_roas or 1)
            r_cvr = c["cvr"] / (acc_cvr or 1)
            r_cpa = (b["cpa_med"] or 1) / (c["cpa"] or 1)
            r_cpmr = (acc_cpmr or 1) / (c["cpmr"] or 1)
            c["scale_score"] = round((r_roas + r_cvr + r_cpa + r_cpmr) * (c["spend"] ** 0.5), 1)
        else:
            c["scale_score"] = 0
    cur_a = agg(rows); prev_a = agg(list(prev.values())) if prev else None
    cat = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    summary = dict(cur_a)
    summary.update({"revenue": cur_a["rev"], "purchases": cur_a["purch"],
                    "cat_pct": round(cat / cur_a["spend"] * 100) if cur_a["spend"] else 0,
                    "cold_roas": cold_roas, "prev": prev_a,
                    "d_spend": pct(cur_a["spend"], prev_a["spend"]) if prev_a else None,
                    "d_roas": pct(cur_a["roas"], prev_a["roas"]) if prev_a else None,
                    "d_cpa": pct(cur_a["cpa"] or 0, (prev_a or {}).get("cpa") or 0) if prev_a else None,
                    "d_cpm": pct(cur_a["cpm"], prev_a["cpm"]) if prev_a else None,
                    "d_cpmr": pct(cur_a["cpmr"], prev_a["cpmr"]) if prev_a else None,
                    "d_cvr": pct(cur_a["cvr"], prev_a["cvr"]) if prev_a else None,
                    "d_atc": pct(cur_a["atc_rate"], prev_a["atc_rate"]) if prev_a else None,
                    "diagnosis": diagnose(cur_a, prev_a, rows, prev)})
    winners = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    offenders = [c for c in rows if c["waste"] > 0]
    offender = max(offenders, key=lambda c: c["waste"]) if offenders else None
    best = max(winners, key=lambda c: c["scale_score"]) if winners else None
    off_id = offender["ad_id"] if offender else None
    # the reallocation target must be a DIFFERENT ad than the one being cut
    if not best or best["ad_id"] == off_id:
        cand = [c for c in rows if c["aud"] == "COLD" and c["spend"] >= MIN_SPEND and c["roas"]
                and c["ad_id"] != off_id and c["roas"] >= cold_roas]
        best = max(cand, key=lambda c: c["roas"]) if cand else best
    return {"account": acct, "summary": summary, "benchmarks": B,
            "creatives": sorted(rows, key=lambda c: c["spend"], reverse=True),
            "offender": offender,
            "opportunity": (max(winners, key=lambda c: c["scale_score"]) if winners else None),
            "best_target": best if (best and best["ad_id"] != off_id) else None}


# ----------------------- Slack -----------------------
def slack(channel, text):
    if not channel: return
    if not SLACK_TOKEN:
        print("[slack:%s] %s\n" % (channel, text[:80])); return
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False, "mrkdwn": True}).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer %s" % SLACK_TOKEN, "Content-Type": "application/json; charset=utf-8"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"): sys.stderr.write("[slack] %s: %s\n" % (channel, r.get("error")))
    except Exception as e:
        sys.stderr.write("[slack] %s\n" % e)

def cur(A): return A["account"].get("currency", "")
def tgt(A):
    t = A.get("best_target")
    return nm(t) if t else "the lowest-CPA cold winner"
BAR = "━━━━━━━━━━━━━━━━━━"
def cpmrword(c):
    """Reach cost shown raw next to the account average. Never percentage points."""
    if c.get("acc_cpmr") is None: return ""
    return " vs %s avg" % money(c["acc_cpmr"])
def cmet(c):
    """Two clean metric lines for one creative. CVR and CPMR shown next to the account average."""
    return ("     Spend %s · Rev %s · ROAS %s · CPA %s · AOV %s\n"
            "     CVR %s%%%s · ATC %s%% · *CPMR %s*%s · CPM %s · Freq %s") % (
        money(c["spend"]), money(c["rev"]), c["roas"], money(c["cpa"]), money(c["aov"]),
        c["cvr"], vs_avg(c.get("acc_cvr"), pct=True), c["atc_rate"], money(c["cpmr"]), cpmrword(c), money(c["cpm"]), c["freq"])
def gapw(g):
    return "n/a" if g is None else ("%d%% below" % abs(g) if g <= 0 else "%d%% above" % g)
IND = "\n     "  # indent used to break after every period inside a Why block
def why_scale(c, cold):
    return ("Most scalable of the winners." + IND +
            "CPA %s vs group median %s." % (money(c["cpa"]), money(c["seg_cpa_med"])) + IND +
            "ROAS %s vs cold bar %s." % (c["roas"], cold) + IND +
            "CVR %s%% vs %s%% account avg." % (c["cvr"], c.get("acc_cvr")) + IND +
            "CPMR %s vs %s account avg, so it reaches people cheaper than the rest." % (money(c["cpmr"]), money(c.get("acc_cpmr"))) + IND +
            "Best blend of cheap reach, cheap conversion and value.")
def why_cut(c):
    return ("CPA %s vs group median %s at ROAS %s." % (money(c["cpa"]), money(c["seg_cpa_med"]), c["roas"]) + IND +
            "CPMR %s vs %s account avg." % (money(c["cpmr"]), money(c.get("acc_cpmr"))) + IND +
            "Wasting about %s this week versus reallocating that spend." % money(c["waste"]))
def fatigue_line(c):
    """One-line fatigue evidence with the exact numbers and window."""
    p = c.get("prev") or {}
    d1, d2 = DATES["label"]; q1, q2 = DATES["p_label"]
    cpm = "CPM steady" if (c.get("d_cpm") is not None and abs(c["d_cpm"]) < 12) else "CPM %s" % sp(c.get("d_cpm"))
    return "Freq %s→%s (%s) · Outbound CTR %s%%→%s%% (%s) · %s · Spend %s   _(%s → %s vs %s → %s)_" % (
        p.get("freq"), c["freq"], sp(c["d_freq"]), p.get("octr"), c["octr"], sp(c["d_octr"]), cpm, money(c["spend"]),
        d1, d2, q1, q2)
def why_fatigue(c):
    return ("Frequency up, Outbound CTR down means the same people seeing it and fewer clicking." + IND +
            "Creative fatigue, not the auction." + IND + "Refresh the hook." + IND + fatigue_line(c))
def why_lowroas(c, cold):
    return ("Cheap CPA %s vs group median %s, but ROAS %s is under the cold bar of %s." % (
            money(c["cpa"]), money(c["seg_cpa_med"]), c["roas"], cold) + IND +
            "AOV is only %s." % money(c["aov"]) + IND + "Efficient reach, low value.")

def block(icon, title, name, status, do, metrics, why):
    return "\n".join([
        "%s  *%s* - %s   `%s`" % (icon, title, name, status),
        "     :arrow_right: _%s_" % do,
        metrics,
        "     *Why:* %s" % why, ""])

def msg_digest(A):
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    L = ["%s  :bar_chart:  *%s - LAST 7 DAYS*  _(refreshed every morning)_" % (MENTION, A["account"]["name"].upper()),
         ":date: *%s → %s*   vs   *%s → %s*   (all values in %s)" % (d1, d2, p1, p2, cc), BAR,
         "*ACCOUNT - all ads*",
         "Spend *%s* (%s) · Revenue *%s* · %d purchases" % (money(s["spend"]), sp(s["d_spend"]), money(s["revenue"]), s["purchases"]),
         "ROAS *%s* (%s) · CPA *%s* (%s) · AOV %s" % (s["roas"], sp(s["d_roas"]), money(s["cpa"]), sp(s["d_cpa"]), money(s["aov"])),
         "CVR %s%% (%s) · ATC %s%% (%s) · CPMR *%s* (%s) · CPM %s (%s)" % (s["cvr"], sp(s["d_cvr"]), s["atc_rate"], sp(s["d_atc"]), money(s["cpmr"]), sp(s["d_cpmr"]), money(s["cpm"]), sp(s["d_cpm"])),
         "Cold prospecting ROAS *%s* = the bar to beat.%s" % (s["cold_roas"], (" Catalogue %d%% of spend inflates blended ROAS." % s["cat_pct"]) if s["cat_pct"] >= 15 else ""),
         "", ":mag: *WHY REVENUE MOVED*  (Revenue = clicks × CVR × AOV)", s["diagnosis"], BAR, "*DO NOW*", ""]
    did = False
    op = A["opportunity"]
    if op:
        L.append(block(":rocket:", "SCALE", nm(op), statuslabel(op), "Raise budget 20-30%.", cmet(op), why_scale(op, s["cold_roas"]))); did = True
    off = A["offender"]
    if off:
        t = A.get("best_target")
        act = ("Drop 30-40%%, move budget to %s." % nm(t)) if t else "Drop 30-40%%. Park the freed budget until a winner proves out."
        L.append(block(":rotating_light:", "CUT", nm(off), statuslabel(off), act, cmet(off), why_cut(off))); did = True
    # only significant (80/20) fatiguing ads, never trivial-spend ads
    fat = [c for c in rows if c["label"] == "FATIGUE" and c.get("significant")]
    if fat:
        c = fat[0]
        L.append(block(":recycle:", "REFRESH", nm(c), statuslabel(c), "New first 3 seconds, keep the concept.", cmet(c), why_fatigue(c))); did = True
    low = [c for c in rows if c["label"] == "EFFICIENT-LOW-ROAS" and c.get("significant")]
    if low:
        c = low[0]
        L.append(block(":test_tube:", "TEST HIGHER AOV", nm(low[0]), statuslabel(low[0]), "Same hook, pricier product.", cmet(low[0]), why_lowroas(low[0], s["cold_roas"]))); did = True
    if not did:
        L.append("_Nothing crossed an action threshold. Everything is inside its guardrails._\n")
    if len(fat) > 1:
        L.append(":chart_with_downwards_trend: *Also fatiguing (%d), the numbers:*" % (len(fat) - 1))
        for c in fat[1:5]:
            L.append("• %s   `%s`\n     %s" % (nm(c), statuslabel(c), fatigue_line(c)))
        L.append("")
    L += [BAR, "_Window: last 7 full days vs the 7 before. Runs automatically 9 AM Cairo, every day._"]
    return "\n".join(L)


# ----------------------- 3-day pulse (80/20) -----------------------
def prow(r, key):
    spend = f(r.get("spend")); purch = pick(r.get("actions"), PURCH); reach = f(r.get("reach"))
    lc = pick(r.get("actions"), ["link_click"])
    rev = pick(r.get("action_values"), PURCH) or f(r.get("purchase_roas")) * spend
    return {"name": r.get(key, "(unnamed)"), "id": r.get("ad_id") or r.get("adset_id"),
            "spend": round(spend), "rev": round(rev), "purch": round(purch),
            "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
            "cpmr": round(spend / reach * 1000) if reach else None,
            "cvr": round(purch / lc * 100, 2) if lc else None}

def pareto(rows):
    rows = [x for x in rows if x["spend"] > 0]
    total = sum(x["spend"] for x in rows) or 1
    vital, cum = [], 0
    for x in sorted(rows, key=lambda x: x["spend"], reverse=True):
        vital.append(x); cum += x["spend"]
        if cum >= 0.8 * total: break
    real = [x for x in rows if x["spend"] >= total * 0.03 and x["purch"]]
    best = sorted(real, key=lambda x: x["roas"], reverse=True)[:3]
    worst = sorted(real, key=lambda x: x["roas"])[:3]
    return vital, best, worst, total

def linkrow(x, cc, is_ad, acc_roas=None, acc_cpmr=None, acc_cvr=None):
    label = nm({"ad_name": x["name"], "ad_id": x["id"]}) if is_ad else aset_link(x["name"], x["id"])
    st = ""
    if is_ad and STATUS.get(x["id"]):
        st = "   `%s`" % STAT.get(STATUS[x["id"]], STATUS[x["id"]])
    roasv = vs_avg(acc_roas)
    cvr = ("  ·  CVR %s%%%s" % (x["cvr"], vs_avg(acc_cvr, pct=True))) if x.get("cvr") is not None else ""
    cpmr = ("  ·  CPMR %s%s" % (money(x["cpmr"]), vs_avg(acc_cpmr, is_money=True))) if x.get("cpmr") is not None else ""
    return "   • %s%s\n        Spend %s · Rev %s · ROAS %s%s · CPA %s%s%s" % (
        label, st, money(x["spend"]), money(x["rev"]), x["roas"], roasv, money(x["cpa"]), cvr, cpmr)

def pulse_3day(acct, ad_rows, set_rows, m3, p3m):
    cc = acct.get("currency", "")
    ads = [prow(r, "ad_name") for r in ad_rows]
    sets = [prow(r, "adset_name") for r in set_rows]
    tot = sum(x["spend"] for x in ads)
    if tot <= 0: return None
    rev = sum(x["roas"] * x["spend"] for x in ads); purch = sum(x["purch"] for x in ads)
    d1, d2 = DATES["l3"]; q1, q2 = DATES["p3"]
    _, bset, wset, tset = pareto(sets)
    _, bad, wad, _ = pareto(ads)
    L = ["%s  :zap:  *%s - 3-Day Pulse*" % (MENTION, acct["name"]),
         ":date: *%s → %s*   (vs prior 3 days %s → %s)" % (d1, d2, q1, q2), "",
         "Spend *%s %s*  ·  ROAS *%s*  ·  %d purch" % (money(tot), cc, round(rev / tot, 2), int(purch))]
    if m3:
        dd = lambda k: sp(pct(m3.get(k), (p3m or {}).get(k))) if p3m else "n/a"
        L.append("ROAS %s (%s)  ·  CPA %s (%s)  ·  *CPMR %s* (%s)  ·  CPM %s (%s)" %
                 (m3["roas"], dd("roas"), money(m3["cpa"]), dd("cpa"), money(m3["cpmr"]), dd("cpmr"), money(m3["cpm"]), dd("cpm")))
        L.append("CVR %s%% (%s)  ·  ATC %s%% (%s)  ·  AOV %s %s (%s)" %
                 (m3["cvr"], dd("cvr"), m3["atc_rate"], dd("atc_rate"), money(m3["aov"]), cc, dd("aov")))
        tf3 = "%s→%s vs %s→%s" % (DATES["l3"][0], DATES["l3"][1], DATES["p3"][0], DATES["p3"][1])
        L.append(":mag: *WHY REVENUE MOVED*  (Revenue = clicks × CVR × AOV)")
        L.append(diagnose(m3, p3m, tf=tf3))
    ar = (m3 or {}).get("roas"); ac = (m3 or {}).get("cpmr"); av = (m3 or {}).get("cvr")
    lr = lambda x, isad: linkrow(x, cc, isad, ar, ac, av)
    L += ["", "_Verdicts below are vs this account's 3-day average._",
         ":large_green_circle: *Best adsets*"] + [lr(x, False) for x in bset] + \
        ["", ":red_circle: *Worst adsets*"] + [lr(x, False) for x in wset] + \
        ["", ":large_green_circle: *Best ads*"] + [lr(x, True) for x in bad] + \
        ["", ":red_circle: *Worst ads*"] + [lr(x, True) for x in wad] + \
        ["", "_Rolling 3-day read. Runs 9 AM Cairo daily._"]
    return "\n".join(L)


# ----------------------- New launches -----------------------
def msg_launches(acct, creatives, prev_ids, acc_roas=None, acc_cpmr=None, acc_cvr=None):
    cc = acct.get("currency", "")
    launched = [c for c in creatives if c["ad_id"] not in prev_ids and c["spend"] > 0]
    d1, d2 = DATES["label"]
    head = ["%s  :rocket:  *%s - New Launches*" % (MENTION, acct["name"]),
            ":date: new this week (no spend in the prior 7 days)  ·  performance *%s → %s*" % (d1, d2),
            "_Verdicts are vs the account average._", ""]
    if not launched:
        return "\n".join(head + ["_No new ads started spending in the last 7 days._"])
    groups = {}
    for c in launched:
        g = groups.setdefault(c["adset"] or "(no adset)", {"spend": 0, "rev": 0, "purch": 0, "reach": 0, "lc": 0, "sid": c.get("adset_id"), "ads": []})
        g["spend"] += c["spend"]; g["rev"] += c["rev"]; g["purch"] += c["purch"]
        g["reach"] += c.get("reach", 0); g["lc"] += c.get("lc", 0); g["ads"].append(c)
    ordered = sorted(groups.items(), key=lambda kv: kv[1]["spend"], reverse=True)[:8]
    L = list(head)
    for name, g in ordered:
        roas = round(g["rev"] / g["spend"], 2) if g["spend"] else 0
        cpa = money(round(g["spend"] / g["purch"])) if g["purch"] else "n/a"
        gcpmr = round(g["spend"] / g["reach"] * 1000) if g["reach"] else None
        gcvr = round(g["purch"] / g["lc"] * 100, 2) if g["lc"] else None
        best = max([a for a in g["ads"] if a["spend"] > 0], key=lambda a: a["roas"], default=None)
        L.append("• %s" % aset_link(name, g["sid"]))
        L.append("     spend %s %s  ·  ROAS %s%s  ·  CPA %s %s  ·  %d purch" % (
            money(g["spend"]), cc, roas, vs_avg(acc_roas), cpa, cc, int(g["purch"])))
        L.append("     CVR %s%%%s  ·  CPMR %s%s" % (
            gcvr if gcvr is not None else "n/a", vs_avg(acc_cvr, pct=True),
            money(gcpmr) if gcpmr is not None else "n/a", vs_avg(acc_cpmr, is_money=True)))
        if best:
            L.append("     best ad: %s  `%s`  (ROAS %s%s, CPA %s %s, CVR %s%%%s, CPMR %s%s)" % (
                nm(best), statuslabel(best), best["roas"], vs_avg(acc_roas), money(best["cpa"]), cc,
                best["cvr"], vs_avg(acc_cvr, pct=True), money(best["cpmr"]), vs_avg(acc_cpmr, is_money=True)))
        L.append("")
    L.append("_New launches only. Runs 9 AM Cairo daily._")
    return "\n".join(L)


# ----------------------- Strategic Advisor (weekly) -----------------------
def adref(c):
    return "%s (ROAS %s, CPA %s, CVR %s%%, CPMR %s%s)" % (
        nm(c), c["roas"], money(c["cpa"]), c["cvr"], money(c["cpmr"]), cpmrword(c))
# agreed BSD bands (from the Meta Ads Playbook in the vault)
def hook_band(h): return "good" if h >= 20 else ("average" if h >= 12 else "bad")
def hold_band(h): return "good" if h >= 35 else ("average" if h >= 22 else "bad")
def freq_ceiling(c):
    # DPA/catalogue read on a higher ceiling: bench 2.5, fatigue 3.0. Everything else: cold ceiling 2.0.
    return 3.0 if c.get("type") == "CATALOGUE" else 2.0
def is_saturating(c):
    return c["aud"] == "COLD" and c.get("significant") and c["freq"] > freq_ceiling(c)

def msg_advisor(A):
    """A weekly strategic brief: read the numbers, then tell Shavi exactly what to do and why."""
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    d1, d2 = DATES["label"]; p1, p2 = DATES["p_label"]
    prev = s.get("prev") or {}
    dcpc = pct(s.get("cpc"), prev.get("cpc")); dcvr = pct(s["cvr"], prev.get("cvr"))
    daov = pct(s["aov"] or 0, prev.get("aov") or 0)
    cold = s["cold_roas"]; blended = s["roas"]
    winners = sorted([c for c in rows if c["label"] == "SCALE OPPORTUNITY"], key=lambda c: c.get("scale_score", 0), reverse=True)[:3]
    if not winners:
        winners = sorted([c for c in rows if c["aud"] == "COLD" and c.get("significant") and c["roas"] and c["roas"] >= cold],
                         key=lambda c: c["roas"], reverse=True)[:3]
    bleeders = sorted([c for c in rows if c.get("waste", 0) > 0], key=lambda c: c["waste"], reverse=True)[:3]
    if not bleeders:
        bleeders = sorted([c for c in rows if c.get("significant") and c["aud"] == "COLD" and c["roas"] and c["roas"] < cold * 0.8],
                          key=lambda c: c["roas"])[:3]
    # a winner and a bleeder must never be the same ad (no "move from X into X")
    bleed_ids = {b["ad_id"] for b in bleeders}
    winners = [w for w in winners if w["ad_id"] not in bleed_ids]
    fatiguing = [c for c in rows if c["label"] == "FATIGUE" and c.get("significant")]
    assisted = sorted([c for c in rows if c["aud"] == "WARM" and c["roas"] and c["spend"] >= MIN_SPEND],
                      key=lambda c: c["roas"], reverse=True)[:2]
    saturating = sorted([c for c in rows if is_saturating(c)], key=lambda c: c["spend"], reverse=True)[:3]
    videos = [c for c in rows if c["type"] == "VIDEO" and c.get("significant")]
    top_vid = max(videos, key=lambda c: c["spend"], default=None)

    levers = [("CVR", dcvr, True), ("AOV", daov, True), ("CPC", dcpc, False)]
    levers = [x for x in levers if x[1] is not None]
    lever = max(levers, key=lambda x: abs(x[1]))[0] if levers else "CVR"
    fix = {
        "CVR": ("*Conversion is the lever.* That is a landing page, offer and checkout problem, not media." + IND +
                "Audit the PDP, the price and shipping presentation, COD friction and social proof." + IND +
                "Revenue = Traffic × CVR × AOV, so a CVR fix compounds on every click you already pay for."),
        "AOV": ("*Order value is the lever.* Pull the offer mechanics: bundles, gift-with-purchase, volume tiers and a higher-price hero." + IND +
                "A higher AOV lifts ROAS without touching CPC or CVR, so it is the cheapest win available."),
        "CPC": ("*Click cost is the lever.* That is creative and auction." + IND +
                "If hook rate broke, the problem is the first 3 seconds, that is a thumb-stop fix." + IND +
                "If hook held but Outbound CTR fell, the promise or the click incentive is weak." + IND +
                "Refresh the fatiguing ads, add new angles, and widen the audience so frequency drops."),
    }[lever]

    L = ["%s  :compass:  *%s - STRATEGIC ADVISOR*" % (MENTION, A["account"]["name"].upper()),
         ":date: Week of *%s → %s*   vs   *%s → %s*   (all values in %s)" % (d1, d2, p1, p2, cc), BAR,
         "*THE READ*",
         "Spent %s to make %s at ROAS *%s*.  Cold prospecting ROAS is *%s*, the number that actually matters." % (
             money(s["spend"]), money(s["revenue"]), blended, cold),
         "Blended ROAS %s vs cold %s%s." % (
             blended, cold,
             (".  Catalogue is %d%% of spend and is propping up the blended number, judge scaling on cold only" % s["cat_pct"]) if s["cat_pct"] >= 20 else ""),
         "The lever that moved performance this week: *%s*.  %s" % (
             lever, ", ".join("%s %s" % (n, sp(d)) for n, d, _ in levers)),
         "ROAS here is attributed.  Reconcile against MER and AMER before any big cut, and treat catalogue ROAS as over-attributed.",
         "", BAR, "*WHAT IS WORKING, scale these*"]
    if winners:
        for c in winners: L.append("• %s" % adref(c))
        L.append("_Cheapest reach and best conversion. This is where new budget should go._")
    else:
        L.append("_No ad cleared the scale bar this week. Priority is finding one, not scaling._")
    L += ["", "*WHAT IS BLEEDING, cut or fix*"]
    if bleeders:
        for c in bleeders:
            w = ("  wasting ~%s/wk" % money(c["waste"])) if c.get("waste") else ""
            L.append("• %s%s" % (adref(c), w))
    else:
        L.append("_Nothing is badly bleeding. Guardrails are holding._")
    # Saturation and creative read, straight from the BSD playbook (cold freq ceiling 2.0, hook/hold bands)
    L += ["", BAR, "*SATURATION & CREATIVE*"]
    if saturating:
        L.append("Cold frequency ceiling is 2.0, over that is saturation not a bad ad.")
        for c in saturating:
            L.append("• %s at frequency *%s* on cold." % (nm(c), c["freq"]))
        L.append("_Signature: ROAS falls while hook and CTR hold and CPM climbs._")
        L.append("_Fix: pull budget back, let frequency recover toward 2.5, then scale in 15% increments._")
    else:
        L.append("Cold frequency is under the 2.0 ceiling, no saturation.  Room to scale.")
    if top_vid and top_vid["hook"] > 0:
        L.append("Top video %s: hook %s%% (%s), hold %s%% (%s)." % (
            nm(top_vid), top_vid["hook"], hook_band(top_vid["hook"]), top_vid["hold"], hold_band(top_vid["hold"])))
        if hook_band(top_vid["hook"]) == "bad":
            L.append("_Hook is under 12%, the first 3 seconds are losing people.  Rework the thumb-stop._")
        elif hold_band(top_vid["hold"]) == "bad":
            L.append("_Hook lands but hold is under 22%, the body drops them.  Tighten the middle._")
    L += ["", BAR, "*THE MAIN LEVER TO FIX*", fix, "", BAR, "*STRATEGIC MOVES THIS WEEK*"]
    n = 0
    if bleeders and winners:
        move = round(bleeders[0]["spend"] * 0.35)
        n += 1; L.append("%d. *Reallocate.* Pull ~%s from %s and push it into %s.  Same audience pool, better creative economics." % (
            n, money(move), nm(bleeders[0]), nm(winners[0])))
    if winners:
        w = winners[0]
        sat = w["freq"] > freq_ceiling(w)
        how = "Frequency is %s, already over the 2.0 ceiling, so add budget slowly in 15%% steps and watch CPM." % w["freq"] if sat \
              else "Frequency is %s, under the 2.0 ceiling, so scale in 15%% increments." % w["freq"]
        n += 1; L.append("%d. *Scale the winner.* Raise %s.  %s" % (n, nm(w), how))
    if fatiguing:
        names = ", ".join(nm(c) for c in fatiguing[:3])
        n += 1; L.append("%d. *Refresh creative.* %d ad(s) are fatiguing (%s).  New first 3 seconds, keep the concept.  Then launch 3-5 fresh concepts, creative volume is the real lever on CPC." % (
            n, len(fatiguing), names))
    if saturating and not fatiguing:
        n += 1; L.append("%d. *Relieve saturation.* Pull budget on the over-frequency ads, let them recover toward 2.5, then re-enter.  Broaden the audience so the same creative reaches fresh people." % n)
    if lever == "AOV" or (s["aov"] and daov is not None and daov < -5):
        n += 1; L.append("%d. *Lift AOV.* Add a bundle or gift-with-purchase and push a higher-price hero.  AOV is %s, every 10%% here is 10%% ROAS for free." % (n, money(s["aov"])))
    if s["cat_pct"] >= 25:
        n += 1; L.append("%d. *Catalogue discipline.* It is %d%% of spend and masks weak prospecting.  Cap it and force cold prospecting to stand on its own ROAS of %s." % (n, s["cat_pct"], cold))
    if assisted:
        names = ", ".join(nm(c) for c in assisted)
        n += 1; L.append("%d. *Cold-test your warm winners.* %s look strong but are audience-assisted.  Test their creative cold to find a true prospecting winner." % (n, names))
    if n == 0:
        L.append("_Hold. Everything is inside its guardrails, keep feeding the winners and watch frequency._")
    # the one thing
    if bleeders and winners:
        one = "Move budget from %s into %s today." % (nm(bleeders[0]), nm(winners[0]))
    elif winners:
        one = "Scale %s and protect it, it is your engine." % nm(winners[0])
    else:
        one = "Fix %s. Nothing scales until that lever turns." % lever
    L += ["", BAR, ":dart: *THE ONE THING:*  %s" % one,
          "_Strategic read. Runs once a week. Numbers are the last 7 days vs the 7 before._"]
    return "\n".join(L)


# ----------------------- main -----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    global SLACK_TOKEN, NUMID, STATUS
    if a.dry_run: SLACK_TOKEN = ""
    if not TOKEN: sys.stderr.write("META_ACCESS_TOKEN missing\n"); sys.exit(1)

    try:
        from zoneinfo import ZoneInfo; z = ZoneInfo(TZ)
    except Exception:
        z = datetime.timezone.utc
    now = datetime.datetime.now(z); y = (now - datetime.timedelta(days=1)).date()
    last = {"since": str(y - datetime.timedelta(days=6)), "until": str(y)}
    prev = {"since": str(y - datetime.timedelta(days=13)), "until": str(y - datetime.timedelta(days=7))}
    l3 = {"since": str(y - datetime.timedelta(days=2)), "until": str(y)}
    prev3 = {"since": str(y - datetime.timedelta(days=5)), "until": str(y - datetime.timedelta(days=3))}
    DATES["label"] = (fmt_day(y - datetime.timedelta(days=6)), fmt_day(y))
    DATES["p_label"] = (fmt_day(y - datetime.timedelta(days=13)), fmt_day(y - datetime.timedelta(days=7)))
    DATES["l3"] = (fmt_day(y - datetime.timedelta(days=2)), fmt_day(y))
    DATES["p3"] = (fmt_day(y - datetime.timedelta(days=5)), fmt_day(y - datetime.timedelta(days=3)))
    ACC = "spend,impressions,reach,frequency,cpm,ctr,actions,action_values"

    report = {"generated_at": now.isoformat(), "timezone": TZ, "sample": False, "accounts": []}
    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        STATUS = get_ad_statuses(acct["id"])
        A = analyze(acct, cur_rows, prev_rows, STATUS)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)
        NUMID = acct["id"].replace("act_", "")
        ch = channel_for(acct["name"]); lch = channel_launch_for(acct["name"])
        ach = channel_advisor_for(acct["name"])
        sm = A["summary"]
        if a.daily or a.dry_run:
            slack(ch, msg_digest(A))
            a3 = get_insights(acct["id"], l3, level="account", fields=ACC)
            ap3 = get_insights(acct["id"], prev3, level="account", fields=ACC)
            m3 = metric(a3[0]) if a3 else None; p3m = metric(ap3[0]) if ap3 else None
            p3 = pulse_3day(acct, get_insights(acct["id"], l3, level="ad", fields=LITE_FIELDS, extra="ad_name,ad_id"),
                            get_insights(acct["id"], l3, level="adset", fields=LITE_FIELDS, extra="adset_name,adset_id"), m3, p3m)
            if p3: slack(ch, p3)
            if lch:
                prev_ids = set(r.get("ad_id") for r in prev_rows)
                slack(lch, msg_launches(acct, A["creatives"], prev_ids, sm["roas"], sm["cpmr"], sm["cvr"]))
        if a.weekly or a.dry_run:
            slack(ach, msg_advisor(A))
        time.sleep(1)

    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("prev", None)
    save_json(DATA_PATH, report)
    sys.stderr.write("[done] %d accounts\n" % len(report["accounts"]))

if __name__ == "__main__":
    main()
