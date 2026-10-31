"""Read-only: what the Shopify connector stores per product (ids, handle, images)."""
import os, xmlrpc.client
U = "https://isi-play-more.odoo.com"; DB = "isi-c-play-more-production-12131321"
L, K = os.environ["LOGIN"], os.environ["KEY"]
uid = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/common").authenticate(DB, L, K, {})
M = xmlrpc.client.ServerProxy(U + "/xmlrpc/2/object", allow_none=True)
x = lambda model, method, *a, **kw: M.execute_kw(DB, uid, K, model, method, list(a), kw)
for model in ("shopify.product.template.ept", "shopify.product.product.ept", "shopify.product.image.ept"):
    try:
        f = x(model, "fields_get", attributes=["string", "type"])
        keep = {k: v["type"] for k, v in f.items() if any(w in k for w in ("shopify", "handle", "url", "image", "product_tmpl", "product_id", "sku", "default_code", "name", "published", "src"))}
        print(model, x(model, "search_count", []), keep)
        flds = [k for k in keep if keep[k] in ("char", "many2one", "integer", "boolean", "text")][:12]
        print("  sample:", x(model, "search_read", [], fields=flds, limit=2))
    except Exception as e:
        print(model, "ERR", str(e)[-150:])
