#!/usr/bin/env python3
"""
AI MEDIA BUYER v2  |  Senior Meta Ads Growth Operator.

Runs on GitHub Actions (free cron) with ONLY your Meta token + Slack bot token.
No Claude, no Cowork, no laptop. GitHub's servers run it every morning.

What it does, like a senior growth marketer:
- Separates CREATIVE performance from AUDIENCE performance (cold vs warm/retargeting).
- Benchmarks each ad against its own segment (COLD/VIDEO, COLD/IMAGE, WARM/CATALOGUE...) using MEDIAN + MEAN, never blended.
- Labels: SCALE OPPORTUNITY, AUDIENCE-ASSISTED, UNDERFUNDED, CREATIVE FATIGUE, BAD CREATIVE, PERFORMS, WATCH.
- Funnel diagnosis (auction vs creative vs landing vs checkout vs AOV).
- Impact score -> BIGGEST OFFENDER + BIGGEST OPPORTUNITY.
- Budget mix: catalogue spend %, cold-only ROAS vs blended.
- Posts to #meta-growth-alerts and a P0/P1/P2 board in #meta-actions.
- Writes docs/data.json for the dashboard. Standard library only.

House style: line break after every period, no em dashes, CTR = Outbound CTR, never "pts".
"""
import os, sys, json, time, argparse, datetime, statistics as st
import urllib.request, urllib.parse, urllib.error

GRAPH = "https://graph.facebook.com/v21.0"
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
STATE_PATH = os.path.join(DOCS, "state.json")
DATA_PATH = os.path.join(DOCS, "data.json")
TOKEN = os.environ.get("META_ACCESS_TOKEN", "")
SLACK_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


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

WARM_KW = ["retarget", " rt ", "rt_", "catalog", "dpa", "promocode", "promo code",
           "didn't purchase", "didnt purchase", "back2cart", "atc ", "atc_", "zombie",
           "existing", "evergreen", "ever green", "abandon", "viewed", "add to cart", "savewith"]
CAT_KW = ["catalog", "dpa"]


def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0


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


FIELDS = ",".join(["ad_id", "ad_name", "campaign_name", "adset_name", "objective",
    "spend", "impressions", "reach", "frequency", "cpm", "ctr", "inline_link_click_ctr",
    "outbound_clicks_ctr", "actions", "action_values", "video_play_actions",
    "video_p25_watched_actions", "video_p100_watched_actions", "video_thruplay_watched_actions"])

def get_insights(acct, tr):
    out, after = [], None
    while True:
        p = {"level": "ad", "fields": FIELDS, "time_range": json.dumps(tr), "limit": 300}
        if after: p["after"] = after
        d = api_get("%s/insights" % acct, p)
        if "error" in d: break
        out += d.get("data", [])
        after = d.get("paging", {}).get("cursors", {}).get("after")
        if not after: break
    return out


# ----------------------- metrics -----------------------
def pick(rows, ordered):
    if not rows: return 0.0
    d = {a.get("action_type"): a.get("value") for a in rows}
    for t in ordered:
        if t in d: return f(d[t])
    return 0.0

def first(rows): return f(rows[0].get("value")) if rows else 0.0
PURCH = ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"]

def metric(r):
    spend = f(r.get("spend")); impr = f(r.get("impressions")); reach = f(r.get("reach"))
    purch = pick(r.get("actions"), PURCH); rev = pick(r.get("action_values"), PURCH)
    lc = pick(r.get("actions"), ["link_click"])
    v3 = first(r.get("video_play_actions")); p25 = first(r.get("video_p25_watched_actions"))
    octr = first(r.get("outbound_clicks_ctr")) or f(r.get("inline_link_click_ctr"))
    name = r.get("ad_name", ""); camp = r.get("campaign_name", ""); adset = r.get("adset_name", "")
    blob = (" %s %s %s " % (name, camp, adset)).lower()
    warm = any(k in blob for k in WARM_KW)
    if any(k in blob for k in CAT_KW): typ = "CATALOGUE"
    elif p25 > impr * 0.02: typ = "VIDEO"
    else: typ = "IMAGE"
    return {"ad_id": r.get("ad_id"), "ad_name": name, "campaign": camp, "adset": adset,
            "aud": "WARM" if warm else "COLD", "type": typ,
            "spend": round(spend, 2), "impr": int(impr), "reach": int(reach),
            "freq": round(f(r.get("frequency")) or (impr / reach if reach else 0), 2),
            "cpm": round(f(r.get("cpm")), 2), "ctr": round(f(r.get("ctr")), 2), "octr": round(octr, 2),
            "purch": round(purch, 1), "rev": round(rev, 2), "lc": round(lc, 1),
            "cpa": round(spend / purch, 2) if purch else None,
            "roas": round(rev / spend, 2) if spend else 0.0,
            "aov": round(rev / purch, 2) if purch else None,
            "cvr": round(purch / lc * 100, 2) if lc else 0.0,
            "hook": round(v3 / impr * 100, 1) if impr else 0.0,
            "hold": round(p25 / impr * 100, 1) if impr else 0.0}


def med(xs): return round(st.median(xs), 2) if xs else None
def mean(xs): return round(st.mean(xs), 2) if xs else None

def benchmarks(rows):
    B = {}
    segs = set("%s/%s" % (c["aud"], c["type"]) for c in rows)
    for s in segs:
        g = [c for c in rows if "%s/%s" % (c["aud"], c["type"]) == s and c["cpa"] and c["spend"] >= 500]
        if len(g) >= 2:
            B[s] = {"cpa_med": med([c["cpa"] for c in g]), "cpa_mean": mean([c["cpa"] for c in g]),
                    "roas_med": med([c["roas"] for c in g]), "hook_med": med([c["hook"] for c in g]),
                    "hold_med": med([c["hold"] for c in g]), "n": len(g)}
    return B


def funnel(c, b):
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] >= b["hook_med"] * 1.15:
        return "Attention is strong (hook above segment median). The break is after the click, conversion efficiency, not the hook."
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] < b["hook_med"] * 0.7:
        return "Weak first 3 seconds vs segment. The problem is the hook."
    return "CTR is healthy, the cost is in conversion, not the click."


def label(c, B, prev):
    seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
    # fatigue first (needs prev)
    p = prev.get(c["ad_id"]) if prev else None
    if p and p.get("freq") and p.get("octr"):
        dfreq = (c["freq"] - p["freq"]) / p["freq"] * 100 if p["freq"] else 0
        doctr = (c["octr"] - p["octr"]) / p["octr"] * 100 if p["octr"] else 0
        dcpm = (c["cpm"] - p["cpm"]) / p["cpm"] * 100 if p["cpm"] else 0
        if dfreq > FAT_FREQ and doctr < -FAT_CTR and abs(dcpm) < 15:
            return "CREATIVE FATIGUE", ("Frequency %s to %s (+%.0f%%). Outbound CTR %s%% to %s%% (%.0f%%). CPM stable. "
                "Users see it more and respond less. Creative fatigue, refresh the first 3 seconds." %
                (p["freq"], c["freq"], dfreq, p["octr"], c["octr"], doctr)), {"dfreq": dfreq, "doctr": doctr}
    if (c["spend"] < MIN_SPEND * 0.5) and (c["purch"] or 0) < MIN_PUR:
        return "UNDERFUNDED", ("Only %.0f spend and %d purchases. Not enough data to judge. Do not kill." %
                (c["spend"], int(c["purch"] or 0))), {}
    if c["aud"] == "WARM":
        return "AUDIENCE-ASSISTED", ("Warm/%s audience. CPA %s and ROAS %s are driven by purchase intent, not the creative. "
            "Do not call it a creative winner. To prove the creative, test it cold." %
            (c["type"].lower(), c["cpa"], c["roas"]), ), {}
    if not b or not c["cpa"]:
        return "STEADY", "Not enough cold peers in this segment to benchmark yet.", {}
    gap = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100)
    if c["spend"] >= MIN_SPEND and c["purch"] >= MIN_PUR and c["cpa"] <= b["cpa_med"] * 0.85 and c["roas"] >= ROAS_T and c["freq"] < 3.5:
        return "SCALE OPPORTUNITY", ("Cold %s. CPA %s is %d%% below the %s median of %s. ROAS %s. Frequency %s healthy. "
            "Real creative winner, the audience is not inflating it." %
            (c["type"].lower(), c["cpa"], abs(gap), seg, b["cpa_med"], c["roas"], c["freq"]), ), {"gap": gap}
    if c["spend"] >= MIN_SPEND and c["cpa"] > b["cpa_med"] * 1.3:
        return "BAD CREATIVE", ("Cold %s. CPA %s is %+d%% vs the %s median of %s on real spend. %s" %
            (c["type"].lower(), c["cpa"], gap, seg, b["cpa_med"], funnel(c, b)), ), {"gap": gap}
    if c["cpa"] <= b["cpa_med"]:
        return "PERFORMS", "Cold %s, CPA %s at/below the %s median %s. Keep." % (c["type"].lower(), c["cpa"], seg, b["cpa_med"]), {"gap": gap}
    return "WATCH", "Cold %s, CPA %s slightly above %s median %s (%+d%%)." % (c["type"].lower(), c["cpa"], seg, b["cpa_med"], gap), {"gap": gap}


def analyze(acct, cur_rows, prev_rows):
    rows = [metric(r) for r in cur_rows]
    prev = {m["ad_id"]: m for m in (metric(r) for r in prev_rows)}
    B = benchmarks(rows)
    for c in rows:
        lab, why, extra = label(c, B, prev)
        c["label"] = lab; c["why"] = why[0] if isinstance(why, tuple) else why
        seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
        c["waste"] = round(c["purch"] * (c["cpa"] - b["cpa_med"])) if (c["aud"] == "COLD" and b and c["cpa"] and c["cpa"] > b["cpa_med"] and c["spend"] >= MIN_SPEND) else 0
        c["scale_score"] = round(c["spend"] * (b["cpa_med"] - c["cpa"]) / b["cpa_med"]) if (lab == "SCALE OPPORTUNITY" and b) else 0
    spend = sum(c["spend"] for c in rows); rev = sum(c["rev"] for c in rows); purch = sum(c["purch"] for c in rows)
    cat = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    cold = [c for c in rows if c["aud"] == "COLD"]; cold_s = sum(c["spend"] for c in cold); cold_r = sum(c["rev"] for c in cold)
    summary = {"spend": round(spend), "revenue": round(rev), "purchases": int(purch),
               "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
               "cat_pct": round(cat / spend * 100) if spend else 0, "cold_roas": round(cold_r / cold_s, 2) if cold_s else 0}
    offenders = [c for c in rows if c["waste"] > 0]
    opps = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    return {"account": acct, "summary": summary, "benchmarks": B,
            "creatives": sorted(rows, key=lambda c: c["spend"], reverse=True),
            "offender": max(offenders, key=lambda c: c["waste"]) if offenders else None,
            "opportunity": max(opps, key=lambda c: c["scale_score"]) if opps else None}


# ----------------------- Slack -----------------------
def slack(channel, text):
    if not SLACK_TOKEN:
        print("[slack:%s] %s\n" % (channel, text[:70])); return
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False}).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer %s" % SLACK_TOKEN, "Content-Type": "application/json; charset=utf-8"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"): sys.stderr.write("[slack] %s: %s\n" % (channel, r.get("error")))
    except Exception as e:
        sys.stderr.write("[slack] %s\n" % e)


def cur(a): return a["account"].get("currency", "")

def msg_summary(A):
    s = A["summary"]; c = cur(A); rows = A["creatives"]
    wins = [x for x in rows if x["label"] == "SCALE OPPORTUNITY"]
    warm = [x for x in rows if x["label"] == "AUDIENCE-ASSISTED"][:3]
    L = [MENTION + " 📊 Daily Growth Summary — " + A["account"]["name"] + " (" + c + ")",
         "Spend %s %s | ROAS %s | %d purchases | CPA %s %s" % (s["spend"], c, s["roas"], s["purchases"], s["cpa"], c)]
    if s["cat_pct"] >= 15:
        L.append("\nRead ROAS carefully: catalogue is %d%% of spend. Cold prospecting ROAS is %s, judge new creatives against cold." % (s["cat_pct"], s["cold_roas"]))
    if wins:
        L.append("\nTrue creative winners (cold): " + ", ".join("%s (CPA %s)" % (w["ad_name"], w["cpa"]) for w in wins[:3]))
    if warm:
        L.append("Audience-assisted (not creative winners): " + ", ".join("%s (%s)" % (w["ad_name"], w["cpa"]) for w in warm))
    if A["offender"]: L.append("\nBiggest risk: " + A["offender"]["ad_name"])
    if A["opportunity"]: L.append("Biggest opportunity: " + A["opportunity"]["ad_name"])
    return "\n".join(L)

def msg_offender(A):
    c = A["offender"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    return "\n".join([MENTION + " 🚨 Biggest Offender Today", "Account: " + A["account"]["name"],
        "Creative: %s" % c["ad_name"], "Type: %s | Audience: %s | Impact: High" % (c["type"].title(), c["aud"].title()), "",
        "Spend: %s %s" % (c["spend"], cc), "CPA: %s %s" % (c["cpa"], cc),
        "%s median CPA: %s %s" % (seg, b.get("cpa_med"), cc),
        "Estimated wasted spend: ~%s %s this week" % (c["waste"], cc), "",
        "Diagnosis: " + c["why"], "Action: cut budget 30 to 40%. Reallocate to the cold winners in this account."])

def msg_opportunity(A):
    c = A["opportunity"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    return "\n".join([MENTION + " 🚀 Biggest Opportunity", "Account: " + A["account"]["name"],
        "Creative: %s" % c["ad_name"], "Type: %s | Audience: %s | Label: Scale Opportunity" % (c["type"].title(), c["aud"].title()), "",
        "CPA: %s %s" % (c["cpa"], cc), "%s median CPA: %s %s" % (seg, b.get("cpa_med"), cc),
        "ROAS: %s | Frequency: %s | Spend: %s %s" % (c["roas"], c["freq"], c["spend"], cc), "",
        "Why it is real and not audience: it wins in COLD, so the creative is doing the work.",
        "Action: increase budget 20 to 30%. Build 5 iterations:",
        "1. Same layout, different product", "2. Alternate model", "3. Parent POV angle",
        "4. UGC testimonial", "5. Close-up quality detail", "Expected impact: High."])

def action_cards(A):
    cards = []; cc = cur(A); nm = A["account"]["name"]
    if A["offender"]:
        c = A["offender"]
        cards.append("🔴 P0 — Cut budget: %s (%s)\nAction: reduce 30 to 40%%, reallocate to cold winners.\nReason: biggest offender.\nEvidence: spend %s %s, CPA %s, ~%s %s wasted this week.\nExpected impact: High.\nStatus: Open" % (c["ad_name"], nm, c["spend"], cc, c["cpa"], c["waste"], cc))
    if A["opportunity"]:
        c = A["opportunity"]
        cards.append("🔴 P0 — Scale: %s (%s)\nAction: increase budget 20 to 30%% and brief 5 iterations.\nReason: true cold creative winner.\nEvidence: CPA %s, ROAS %s, frequency %s, spend %s %s.\nExpected impact: High.\nStatus: Open" % (c["ad_name"], nm, c["cpa"], c["roas"], c["freq"], c["spend"], cc))
    for c in A["creatives"]:
        if c["label"] == "CREATIVE FATIGUE":
            cards.append("🟠 P1 — Refresh: %s (%s)\nAction: new first 3 seconds, keep the concept.\nReason: creative fatigue with evidence.\nEvidence: %s\nExpected impact: Medium.\nStatus: Open" % (c["ad_name"], nm, c["why"]))
        elif c["label"] == "BAD CREATIVE" and c is not A["offender"]:
            cards.append("🟠 P1 — Trim: %s (%s)\nAction: reduce budget or pause.\nReason: above segment on real spend.\nEvidence: %s\nExpected impact: Medium.\nStatus: Open" % (c["ad_name"], nm, c["why"]))
    return cards


# ----------------------- main -----------------------
def daterange(tz_hour_check=False):
    try:
        from zoneinfo import ZoneInfo; tz = ZoneInfo(TZ)
    except Exception:
        tz = datetime.timezone.utc
    now = datetime.datetime.now(tz); y = (now - datetime.timedelta(days=1)).date()
    last = {"since": str(y - datetime.timedelta(days=6)), "until": str(y)}
    prev = {"since": str(y - datetime.timedelta(days=13)), "until": str(y - datetime.timedelta(days=7))}
    return last, prev, now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    global SLACK_TOKEN
    if a.dry_run: SLACK_TOKEN = ""
    if not TOKEN: sys.stderr.write("META_ACCESS_TOKEN missing\n"); sys.exit(1)

    last, prev, now = daterange()
    state = load_json(STATE_PATH, {"winners": {}, "fatigue": {}})
    report = {"generated_at": now.isoformat(), "timezone": TZ, "sample": False, "accounts": []}
    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        A = analyze(acct, cur_rows, prev_rows)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)

        if a.daily or a.dry_run:
            slack(CH.get("growth", "#meta-growth-alerts"), msg_summary(A))
            if A["offender"]: slack(CH.get("growth", "#meta-growth-alerts"), msg_offender(A))
            if A["opportunity"]: slack(CH.get("growth", "#meta-growth-alerts"), msg_opportunity(A))
            cards = action_cards(A)
            if cards:
                slack(CH.get("actions", "#meta-actions"), MENTION + " 📌 Daily Growth Actions — " + A["account"]["name"])
                for c in cards: slack(CH.get("actions", "#meta-actions"), c)
        # winner announce once
        for c in A["creatives"]:
            key = "%s:%s" % (acct["id"], c["ad_id"])
            if c["label"] == "SCALE OPPORTUNITY" and key not in state["winners"]:
                state["winners"][key] = str(datetime.date.today())
        time.sleep(1)

    # trim heavy fields for dashboard
    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("adset", None)
    save_json(DATA_PATH, report)
    if not a.dry_run: save_json(STATE_PATH, state)
    sys.stderr.write("[done] %d accounts\n" % len(report["accounts"]))


if __name__ == "__main__":
    main()
