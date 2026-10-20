"""Did what you applied do what the card said? Runs every morning before the new cards are written.

For each action applied 1-30 days ago: the object's Meta numbers for the days since it was applied vs the
7 days before, per day, with the whole account's ROAS drift between the two windows taken out. Meta's
attributed value is turned into real revenue with the same conversion the forecast used (the account's
measured extra-pound return / the account's Meta ROAS), so forecast and actual are like for like.
"""
import json, datetime as dt
import data as D
import expect as X
import notify as N


def window(obj, since, until):
    j = D.g(obj + "/insights", {"time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
                                "fields": "spend,actions,action_values", "action_attribution_windows": json.dumps(["7d_click"])})
    x = (j.get("data") or [{}])[0]
    days = (until - since).days + 1
    V = x.get("action_values")
    return {"spend": float(x.get("spend") or 0) / days,
            "online": D._act(V, "offsite_conversion.fb_pixel_purchase", "7d_click") / days,
            "instore": D._act(V, "offline_conversion.purchase", "7d_click") / days}


def track(account, M_ON, M_ST):
    today = dt.date.today(); yday = today - dt.timedelta(days=1)
    since = (today - dt.timedelta(days=31)).isoformat()
    rows = N.sb("GET", "adbot_actions?select=id,kind,object_id,change,result,metrics,executed_at&status=eq.executed&executed_at=gte." + since) or []
    acc = D.ACT
    k_on = X.CAL["on"][0] / (account.get("roas_online7_4w") or 1)      # real revenue per Meta-claimed E£, online
    k_st = X.CAL["st"][0] / (account.get("roas_instore7_4w") or 1)     # same, in-store
    for a in rows:
        ex = dt.datetime.fromisoformat(a["executed_at"].replace("Z", "+00:00")).date()
        a0, a1 = ex + dt.timedelta(days=1), min(ex + dt.timedelta(days=14), yday)
        if a1 < a0:
            continue
        ch = a.get("change") or {}
        obj = ((a.get("result") or {}).get("copy") if ch.get("op") == "copy" else None) or ch.get("object") or a["object_id"]
        if not isinstance(obj, str):
            continue
        try:
            b = window(obj, ex - dt.timedelta(days=7), ex - dt.timedelta(days=1)) if ch.get("op") != "copy" else {"spend": 0, "online": 0, "instore": 0}
            af = window(obj, a0, a1)
            ab = window("act_" + acc, ex - dt.timedelta(days=7), ex - dt.timedelta(days=1)); aa = window("act_" + acc, a0, a1)
        except Exception as e:
            D.log("outcome", a["id"], "failed", e); continue
        roas = lambda w: (w["online"] + w["instore"]) / w["spend"] if w["spend"] else 0
        drift = roas(aa) / roas(ab) if roas(ab) else 1.0
        d_sp = af["spend"] - b["spend"]
        d_on = af["online"] / drift - b["online"]; d_st = af["instore"] / drift - b["instore"]
        net = d_on * k_on * M_ON + d_st * k_st * M_ST - d_sp
        days = (a1 - a0).days + 1
        pick = ((a.get("metrics") or {}).get("sizing") or {}).get("pick") or {}
        if days < 4:
            verdict = "too early"
        elif not pick:
            verdict = "saved" if net >= 0 else "cost"
        else:
            verdict = "beat" if net >= pick["net_p50"] else "on track" if net >= pick["net_p10"] else "missed"
        out = {"days": days, "measured": obj, "before": {k: round(v) for k, v in b.items()}, "after": {k: round(v) for k, v in af.items()},
               "account_drift": round(drift, 2), "net_day": round(net), "verdict": verdict,
               "forecast": {"p10": pick.get("net_p10"), "p50": pick.get("net_p50"), "p90": pick.get("net_p90")} if pick else None}
        N.sb("PATCH", "adbot_actions?id=eq.%s" % a["id"], {"outcome": out, "outcome_at": dt.datetime.utcnow().isoformat() + "Z"}, prefer="return=minimal")
    D.log("outcomes tracked", len(rows))
