"""Save the morning's run to Supabase and push one notification to every saved phone."""
import json, os, datetime as dt
import requests
import data as D

SB = os.environ.get("SUPABASE_URL", "").rstrip("/")
SBK = os.environ.get("SUPABASE_SERVICE_KEY", "")


def sb(method, path, payload=None, prefer="return=representation"):
    r = requests.request(method, SB + "/rest/v1/" + path, data=json.dumps(payload, default=str) if payload is not None else None,
                         headers={"apikey": SBK, "Authorization": "Bearer " + SBK, "Content-Type": "application/json", "Prefer": prefer},
                         timeout=60)
    if r.status_code >= 300:
        raise RuntimeError("Supabase %s %s: %s" % (method, path, r.text[:300]))
    return r.json() if r.text else None


def row(a, run_id):
    keep = ("need7", "x_need", "type_cpa", "last7", "last28", "lifetime", "stock", "adset_name", "note", "rule", "hit_txt")
    return {"run_id": run_id, "kind": a["kind"], "level": a["level"], "object_id": str(a["id"]), "object_name": a.get("name"),
            "campaign": a.get("campaign"), "audience": a.get("audience"), "confidence": a["confidence"],
            "hit_rate": a.get("hit_rate"), "why": a.get("why"), "change": a.get("change"),
            "metrics": {k: a.get(k) for k in keep if a.get(k) is not None}}


def publish(rep):
    # yesterday's unanswered actions are stale once new numbers exist
    sb("PATCH", "adbot_actions?status=eq.pending", {"status": "expired"}, prefer="return=minimal")
    summary = {k: rep.get(k) for k in ("weeks", "type_cpa", "types", "counts", "skipped", "dpa_sets", "token_scopes")}
    summary["landing_pages"] = rep.get("landing_pages")
    run = sb("POST", "adbot_runs", {"run_date": dt.date.today().isoformat(), "summary": summary})[0]
    acts = rep.get("actions") or []
    for i in range(0, len(acts), 200):
        sb("POST", "adbot_actions", [row(a, run["id"]) for a in acts[i:i + 200]], prefer="return=minimal")
    D.log("saved run", run["id"], "with", len(acts), "actions")
    # every live ad's landing / catalogue stock, every day -> the per-ad stock test grows over time
    day = dt.date.today().isoformat(); snap = []
    for aid, st in (rep.get("ad_stock") or {}).items():
        snap.append({"day": day, "ad_id": str(aid), "kind": st.get("kind"), "url": st.get("url"), "product_set": st.get("product_set"),
                     "idx": st.get("index"), "idx_unweighted": st.get("index_unweighted"), "first4": st.get("first4")})
    for i in range(0, len(snap), 300):
        sb("POST", "adbot_stock?on_conflict=day,ad_id", snap[i:i + 300], prefer="resolution=merge-duplicates,return=minimal")
    D.log("stock snapshot", len(snap), "ads")
    push(rep, len(acts))


def push(rep, n):
    priv = os.environ.get("VAPID_PRIVATE", "")
    if not priv or not n:
        D.log("push skipped (no key or no actions)"); return
    from pywebpush import webpush, WebPushException
    c = collections_counts(rep)
    body = "%d to pause · %d stock · %d to trim · %d to push · %d winners" % (c["pause"], c["stock"], c["trim"], c["scale"], c["winner"])
    payload = json.dumps({"title": "Ad Desk: %d actions waiting" % n, "body": body, "url": "/", "tag": "adbot-" + dt.date.today().isoformat()})
    for p in sb("GET", "adbot_phones?select=id,endpoint,sub") or []:
        try:
            webpush(subscription_info=p["sub"], data=payload, vapid_private_key=priv,
                    vapid_claims={"sub": "mailto:" + os.environ.get("VAPID_EMAIL", "adbot@ourkids-eg.com")})
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):          # phone unsubscribed or reinstalled: forget it
                sb("DELETE", "adbot_phones?id=eq.%s" % p["id"], prefer="return=minimal")
            D.log("push failed", code)
    D.log("push sent")


def collections_counts(rep):
    c = {"pause": 0, "stock": 0, "trim": 0, "scale": 0, "winner": 0}
    for a in rep.get("actions") or []:
        c[a["kind"]] = c.get(a["kind"], 0) + 1
    return c
