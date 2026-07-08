#!/usr/bin/env python3
"""
AI MEDIA BUYER v3  |  Senior Meta Ads Growth Operator.

Runs on GitHub Actions (free cron) with ONLY a Meta token + Slack bot token.
No Claude, no laptop. GitHub runs it every morning.

Each account posts to its OWN Slack channel (routed by name):
  #meta-ourkids, #meta-playmore, else a default channel.

Rules baked in:
- A winner must beat cost AND value. SCALE = CPA well below its group median
  AND ROAS at least the account cold-prospecting ROAS. A great CPA with a
  below-average ROAS is flagged EFFICIENT-LOW-ROAS, never "scale".
- Never a lone metric. Every number is shown against its benchmark, the account,
  or last week.
- Always name the exact date window.
- Every action names WHERE to move budget and WHY.
- 3-day pulse names the 80/20 best and worst adsets and ads.

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
AN_SPEND = TH.get("anomaly_spend_swing", 40)

WARM_KW = ["retarget", " rt ", "rt_", "catalog", "dpa", "promocode", "promo code",
           "didn't purchase", "didnt purchase", "back2cart", "atc ", "atc_", "zombie",
           "existing", "evergreen", "ever green", "abandon", "viewed", "add to cart", "savewith"]
CAT_KW = ["catalog", "dpa"]
DIV = "──────────────────────"

def channel_for(name):
    n = (name or "").lower()
    if "playmore" in n: return CH.get("playmore") or CH.get("default")
    if "ourkids" in n or "our kids" in n or "kids" in n: return CH.get("ourkids") or CH.get("default")
    return CH.get("default")

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
    return "*%d%% better*" % abs(gap) if gap <= 0 else "*%d%% worse*" % gap
def fmt_day(d): return d.strftime("%b ") + str(d.day)


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

AD_FIELDS = ",".join(["ad_id", "ad_name", "campaign_name", "adset_name", "objective",
    "spend", "impressions", "reach", "frequency", "cpm", "ctr", "inline_link_click_ctr",
    "outbound_clicks_ctr", "actions", "action_values", "video_play_actions",
    "video_p25_watched_actions", "video_p100_watched_actions", "video_thruplay_watched_actions"])
LITE_FIELDS = "spend,purchase_roas,actions,action_values"

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

def benchmarks(rows):
    B = {}
    for s in set("%s/%s" % (c["aud"], c["type"]) for c in rows):
        g = [c for c in rows if "%s/%s" % (c["aud"], c["type"]) == s and c["cpa"] and c["spend"] >= 500]
        if len(g) >= 2:
            B[s] = {"cpa_med": med([c["cpa"] for c in g]), "roas_med": med([c["roas"] for c in g]),
                    "hook_med": med([c["hook"] for c in g]), "hold_med": med([c["hold"] for c in g]), "n": len(g)}
    return B

def funnel(c, b):
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] >= b["hook_med"] * 1.15:
        return "Attention is strong (hook above the group median), so the break is after the click, in conversion, not the creative."
    if c["type"] == "VIDEO" and b and b.get("hook_med") and c["hook"] < b["hook_med"] * 0.7:
        return "The first 3 seconds are weak versus the group, so the hook is the problem."
    return "Click metrics are healthy, so the cost sits in conversion, not the click."

def label(c, B, prev, cold_roas):
    seg = "%s/%s" % (c["aud"], c["type"]); b = B.get(seg)
    p = prev.get(c["ad_id"]) if prev else None
    c["d_roas"] = pct(c["roas"], p["roas"]) if p else None
    c["d_cpa"] = pct(c["cpa"], p["cpa"]) if (p and p.get("cpa") and c["cpa"]) else None
    c["d_freq"] = pct(c["freq"], p["freq"]) if p else None
    c["d_octr"] = pct(c["octr"], p["octr"]) if p else None
    c["prev"] = {k: p.get(k) for k in ("cpa", "roas", "freq", "octr", "spend")} if p else None
    if p and p.get("freq") and p.get("octr") and c["d_freq"] is not None and c["d_freq"] > FAT_FREQ \
       and c["d_octr"] is not None and c["d_octr"] < -FAT_CTR:
        return "CREATIVE FATIGUE", ("Frequency rose from %s to %s (%s) while Outbound CTR fell from %s%% to %s%% (%s), "
            "so the audience is seeing it more and responding less. Refresh the first 3 seconds." %
            (p["freq"], c["freq"], sp(c["d_freq"]), p["octr"], c["octr"], sp(c["d_octr"])))
    if (c["spend"] < MIN_SPEND * 0.5) and (c["purch"] or 0) < MIN_PUR:
        return "UNDERFUNDED", ("Only %s spend and %d purchases so far, which is not enough to judge. Do not kill, give it budget or time." %
            (money(c["spend"]), int(c["purch"] or 0)))
    if c["aud"] == "WARM":
        return "AUDIENCE-ASSISTED", ("Warm/%s audience, so CPA %s and ROAS %s come from purchase intent, not the creative. "
            "Judge it as audience, and prove the creative in a cold test before scaling." % (c["type"].lower(), money(c["cpa"]), c["roas"]))
    if not b or not c["cpa"]:
        return "STEADY", "Not enough cold peers in this group yet to benchmark it fairly."
    gap = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100)
    strong_cpa = c["cpa"] <= b["cpa_med"] * 0.85
    beats_value = c["roas"] >= max(ROAS_T, cold_roas * 0.95)
    if c["spend"] >= MIN_SPEND and c["purch"] >= MIN_PUR and strong_cpa and beats_value and c["freq"] < 3.5:
        return "SCALE OPPORTUNITY", ("CPA %s is %d%% below the %s median of %s, and ROAS %s matches or beats the account cold ROAS of %s, "
            "so this wins on both cost and value in a cold audience. Scale it." %
            (money(c["cpa"]), abs(gap), seg, money(b["cpa_med"]), c["roas"], cold_roas))
    if c["spend"] >= MIN_SPEND and strong_cpa and not beats_value:
        return "EFFICIENT-LOW-ROAS", ("CPA %s is %d%% below the %s median, but ROAS %s is under the account cold ROAS of %s, likely a low AOV of %s. "
            "Cheap to acquire but low value, so test the same hook on a higher-price hero product before scaling." %
            (money(c["cpa"]), abs(gap), seg, c["roas"], cold_roas, money(c["aov"])))
    if c["spend"] >= MIN_SPEND and c["cpa"] > b["cpa_med"] * 1.3:
        return "BAD CREATIVE", ("CPA %s is %s than the %s median of %s on real spend, and ROAS is %s. %s" %
            (money(c["cpa"]), bw(gap), seg, money(b["cpa_med"]), c["roas"], funnel(c, b)))
    if c["cpa"] <= b["cpa_med"]:
        return "PERFORMS", ("CPA %s sits at or below the %s median of %s and ROAS is %s, so keep it running and watch frequency %s." %
            (money(c["cpa"]), seg, money(b["cpa_med"]), c["roas"], c["freq"]))
    return "WATCH", ("CPA %s is %s the %s median of %s and ROAS is %s, so hold budget and watch it." %
            (money(c["cpa"]), bw(gap), seg, money(b["cpa_med"]), c["roas"]))

def analyze(acct, cur_rows, prev_rows):
    rows = [metric(r) for r in cur_rows]
    prev = {m["ad_id"]: m for m in (metric(r) for r in prev_rows)}
    B = benchmarks(rows)
    cold = [c for c in rows if c["aud"] == "COLD"]
    cold_s = sum(c["spend"] for c in cold); cold_r = sum(c["rev"] for c in cold)
    cold_roas = round(cold_r / cold_s, 2) if cold_s else 0
    for c in rows:
        c["label"], c["why"] = label(c, B, prev, cold_roas)
        b = B.get("%s/%s" % (c["aud"], c["type"]))
        c["gap"] = round((c["cpa"] - b["cpa_med"]) / b["cpa_med"] * 100) if (b and c["cpa"]) else None
        c["seg_cpa_med"] = b["cpa_med"] if b else None
        c["waste"] = round(c["purch"] * (c["cpa"] - b["cpa_med"])) if (c["aud"] == "COLD" and b and c["cpa"] and c["cpa"] > b["cpa_med"] and c["spend"] >= MIN_SPEND) else 0
        c["scale_score"] = round(c["spend"] * (b["cpa_med"] - c["cpa"]) / b["cpa_med"]) if (c["label"] == "SCALE OPPORTUNITY" and b) else 0
    spend = sum(c["spend"] for c in rows); rev = sum(c["rev"] for c in rows); purch = sum(c["purch"] for c in rows)
    pspend = sum((c["prev"] or {}).get("spend", 0) for c in rows)
    cat = sum(c["spend"] for c in rows if c["type"] == "CATALOGUE")
    summary = {"spend": round(spend), "revenue": round(rev), "purchases": int(purch),
               "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None,
               "cat_pct": round(cat / spend * 100) if spend else 0, "cold_roas": cold_roas, "d_spend": pct(spend, pspend)}
    winners = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    offenders = [c for c in rows if c["waste"] > 0]
    best = max(winners, key=lambda c: c["scale_score"]) if winners else None
    if not best:  # fallback allocation target: best cold ROAS on real spend
        cand = [c for c in rows if c["aud"] == "COLD" and c["spend"] >= MIN_SPEND and c["roas"]]
        best = max(cand, key=lambda c: c["roas"]) if cand else None
    return {"account": acct, "summary": summary, "benchmarks": B,
            "creatives": sorted(rows, key=lambda c: c["spend"], reverse=True),
            "offender": max(offenders, key=lambda c: c["waste"]) if offenders else None,
            "opportunity": best if winners else None, "best_target": best}


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

DATES = {"label": "", "p_label": ""}
def cur(A): return A["account"].get("currency", "")
def vid(c): return c["type"] == "VIDEO"
def target_str(A):
    t = A.get("best_target")
    if not t: return "the lowest-CPA cold winner in this account"
    cc = cur(A)
    return "*%s* (CPA %s %s, ROAS %s)" % (t["ad_name"], money(t["cpa"]), cc, t["roas"])
def trend(c, cc):
    p = c.get("prev"); bits = []
    if not p: return None
    if c["d_cpa"] is not None and p.get("cpa"): bits.append("CPA %s (was %s %s last week)" % (sp(c["d_cpa"]), money(p["cpa"]), cc))
    if c["d_roas"] is not None and p.get("roas"): bits.append("ROAS %s (was %s last week)" % (sp(c["d_roas"]), p["roas"]))
    return "   ".join(bits) if bits else None

def win_line(c, cc):
    out = ["• *%s*" % c["ad_name"],
           "      CPA %s %s  (%s vs its group median %s %s)" % (money(c["cpa"]), cc, bw(c["gap"]), money(c["seg_cpa_med"]), cc),
           "      ROAS %s  ·  freq %s  ·  spend %s %s" % (c["roas"], c["freq"], money(c["spend"]), cc)]
    if vid(c): out.append("      hook %s%%  ·  hold %s%%" % (c["hook"], c["hold"]))
    return "\n".join(out)

def msg_summary(A):
    s = A["summary"]; cc = cur(A); rows = A["creatives"]
    wins = [c for c in rows if c["label"] == "SCALE OPPORTUNITY"]
    warm = sorted([c for c in rows if c["label"] == "AUDIENCE-ASSISTED"], key=lambda c: c["spend"], reverse=True)[:4]
    L = ["%s   :dart:  *DAILY GROWTH SUMMARY*" % MENTION,
         "*%s (%s)*" % (A["account"]["name"], cc),
         ":date:  %s to %s   (vs previous week %s to %s)" % (DATES["label"][0], DATES["label"][1], DATES["p_label"][0], DATES["p_label"][1]),
         "", DIV, "", "*ACCOUNT HEALTH*", "",
         "• Spend:  *%s %s*   (%s vs last week)" % (money(s["spend"]), cc, sp(s["d_spend"])),
         "• Revenue:  %s %s   at ROAS *%s*" % (money(s["revenue"]), cc, s["roas"]),
         "• Purchases:  %d   at a blended CPA of %s %s" % (s["purchases"], money(s["cpa"]), cc),
         "• Cold prospecting ROAS:  *%s*   (this is the bar new creatives must beat)" % s["cold_roas"]]
    if s["cat_pct"] >= 15:
        L += ["", ":warning:  Catalogue is *%d%%* of spend and inflates the blended ROAS of %s. Judge new creatives against the Cold ROAS of *%s*, not the blended number." % (s["cat_pct"], s["roas"], s["cold_roas"])]
    L += ["", DIV, "", ":large_green_circle:  *TRUE CREATIVE WINNERS (cold, beat cost AND value)*", ""]
    L += [win_line(c, cc) + "\n" for c in wins[:4]] if wins else ["_None cleared both the cost and the value bar this week._\n"]
    if warm:
        L += [DIV, "", ":large_blue_circle:  *AUDIENCE-ASSISTED (retargeting / catalogue, NOT creative wins)*", ""]
        L += ["• *%s*\n      CPA %s %s  ·  ROAS %s  ·  freq %s  ·  spend %s %s\n" %
              (c["ad_name"], money(c["cpa"]), cc, c["roas"], c["freq"], money(c["spend"]), cc) for c in warm]
    L += [DIV, ""]
    if A["offender"]:
        o = A["offender"]
        L.append(":rotating_light:  *Biggest risk:*  %s  (CPA %s vs its group, ~%s %s wasted this week)" % (o["ad_name"], sp(o["gap"]), money(o["waste"]), cc))
    if A["opportunity"]:
        op = A["opportunity"]
        L.append(":rocket:  *Biggest opportunity:*  %s  (CPA %s vs its group, ROAS %s beats cold %s)" % (op["ad_name"], sp(op["gap"]), op["roas"], s["cold_roas"]))
    L += ["", CADENCE()]
    return "\n".join(L)

def msg_offender(A):
    c = A["offender"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    L = ["%s   :rotating_light:  *BIGGEST OFFENDER*  —  %s" % (MENTION, A["account"]["name"]),
         ":date:  %s to %s   (vs previous week)" % (DATES["label"][0], DATES["label"][1]),
         "", DIV, "",
         "*Creative:*  %s" % c["ad_name"],
         "*Type:*  %s %s" % (c["aud"].title(), c["type"].title()),
         "",
         "• *CPA:*  %s %s   →   %s than the %s median of %s %s" % (money(c["cpa"]), cc, bw(c["gap"]), seg, money(b.get("cpa_med")), cc),
         "• *ROAS:*  %s   (account cold ROAS is %s, so this is below the bar)" % (c["roas"], A["summary"]["cold_roas"]),
         "• *Spend:*  %s %s   which is real money, not a small test" % (money(c["spend"]), cc)]
    t = trend(c, cc)
    if t: L.append("• *Trend vs last week:*  %s" % t)
    if vid(c):
        L.append("• *Hook:*  %s%%   (group median %s%%)" % (c["hook"], b.get("hook_med")))
        L.append("• *Hold:*  %s%%   (group median %s%%)" % (c["hold"], b.get("hold_med")))
    L += ["• *Wasted this week:*  *~%s %s*   (spend times the gap to the group median)" % (money(c["waste"]), cc),
          "", DIV, "",
          ":brain:  *Why it is the offender:*  %s" % c["why"],
          "",
          ":white_check_mark:  *Action:*  cut its budget 30 to 40%% and move that spend to %s, because it hits the same cold audience at a lower cost and a higher return." % target_str(A),
          "", CADENCE()]
    return "\n".join(L)

def msg_opportunity(A):
    c = A["opportunity"]; cc = cur(A); seg = "%s/%s" % (c["aud"], c["type"]); b = A["benchmarks"].get(seg, {})
    L = ["%s   :rocket:  *BIGGEST OPPORTUNITY*  —  %s" % (MENTION, A["account"]["name"]),
         ":date:  %s to %s   (vs previous week)" % (DATES["label"][0], DATES["label"][1]),
         "", DIV, "",
         "*Creative:*  %s" % c["ad_name"],
         "*Type:*  %s %s" % (c["aud"].title(), c["type"].title()),
         "",
         "• *CPA:*  %s %s   →   %s than the %s median of %s %s" % (money(c["cpa"]), cc, bw(c["gap"]), seg, money(b.get("cpa_med")), cc),
         "• *ROAS:*  %s   (beats the account cold ROAS of %s, so it wins on value too)" % (c["roas"], A["summary"]["cold_roas"]),
         "• *Frequency:*  %s   (healthy, room to scale)" % c["freq"],
         "• *Spend:*  %s %s   (enough to trust the read)" % (money(c["spend"]), cc)]
    t = trend(c, cc)
    if t: L.append("• *Trend vs last week:*  %s" % t)
    if vid(c):
        L.append("• *Hook:*  %s%%   (group median %s%%)" % (c["hook"], b.get("hook_med")))
        L.append("• *Hold:*  %s%%   (group median %s%%)" % (c["hold"], b.get("hold_med")))
    L += ["", DIV, "",
          ":white_check_mark:  *Action:*  increase its budget 20 to 30%, and take the extra spend from the biggest offender named above.",
          "Build 5 iterations while it is hot:",
          "     1. Same layout, different product",
          "     2. Alternate model",
          "     3. Parent POV angle",
          "     4. UGC testimonial",
          "     5. Close-up quality detail",
          "", CADENCE()]
    return "\n".join(L)

def msg_actions(A):
    cc = cur(A); nm = A["account"]["name"]; cards = []
    if A["offender"]:
        c = A["offender"]
        cards.append(":red_circle:  *P0 — Cut and reallocate:*  %s\n"
            "   • *Action:*  reduce budget 30 to 40%%, move it to %s.\n"
            "   • *Reason:*  biggest offender, CPA %s %s is %s than its group median %s %s.\n"
            "   • *Evidence:*  ROAS %s, spend %s %s, ~%s %s wasted this week.\n"
            "   • *Impact:*  High.   *Status:*  Open" %
            (c["ad_name"], target_str(A), money(c["cpa"]), cc, bw(c["gap"]), money(c["seg_cpa_med"]), cc, c["roas"], money(c["spend"]), cc, money(c["waste"]), cc))
    if A["opportunity"]:
        c = A["opportunity"]
        cards.append(":red_circle:  *P0 — Scale:*  %s\n"
            "   • *Action:*  increase budget 20 to 30%%, brief 5 iterations.\n"
            "   • *Reason:*  wins on cost and value, CPA %s %s (%s vs group) and ROAS %s beats cold %s.\n"
            "   • *Evidence:*  freq %s, spend %s %s.\n"
            "   • *Impact:*  High.   *Status:*  Open" %
            (c["ad_name"], money(c["cpa"]), cc, bw(c["gap"]), c["roas"], A["summary"]["cold_roas"], c["freq"], money(c["spend"]), cc))
    for c in A["creatives"]:
        if c["label"] == "CREATIVE FATIGUE":
            cards.append(":large_orange_circle:  *P1 — Refresh:*  %s\n   • *Action:*  new first 3 seconds, keep the concept.\n   • *Reason:*  %s\n   • *Status:*  Open" % (c["ad_name"], c["why"]))
        elif c["label"] == "EFFICIENT-LOW-ROAS":
            cards.append(":large_orange_circle:  *P1 — Test higher AOV:*  %s\n   • *Action:*  run the same hook on a higher-price product, do not scale as is.\n   • *Reason:*  %s\n   • *Status:*  Open" % (c["ad_name"], c["why"]))
    if not cards: return None
    return ["%s   :pushpin:  *DAILY GROWTH ACTIONS*  —  %s\n:date:  %s to %s   ·   ranked by impact, evidence attached\n%s" % (MENTION, nm, DATES["label"][0], DATES["label"][1], DIV)] + cards

def msg_anomaly(A):
    s = A["summary"]; cc = cur(A); out = []
    for c in A["creatives"]:
        if c["spend"] < MIN_SPEND / 2: continue
        flags = []
        if c["d_roas"] is not None and c["d_roas"] <= -AN_ROAS: flags.append("ROAS fell %s (from %s to %s)" % (sp(c["d_roas"]), (c["prev"] or {}).get("roas"), c["roas"]))
        if c["d_cpa"] is not None and c["d_cpa"] >= AN_CPA: flags.append("CPA rose %s (from %s to %s %s)" % (sp(c["d_cpa"]), money((c["prev"] or {}).get("cpa")), money(c["cpa"]), cc))
        if c["d_freq"] is not None and c["d_freq"] >= FAT_FREQ: flags.append("Frequency rose %s (from %s to %s)" % (sp(c["d_freq"]), (c["prev"] or {}).get("freq"), c["freq"]))
        if flags:
            out.append("• *%s*   (spend %s %s)\n      %s" % (c["ad_name"], money(c["spend"]), cc, "\n      ".join(flags)))
    acc = []
    if s["d_spend"] is not None and abs(s["d_spend"]) >= AN_SPEND:
        acc.append("Account spend swung *%s* week over week (now %s %s)." % (sp(s["d_spend"]), money(s["spend"]), cc))
    if not acc and not out: return None
    L = ["%s   :ocean:  *ANOMALY DETECTOR*  —  %s" % (MENTION, A["account"]["name"]),
         ":date:  %s to %s   (vs previous week)" % (DATES["label"][0], DATES["label"][1]), "", DIV, ""]
    if acc: L += acc + [""]
    if out: L += ["*Creatives that moved sharply*", ""] + out
    L += ["", CADENCE()]
    return "\n".join(L)


# ----------------------- 3-day pulse with 80/20 -----------------------
def prow(r, namekey):
    spend = f(r.get("spend")); purch = pick(r.get("actions"), PURCH)
    rev = pick(r.get("action_values"), PURCH) or f(r.get("purchase_roas")) * spend
    return {"name": r.get(namekey, "(unnamed)"), "spend": round(spend), "purch": round(purch),
            "roas": round(rev / spend, 2) if spend else 0, "cpa": round(spend / purch) if purch else None}

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

def block(title, items, cc):
    if not items: return ["*%s*\n   _none_" % title]
    return ["*%s*" % title] + ["   • %s\n        spend %s %s  ·  ROAS %s  ·  CPA %s %s" %
            (x["name"], money(x["spend"]), cc, x["roas"], money(x["cpa"]), cc) for x in items]

def pulse_3day(acct, ad_rows, set_rows):
    cc = acct.get("currency", "")
    ads = [prow(r, "ad_name") for r in ad_rows]
    sets = [prow(r, "adset_name") for r in set_rows]
    tot = sum(x["spend"] for x in ads)
    if tot <= 0: return None
    rev = sum((prow(r, "ad_name")["roas"] * prow(r, "ad_name")["spend"]) for r in ad_rows)
    purch = sum(x["purch"] for x in ads)
    vset, bset, wset, tset = pareto(sets)
    vad, bad, wad, tad = pareto(ads)
    L = ["%s   :zap:  *3-DAY PULSE*  —  %s (%s)" % (MENTION, acct["name"], cc),
         ":date:  %s to %s   (fast 3-day read)" % (DATES["l3"][0], DATES["l3"][1]),
         "", DIV, "",
         "• Spend:  *%s %s*   at ROAS *%s*   on %d purchases" % (money(tot), cc, round(rev / tot, 2), int(purch)),
         "", DIV, "", ":dart:  *THE 80/20*   (the few adsets/ads carrying the account)", "",
         "*%d adsets carry ~80%% of spend (%s %s of %s %s):*" % (len(vset), money(sum(x['spend'] for x in vset)), cc, money(tset), cc), ""]
    L += block(":large_green_circle: Best adsets (by ROAS)", bset, cc) + [""]
    L += block(":red_circle: Worst adsets (by ROAS)", wset, cc) + ["", DIV, ""]
    L += block(":large_green_circle: Best ads (by ROAS)", bad, cc) + [""]
    L += block(":red_circle: Worst ads (by ROAS)", wad, cc)
    L += ["", "_Fast 3-day read. Runs automatically every morning at 9 AM Cairo._"]
    return "\n".join(L)


def CADENCE():
    return "_Window: %s to %s vs the previous 7 days. Runs automatically every morning at 9 AM Cairo._" % (DATES["label"][0], DATES["label"][1])


# ----------------------- main -----------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    global SLACK_TOKEN
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
    DATES["label"] = (fmt_day(y - datetime.timedelta(days=6)), fmt_day(y))
    DATES["p_label"] = (fmt_day(y - datetime.timedelta(days=13)), fmt_day(y - datetime.timedelta(days=7)))
    DATES["l3"] = (fmt_day(y - datetime.timedelta(days=2)), fmt_day(y))

    report = {"generated_at": now.isoformat(), "timezone": TZ, "sample": False, "accounts": []}
    for acct in get_accounts():
        cur_rows = get_insights(acct["id"], last)
        if not cur_rows: continue
        prev_rows = get_insights(acct["id"], prev)
        A = analyze(acct, cur_rows, prev_rows)
        if A["summary"]["spend"] <= 0: continue
        report["accounts"].append(A)
        ch = channel_for(acct["name"])
        if a.daily or a.dry_run:
            slack(ch, msg_summary(A))
            if A["offender"]: slack(ch, msg_offender(A))
            if A["opportunity"]: slack(ch, msg_opportunity(A))
            cards = msg_actions(A)
            if cards:
                for card in cards: slack(ch, card)
            an = msg_anomaly(A)
            if an: slack(ch, an)
            p3 = pulse_3day(acct, get_insights(acct["id"], l3, level="ad", fields=LITE_FIELDS, extra="ad_name"),
                            get_insights(acct["id"], l3, level="adset", fields=LITE_FIELDS, extra="adset_name"))
            if p3: slack(ch, p3)
        time.sleep(1)

    for A in report["accounts"]:
        for c in A["creatives"]:
            c.pop("prev", None)
    save_json(DATA_PATH, report)
    sys.stderr.write("[done] %d accounts\n" % len(report["accounts"]))

if __name__ == "__main__":
    main()
