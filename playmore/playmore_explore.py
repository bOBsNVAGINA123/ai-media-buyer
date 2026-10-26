"""Read-only survey round 3: courier costs and order/delivery status anywhere in Playmore's Odoo. Names/totals only."""
import os, xmlrpc.client
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
def x(model, method, *a, **kw):
    try: return M.execute_kw(DB, uid, K, model, method, list(a), kw)
    except Exception as e: return "ERR " + str(e)[-160:]
WORDS = ["bosta", "aramex", "mylerz", "sprint", "j&t", "courier", "shipping", "shipment", "delivery", "deliver", "fedex", "dhl", "r2s", "flash", "شحن", "توصيل", "مندوب"]
print("== installed modules about shipping/courier")
mods = x("ir.module.module", "search_read", [["state", "=", "installed"]], fields=["name", "shortdesc"])
if isinstance(mods, list):
    for m in mods:
        t = (m["name"] + " " + (m["shortdesc"] or "")).lower()
        if any(w in t for w in ("bosta", "deliver", "ship", "carrier", "courier", "shopify", "aramex", "stock")): print("  ", m["name"], "|", m["shortdesc"])
print("== ALL accounts (any type) whose name hints at courier/shipping")
acc = x("account.account", "search_read", [], fields=["code", "name", "account_type"])
if isinstance(acc, list):
    for a in acc:
        if any(w in (a["name"] or "").lower() for w in WORDS): print("  ", a["code"], a["name"], a["account_type"])
print("== partners that look like couriers, and money moved with them (12 months)")
for w in ["bosta", "aramex", "mylerz", "sprint", "j&t", "courier", "shipping", "شحن", "توصيل"]:
    ps = x("res.partner", "search_read", [["name", "ilike", w]], fields=["name"], limit=10)
    if isinstance(ps, list) and ps:
        for p in ps:
            b = x("account.move", "read_group", [["partner_id", "=", p["id"]], ["state", "=", "posted"], ["date", ">=", "2025-10-01"]], ["amount_total:sum"], ["move_type"], lazy=False)
            print("  ", w, "->", p["name"], "|", [(g["move_type"], g["__count"], round(g["amount_total"])) for g in b] if isinstance(b, list) else b)
print("== journal lines whose label mentions a courier (12 months)")
for w in ["bosta", "aramex", "mylerz", "shipping", "courier", "شحن"]:
    g = x("account.move.line", "read_group", [["name", "ilike", w], ["parent_state", "=", "posted"], ["date", ">=", "2025-10-01"]], ["balance:sum"], ["account_id"], lazy=False, limit=8)
    if isinstance(g, list) and g: print("  ", w, [(r["account_id"][1][:45], r["__count"], round(r["balance"])) for r in g])
print("== journals")
print("  ", [(j["name"], j["type"]) for j in x("journal", "search_read", [], fields=["name","type"])] if False else x("account.journal", "search_read", [], fields=["name", "type"]))
print("== order / delivery status fields")
for model in ("sale.order", "stock.picking"):
    f = x(model, "fields_get", attributes=["string", "type"])
    if isinstance(f, dict):
        hits = {k: (v["string"], v["type"]) for k, v in f.items() if any(w in (k + " " + v["string"]).lower() for w in ("bosta", "track", "deliver", "carrier", "ship", "fulfil", "status", "state", "courier", "cod", "x_"))}
        print("  ", model, hits)
print("== sale.order (Shopify team) delivery status counts")
print("  ", x("sale.order", "read_group", [["team_id.name", "=", "Shopify"], ["state", "=", "sale"]], ["amount_total:sum"], ["delivery_status"], lazy=False))
print("== stock.picking states (outgoing, 12 months)")
print("  ", [(g["state"], g["__count"]) for g in x("stock.picking", "read_group", [["picking_type_code", "=", "outgoing"], ["create_date", ">=", "2025-10-01"]], ["id:count"], ["state"], lazy=False)] if True else "")
print("== delivery.carrier")
print("  ", x("delivery.carrier", "search_read", [], fields=["name", "delivery_type"]))
