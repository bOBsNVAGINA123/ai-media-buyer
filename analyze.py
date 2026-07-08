#!/usr/bin/env python3
"""
AI MEDIA BUYER v2.1  |  Senior Meta Ads Growth Operator.

Runs on GitHub Actions (free cron) with ONLY a Meta token + Slack bot token.
No Claude, no laptop. GitHub runs it every morning.

Posts to 4 channels:
  #meta-growth-alerts  daily summary + biggest offender + biggest opportunity
  #meta-actions        P0/P1/P2 task board
  #meta-anomalies      week-over-week anomaly detector (ROAS/CPA/spend/frequency)
  #meta-3day-pulse     fast 3-day rolling read

Every message states the time window and the run cadence, shows hook / hold /
ROAS / CPA, and the exact % better or worse vs the segment or prior period.

House style: line break after every period, no em dashes, CTR = Outbound CTR.
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
AN_ROAS = TH.get("anomaly_roas_drop", 25)
AN_CPA = TH.get("anomaly_cpa_spike", 30)
AN_SPEND = TH.get("anomaly_spend_swing", 40)

WARM_KW = ["retarget", " rt ", "rt_", "catalog", "dpa", "promocode", "promo code",
           "didn't purchase", "didnt purchase", "back2cart", "atc ", "atc_", "zombie",
           "existing", "evergreen", "ever green", "abandon", "viewed", "add to cart", "savewith"]
CAT_KW = ["catalog", "dpa"]

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def money(v):
    return "{:,.0f}".format(v) if v is not None else "n/a"
def pct(new, old):
    if not old: return None
    return (new - old) / old * 100.0
def sp(x):  # signed percent string
    return "n/a" if x is None else ("+" if x >= 0 else "") + "{:.0f}%".format(x)
def when(tz):
    try:
        from zoneinfo import ZoneInfo; z = ZoneInfo(tz)
    except Exception:
        z = datetime.timezone.utc
    now = datetime.datetime.now(z); y = (now - datetime.timedelta(days=1)).date()
    return now, y
CADENCE = "_Window: Last 7 Days vs Previous 7 Days. Runs automatically every morning at 9 AM Cairo._"


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
    return {"ad_id": r.get("ad_id"), "ad_name": name, "campaign": camp,
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
    for s in set("%s/%s" % (c["aud"], c["type"]) for c in rows):
        g = [c for c in rows if "%s/%s" % (c["aud"], c["type"]) == s and c["cpa"] and c["spend"] >= 500]
        if len(g) >= 2:
            B[s] = {"cpa_med": med([c["cpa"] for c in g]), "cpa_mean": mean([c["cpa"] for c in g]),
                    "roas_med": med([c["roas"] for c in g]), "hook_med": med([c["hook"] for c in g]),
                    "hold_med": med([c["hold"] for c in g]), "n": len(g)}
    return B

def funnel(c, b):
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] >= b["hook_med"] * 1.15:
        return "Attention is strong, hook is above the segment median. The break is after the click, conversion efficiency, not the hook."
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] < b["hook_med"] * 0.7:
        return "Weak first 3 seconds versus the segment. The problem is the hook."
    return "CTR is healthy, the cost sits in conversion, not the click."

def label(c, B, prev):
    seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
    p = prev.get(c["ad_id"]) if prev else None
    c["d_roas"] = pct(c["roas"], p["roas"]) if p else None
    c["d_cpa"] = pct(c["cpa"], p["cpa"]) if (p and p.get("cpa") and c["cpa"]) else None
    c["d_freq"] = pct(c["freq"], p["freq"]) if p else None
    c["d_octr"] = pct(c["octr"], p["octr"]) if p else None
    c["d_spend"] = pct(c["spend"], p["spend"]) if p else None
    c["prev"] = {k: p.get(k) for k in ("cpa", "roas", "freq", "octr", "spend")} if p else None
    if p and p.get("freq") and p.get("octr"):
        if c["d_freq"] is not None and c["d_freq"] > FAT_FREQ and c["d_octr"] is not None and c["d_octr"] < -FAT_CTR:
            return "CREATIVE FATIGUE", ("Frequency %s to %s (%s). Outbound CTR %s%% to %s%% (%s). "
                "Seen more, responding less. Refresh the first 3 seconds." %
                (p["freq"], c["freq"], sp(c["d_freq"]), p["octr"], c["octr"], sp(c["d_octr"])))
    if (c["spend"] < MIN_SPEND * 0.5) and (c["purch"] or 0) < MIN_PUR:
        return "UNDERFUNDED", "Only %s spend and %d purchases. Not enough data to judge. Do not kill." % (money(c["spend"]), int(c["purch"] or 0))
    if c["aud"] == "WARM":
        return "AUDIENCE-ASSISTED", ("Warm/%s audience. CPA %s and ROAS %s come from purchase intent, not the creative. Prove it cold before scaling." %
            (c["type"].lower(), money(c["cpa"]), c["roas"]))
    if not b or not c["cpa"]:
        return "STEADY", "Not enough cold peers in this segment to benchmark yet."
    gap = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100)
    if c["spend"] >= MIN_SPEND and c["purch"] >= MIN_PUR and c["cpa"] <= b["cpa_med"] * 0.85 and c["roas"] >= ROAS_T and c["freq"] < 3.5:
        return "SCALE OPPORTUNITY", ("Cold %s. CPA %s is %d%% below the %s median of %s. ROAS %s. Frequency %s. Real creative winner." %
            (c["type"].lower(), money(c["cpa"]), abs(gap), seg, money(b["cpa_med"]), c["roas"], c["freq"]))
    if c["spend"] >= MIN_SPEND and c["cpa"] > b["cpa_med"] * 1.3:
        return "BAD CREATIVE", ("Cold %s. CPA %s is %s versus the %s median of %s. %s" %
            (c["type"].lower(), money(c["cpa"]), sp(gap), seg, money(b["cpa_med"]), funnel(c, b)))
    if c["cpa"] <= b["cpa_med"]:
        return "PERFORMS", "Cold %s, CPA %s at or below the %s median %s. Keep." % (c["type"].lower(), money(c["cpa"]), seg, money(b["cpa_med"]))
    return "WATCH", "Cold %s, CPA %s is %s versus the %s median %s." % (c["type"].lower(), money(c["cpa"]), sp(gap), seg, money(b["cpa_med"]))

def seg_gap(c, B):
    b = B.get("%s/%s" % (c["aud"], c["type"]))
    if not b or not c["cpa"]: return None, None
    return round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100), b

def analyze(acct, cur_rows, prev_rows):
    rows = [metric(r) for r in cur_rows]
    prev = {m["ad_id"]: m for m in (metric(r) for r in prev_rows)}
    B = benchmarks(rows)
    for c in rows:
        c["label"], c["why"] = label(c, B, prev)
        g, b = seg_gap(c, B)
        c["gap"] = g; c["seg_cpa_med"] = b["cpa_med"] if b else None
        c["waste"] = round(c["purch"] * (c["cpa"] - b["cpa_med"])) if (c["aud"] == "COLD" and b and c["cpa"] and c["cpa"] > b["cpa_med"] and c["spend"] >= MIN_SPEND) else 0
        c["scale_score"] = round(c["spend"] * (b["cpa_med"] - c["cpa"]) / b["cpa_med"]) if (c["label"] == "SCALE OPPORTUNITY" and b) else 0
    spend = sum(c["spend"] for c in rows); rev = sum(c["rev"] for c in rows); purch = sum(c["purch"] for c in rows)
    pspend = sum((c["prev"] or {}).get("spend", 0) for c in rows)
    cat = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    cold = [c for c in rows if c["aud"] == "COLD"]; cold_s = sum(c["spend"] for c in cold); cold_r = sum(c["rev"] for c in cold)
    summary = {"spend": round(spend), "revenue": round(rev), "purchases": int(purch),
               "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
               "cat_pct": round(cat / spend * 100) if spend else 0, "cold_roas": round(cold_r / cold_s, 2) if cold_s else 0,
               "d_spend": pct(spend, pspend)}
    offenders = [c for c in rows if c["waste"] > 0]
    opps = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    return {"account": acct, "summary": summary, "benchmarks": B,
            "creatives": sorted(rows, key=lambda c: c["spend"], reverse=True),
            "offender": max(offenders, key=lambda c: c["waste"]) if offenders else None,
            "opportunity": max(opps, key=lambda c: c["scale_score"]) if opps else None}


# ----------------------- Slack -----------------------
def slack(channel, text):
    if not channel: return
    if not SLACK_TOKEN:
        print("[slack:%s] %s\n" % (channel, text[:70])); return
    body = json.dumps({"channel": channel, "text": text, "unfurl_links": False, "mrkdwn": True}).encode()
    req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body,
        headers={"Authorization": "Bearer %s" % SLACK_TOKEN, "Content-Type": "application/json; charset=utf-8"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"): sys.stderr.write("[slack] %s: %s\n" % (channel, r.get("error")))
    except Exception as e:
        sys.stderr.write("[slack] %s\n" % e)

def cur(A): return A["account"].get("currency", "")
def vid(c): return c["type"] == "VIDEO"
def line(c, cc):
    """One readable creative line with the numbers that matter."""
    parts = ["*%s*" % c["ad_name"], "%s %s" % (c["aud"].title(), c["type"].title()),
             "CPA %s %s" % (money(c["cpa"]), cc)]
    if c.get("gap") is not None: parts.append("%s vs seg" % sp(c["gap"]))
    parts.append("ROAS %s" % c["roas"])
    if vid(c): parts.append("hook %s%% / hold %s%%" % (c["hook"], c["hold"]))
    parts.append("freq %s" % c["freq"])
    return "  •  ".join(parts)

def msg_summary(A):
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    wins = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    warm = sorted([c for c in rows if c["label"] == "AUDIENCE-ASSISTED"], key=lambda c: c["spend"], reverse=True)[:4]
    L = ["%s :dart: *Daily Growth Summary — %s (%s)*" % (MENTION, A["account"]["name"], cc),
         ":date: Last 7 Days vs Previous 7 Days", "",
         "*Account*",
         "• Spend: *%s %s* (%s vs prev)" % (money(s["spend"]), cc, sp(s["d_spend"])),
         "• Revenue: %s %s" % (money(s["revenue"]), cc),
         "• Blended ROAS: *%s*   |   Cold prospecting ROAS: *%s*" % (s["roas"], s["cold_roas"]),
         "• Purchases: %d   |   Blended CPA: %s %s" % (s["purchases"], money(s["cpa"]), cc)]
    if s["cat_pct"] >= 15:
        L += ["", ":warning: Catalogue is *%d%%* of spend and lifts blended ROAS. Judge new creatives against Cold ROAS %s, not blended." % (s["cat_pct"], s["cold_roas"])]
    if wins:
        L += ["", ":large_green_circle: *True creative winners (cold)*"] + ["• " + line(c, cc) for c in wins[:4]]
    if warm:
        L += ["", ":large_blue_circle: *Audience-assisted (retargeting / catalogue, NOT creative wins)*"] + \
             ["• *%s*  •  CPA %s %s  •  ROAS %s  •  freq %s  •  spend %s %s" %
              (c["ad_name"], money(c["cpa"]), cc, c["roas"], c["freq"], money(c["spend"]), cc) for c in warm]
    if A["offender"]: L += ["", ":rotating_light: Biggest risk: *%s*" % A["offender"]["ad_name"]]
    if A["opportunity"]: L += [":rocket: Biggest opportunity: *%s*" % A["opportunity"]["ad_name"]]
    L += ["", CADENCE]
    return "\n".join(L)

def msg_offender(A):
    c = A["offender"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    L = ["%s :rotating_light: *Biggest Offender — %s*" % (MENTION, A["account"]["name"]),
         ":date: Last 7 Days vs Previous 7 Days", "",
         "*%s*   (%s %s)" % (c["ad_name"], c["aud"].title(), c["type"].title()),
         "• Spend: *%s %s*" % (money(c["spend"]), cc),
         "• CPA: *%s %s*  →  *%s* vs %s median %s" % (money(c["cpa"]), cc, sp(c["gap"]), seg, money(b.get("cpa_med"))),
         "• ROAS: %s" % c["roas"]]
    if vid(c):
        L.append("• Hook: %s%% (median %s%%)   |   Hold: %s%% (median %s%%)" % (c["hook"], b.get("hook_med"), c["hold"], b.get("hold_med")))
    L += ["• Estimated wasted spend: *~%s %s this week*" % (money(c["waste"]), cc), "",
          ":brain: %s" % c["why"],
          ":white_check_mark: *Action:* cut budget 30 to 40%. Reallocate to the cold winners in this account.",
          "", CADENCE]
    return "\n".join(L)

def msg_opportunity(A):
    c = A["opportunity"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    L = ["%s :rocket: *Biggest Opportunity — %s*" % (MENTION, A["account"]["name"]),
         ":date: Last 7 Days vs Previous 7 Days", "",
         "*%s*   (%s %s, Scale Opportunity)" % (c["ad_name"], c["aud"].title(), c["type"].title()),
         "• CPA: *%s %s*  →  *%s* vs %s median %s" % (money(c["cpa"]), cc, sp(c["gap"]), seg, money(b.get("cpa_med"))),
         "• ROAS: *%s*   |   Frequency: %s   |   Spend: %s %s" % (c["roas"], c["freq"], money(c["spend"]), cc)]
    if vid(c):
        L.append("• Hook: %s%% (median %s%%)   |   Hold: %s%% (median %s%%)" % (c["hook"], b.get("hook_med"), c["hold"], b.get("hold_med")))
    L += ["", "It wins in a COLD audience, so the creative is doing the work, not warm intent.",
          ":white_check_mark: *Action:* increase budget 20 to 30% and build 5 iterations:",
          "1. Same layout, different product", "2. Alternate model", "3. Parent POV angle",
          "4. UGC testimonial", "5. Close-up quality detail", "", CADENCE]
    return "\n".join(L)

def action_cards(A):
    cards, cc, nm = [], cur(A), A["account"]["name"]
    if A["offender"]:
        c = A["offender"]
        cards.append(":red_circle: *P0 — Cut budget: %s* (%s)\n"
            "Action: reduce 30 to 40%%, reallocate to cold winners.\n"
            "Reason: biggest offender.\n"
            "Evidence: spend %s %s, CPA %s (%s vs seg median %s), ROAS %s, ~%s %s wasted this week.\n"
            "Expected impact: High.  Status: Open" %
            (c["ad_name"], nm, money(c["spend"]), cc, money(c["cpa"]), sp(c["gap"]), money(c["seg_cpa_med"]), c["roas"], money(c["waste"]), cc))
    if A["opportunity"]:
        c = A["opportunity"]; hh = " Hook %s%%/hold %s%%." % (c["hook"], c["hold"]) if vid(c) else ""
        cards.append(":red_circle: *P0 — Scale: %s* (%s)\n"
            "Action: increase budget 20 to 30%% and brief 5 iterations.\n"
            "Reason: true cold creative winner.\n"
            "Evidence: CPA %s (%s vs seg median %s), ROAS %s, freq %s, spend %s %s.%s\n"
            "Expected impact: High.  Status: Open" %
            (c["ad_name"], nm, money(c["cpa"]), sp(c["gap"]), money(c["seg_cpa_med"]), c["roas"], c["freq"], money(c["spend"]), cc, hh))
    for c in A["creatives"]:
        if c["label"] == "CREATIVE FATIGUE":
            cards.append(":large_orange_circle: *P1 — Refresh: %s* (%s)\n"
                "Action: new first 3 seconds, keep the concept.\nReason: creative fatigue with evidence.\n"
                "Evidence: %s\nExpected impact: Medium.  Status: Open" % (c["ad_name"], nm, c["why"]))
        elif c["label"] == "BAD CREATIVE" and c is not A["offender"]:
            cards.append(":large_orange_circle: *P1 — Trim: %s* (%s)\n"
                "Action: reduce budget or pause.\nReason: above segment on real spend.\n"
                "Evidence: CPA %s (%s vs seg), ROAS %s.\nExpected impact: Medium.  Status: Open" %
                (c["ad_name"], nm, money(c["cpa"]), sp(c["gap"]), c["roas"]))
    return cards

def anomalies(A):
    """Week over week anomaly detector at account and creative level."""
    s = A["summary"]; cc = cur(A); out = []
    # account level
    acc = []
    if s["d_spend"] is not None and abs(s["d_spend"]) >= AN_SPEND:
        acc.append("Spend swung *%s* week over week" % sp(s["d_spend"]))
    for c in A["creatives"]:
        if c["spend"] < MIN_SPEND / 2: continue
        flags = []
        if c["d_roas"] is not None and c["d_roas"] <= -AN_ROAS:
            flags.append("ROAS %s to %s (*%s*)" % (c["prev"]["roas"], c["roas"], sp(c["d_roas"])))
        if c["d_cpa"] is not None and c["d_cpa"] >= AN_CPA:
            flags.append("CPA %s to %s (*%s*)" % (money(c["prev"]["cpa"]), money(c["cpa"]), sp(c["d_cpa"])))
        if c["d_freq"] is not None and c["d_freq"] >= FAT_FREQ:
            flags.append("Frequency %s to %s (*%s*)" % (c["prev"]["freq"], c["freq"], sp(c["d_freq"])))
        if flags:
            out.append("• *%s* (%s %s, spend %s %s)\n    %s" %
                       (c["ad_name"], c["aud"].title(), c["type"].title(), money(c["spend"]), cc, "\n    ".join(flags)))
    if not acc and not out: return None
    head = ["%s :ocean: *Anomaly Detector — %s*" % (MENTION, A["account"]["name"]),
            ":date: Last 7 Days vs Previous 7 Days", ""]
    if acc: head += acc + [""]
    if out: head += ["*Creatives that moved sharply*"] + out
    else: head += ["No creative-level anomalies. Account-level swing only."]
    head += ["", CADENCE]
    return "\n".join(head)

def pulse_3day(acct, rows):
    cc = acct.get("currency", "")
    spend = sum(f(r.get("spend")) for r in rows)
    if spend <= 0: return None
    ms = [metric(r) for r in rows]
    rev = sum(c["rev"] for c in ms); purch = sum(c["purch"] for c in ms)
    top = max(ms, key=lambda c: c["spend"])
    return "\n".join([
        "%s :zap: *3-Day Pulse — %s (%s)*" % (MENTION, acct["name"], cc),
        ":date: Last 3 Days", "",
        "• Spend: *%s %s*" % (money(spend), cc),
        "• Revenue: %s %s" % (money(rev), cc),
        "• ROAS: *%s*   |   Purchases: %d   |   CPA: %s %s" % (round(rev / spend, 2), int(purch), money(spend / purch if purch else None), cc),
        "• Top spender: *%s* (CPA %s %s, ROAS %s)" % (top["ad_name"], money(top["cpa"]), cc, top["roas"]),
        "", "_Fast 3-day read. Runs every morning at 9 AM Cairo._"])


# ----------------------- main -----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    global SLACK_TOKEN
    if a.dry_run: SLACK_TOKEN = ""
    if not TOKEN: sys.stderr.write("META_ACCESS_TOKEN missing\n"); sys.exit(1)

    now, y = when(TZ)
    last = {"since": str(y - datetime.timedelta(days=6)), "until": str(y)}
    prev = {"since": str(y - datetime.timedelta(days=13)), "until": str(y - datetime.timedelta(days=7))}
    last3 = {"since": str(y - datetime.timedelta(days=2)), "until": str(y)}
    report = {"generated_at": now.isoformat(), "timezone": TZ, "sample": False, "accounts": []}

    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        A = analyze(acct, cur_rows, prev_rows)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)
        if a.daily or a.dry_run:
            slack(CH.get("growth"), msg_summary(A))
            if A["offender"]: slack(CH.get("growth"), msg_offender(A))
            if A["opportunity"]: slack(CH.get("growth"), msg_opportunity(A))
            cards = action_cards(A)
            if cards:
                slack(CH.get("actions"), "%s :pushpin: *Daily Growth Actions — %s*  (ranked by impact, evidence attached)" % (MENTION, acct["name"]))
                for c in cards: slack(CH.get("actions"), c)
            an = anomalies(A)
            if an: slack(CH.get("anomalies"), an)
            p3 = pulse_3day(acct, get_insights(acct["id"], last3))
            if p3: slack(CH.get("three_day"), p3)
        time.sleep(1)

    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("prev", None)
    save_json(DATA_PATH, report)
    sys.stderr.write("[done] %d accounts\n" % len(report["accounts"]))

if __name__ == "__main__":
    main()
