"""Read-only survey of Playmore's Odoo for the P&L build (round 2). Prints structure and totals only."""
import os, xmlrpc.client, collections
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
x = lambda model, method, *a, **kw: M.execute_kw(DB, uid, K, model, method, list(a), kw)
OK = [["state", "=", "sale"]]
# who buys through the PlayMore team, and what are negative orders
print("PlayMore-team top customers:", [(g["partner_id"], g["__count"], round(g["amount_total"])) for g in x("sale.order", "read_group", OK + [["team_id", "=", 6]], ["amount_total:sum"], ["partner_id"], lazy=False, orderby="amount_total desc", limit=8)])
print("PlayMore-team by month:", [(g["date_order:month"], g["__count"], round(g["amount_total"])) for g in x("sale.order", "read_group", OK + [["team_id", "=", 6]], ["amount_total:sum"], ["date_order:month"], lazy=False)][-14:])
neg = x("sale.order", "search_read", OK + [["amount_total", "<", 0]], fields=["name", "team_id", "amount_total", "partner_id", "origin", "client_order_ref"], limit=6, order="amount_total asc")
print("biggest negative orders:", neg)
print("negative orders by team:", [(g["team_id"], g["__count"], round(g["amount_total"])) for g in x("sale.order", "read_group", OK + [["amount_total", "<", 0]], ["amount_total:sum"], ["team_id"], lazy=False)])
# Shopify team monthly: positive vs negative, untaxed vs total, margin
for sign, dom in (("pos", [["amount_total", ">", 0]]), ("neg", [["amount_total", "<", 0]])):
    print("Shopify team", sign, [(g["date_order:month"], g["__count"], round(g["amount_total"]), round(g["amount_untaxed"]), round(g["margin"])) for g in x("sale.order", "read_group", OK + [["team_id", "=", 5]] + dom, ["amount_total:sum", "amount_untaxed:sum", "margin:sum"], ["date_order:month"], lazy=False)][-14:])
# taxes in use
print("taxes:", x("account.tax", "search_read", [["type_tax_use", "=", "sale"], ["active", "=", True]], fields=["name", "amount"]))
# expense accounts with posted balance last 12 months
g = x("account.move.line", "read_group", [["parent_state", "=", "posted"], ["date", ">=", "2025-10-01"], ["account_id.account_type", "in", ["expense", "expense_direct_cost", "expense_depreciation"]]], ["balance:sum"], ["account_id"], lazy=False, orderby="balance desc", limit=40)
for r in g: print("  exp", r["account_id"][1][:60], round(r["balance"]))
# shopify order ids/channel tags
print("sale.order sample shopify:", x("sale.order", "search_read", OK + [["team_id", "=", 5]], fields=["name", "client_order_ref", "origin", "date_order"], limit=3, order="date_order desc"))
