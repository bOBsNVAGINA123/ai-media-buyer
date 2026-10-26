"""Read-only survey of Playmore's Odoo for the P&L build. Prints structure and totals only."""
import os, xmlrpc.client, collections, json
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
def x(model, method, *a, **kw):
    return M.execute_kw(DB, uid, K, model, method, list(a), kw)
def fields(model, want):
    f = x(model, "fields_get", attributes=["string", "type"])
    return {k: f[k]["type"] for k in want if k in f}
print("uid", uid)
print("teams:", x("crm.team", "search_read", [], fields=["name"]))
print("sale.order fields:", fields("sale.order", ["margin", "team_id", "state", "amount_total", "amount_untaxed", "date_order", "source_id", "medium_id", "website_id"]))
print("order states:", x("sale.order", "read_group", [], ["amount_total:sum"], ["state"], lazy=False))
print("orders by team:", [(g["team_id"], g["__count"], round(g["amount_total"])) for g in x("sale.order", "read_group", [["state", "in", ["sale", "done"]]], ["amount_total:sum"], ["team_id"], lazy=False)])
print("orders by month:", [(g["date_order:month"], g["__count"], round(g["amount_total"])) for g in x("sale.order", "read_group", [["state", "in", ["sale", "done"]]], ["amount_total:sum"], ["date_order:month"], lazy=False)][-15:])
print("first order:", x("sale.order", "search_read", [["state", "in", ["sale", "done"]]], fields=["date_order"], order="date_order asc", limit=1))
print("invoices by month:", [(g["invoice_date:month"], g["__count"], round(g["amount_total"])) for g in x("account.move", "read_group", [["move_type", "=", "out_invoice"], ["state", "=", "posted"]], ["amount_total:sum"], ["invoice_date:month"], lazy=False)][-15:])
print("credit notes by month:", [(g["invoice_date:month"], g["__count"], round(g["amount_total"])) for g in x("account.move", "read_group", [["move_type", "=", "out_refund"], ["state", "=", "posted"]], ["amount_total:sum"], ["invoice_date:month"], lazy=False)][-15:])
print("service products sold (top):", [(g["product_id"], g["__count"], round(g["price_subtotal"])) for g in x("sale.order.line", "read_group", [["product_id.detailed_type", "=", "service"], ["order_id.state", "in", ["sale", "done"]]], ["price_subtotal:sum"], ["product_id"], lazy=False, orderby="price_subtotal desc", limit=8)])
print("POS orders:", x("pos.order", "search_count", []) if "pos.order" in [m["model"] for m in x("ir.model", "search_read", [["model", "=", "pos.order"]], fields=["model"])] else "no pos")
print("warehouses:", x("stock.warehouse", "search_read", [], fields=["name", "code"]))
acc = x("account.account", "search_read", [["account_type", "in", ["expense", "expense_direct_cost"]]], fields=["code", "name"], limit=400)
for a in acc:
    n = (a["name"] or "").lower()
    if any(w in n for w in ("ship", "courier", "bosta", "deliver", "aramex", "advert", "ads", "facebook", "meta", "google", "tiktok", "marketing", "paymob", "collection", "fee", "cost of")):
        print("  acct", a["code"], a["name"])
