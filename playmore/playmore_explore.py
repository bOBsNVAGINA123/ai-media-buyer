"""Read-only survey round 4: Bosta/Paymob money flow, returns, shipping cost, Shopify connector records. Totals only."""
import os, xmlrpc.client, datetime
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
def x(model, method, *a, **kw):
    try: return M.execute_kw(DB, uid, K, model, method, list(a), kw)
    except Exception as e: return "ERR " + str(e)[-200:]
def mon(lbl):
    try: return datetime.datetime.strptime(lbl, "%B %Y").strftime("%Y-%m")
    except Exception: return lbl
for code, nm in (("12.01.02.00.00", "Bosta"), (None, "Paymob")):
    dom = [["account_id.code", "=", code]] if code else [["journal_id.name", "=", "Paymob"], ["account_id.account_type", "=", "asset_cash"]]
    print("==", nm, "account by month: debit (money in) / credit (money out) / count")
    g = x("account.move.line", "read_group", dom + [["parent_state", "=", "posted"]], ["debit:sum", "credit:sum"], ["date:month"], lazy=False)
    if isinstance(g, list):
        for r in g[-16:]: print("  ", mon(r["date:month"]), round(r["debit"]), round(r["credit"]), r["__count"])
    print("  counter-accounts on", nm, "moves:")
    mv = x("account.move.line", "search_read", dom + [["parent_state", "=", "posted"]], fields=["move_id"], limit=3000)
    if isinstance(mv, list) and mv:
        ids = list({m["move_id"][0] for m in mv})
        g2 = x("account.move.line", "read_group", [["move_id", "in", ids]] + ([["account_id.code", "!=", code]] if code else [["account_id.account_type", "!=", "asset_cash"]]), ["debit:sum", "credit:sum"], ["account_id"], lazy=False, limit=12)
        if isinstance(g2, list):
            for r in g2: print("    ", r["account_id"][1][:50], "debit", round(r["debit"]), "credit", round(r["credit"]), "lines", r["__count"])
print("== customer payments by journal (12 months)")
g = x("account.payment", "read_group", [["payment_type", "=", "inbound"], ["state", "=", "posted"], ["date", ">=", "2025-10-01"]], ["amount:sum"], ["journal_id"], lazy=False)
print("  ", [(r["journal_id"][1], r["__count"], round(r["amount"])) for r in g] if isinstance(g, list) else g)
print("== returns: incoming pickings that return a delivery, by month")
g = x("stock.picking", "read_group", [["picking_type_code", "=", "incoming"], ["return_id", "!=", False], ["state", "=", "done"]], ["id:count"], ["date_done:month"], lazy=False)
print("  ", [(mon(r["date_done:month"]), r["__count"]) for r in g][-16:] if isinstance(g, list) else g)
g = x("stock.picking", "read_group", [["picking_type_code", "=", "incoming"], ["origin", "ilike", "Return"], ["state", "=", "done"]], ["id:count"], ["date_done:month"], lazy=False)
print("  by origin 'Return':", [(mon(r["date_done:month"]), r["__count"]) for r in g][-16:] if isinstance(g, list) else g)
print("== outgoing deliveries done by month (count) and carrier_price")
g = x("stock.picking", "read_group", [["picking_type_code", "=", "outgoing"], ["state", "=", "done"]], ["carrier_price:sum"], ["date_done:month"], lazy=False)
print("  ", [(mon(r["date_done:month"]), r["__count"], round(r.get("carrier_price") or 0)) for r in g][-16:] if isinstance(g, list) else g)
print("== Shopify connector models")
ms = x("ir.model", "search_read", [["model", "ilike", "shopify"]], fields=["model", "name"])
if isinstance(ms, list):
    for m in ms:
        if any(w in m["model"] for w in ("payout", "profit", "fee", "fulfil", "refund", "transaction", "order")):
            n = x(m["model"], "search_count", [])
            print("  ", m["model"], "|", m["name"], "|", n)
