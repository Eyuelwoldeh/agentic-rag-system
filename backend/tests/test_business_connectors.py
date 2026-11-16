"""Business-system and work-app connectors against canned API responses (no real tokens, no network)."""
import base64
import csv
import io
import json
from datetime import datetime, timezone

import httpx
import pytest
from test_connectors import mock

from app import connectors, oauth
from app.connectors import (base, confluence, hubspot, jira, microsoft365, quickbooks, salesforce, shopify, square, stripe,
                            xero)


@pytest.fixture
def http(monkeypatch):
    def use(routes):
        monkeypatch.setattr(base, "http", mock(routes))
    return use


def table(doc) -> list[dict]:
    name, blob = doc.table_file
    assert name == f"{doc.title}.csv"
    return list(csv.DictReader(io.StringIO(blob.decode())))


def by_id(docs) -> dict:
    return {d.external_id: d for d in docs}


def test_business_category_is_synced_not_live():
    assert connectors.REGISTRY["shopify"].CATEGORY == "business" and not connectors.is_live("shopify")
    assert {c["category"] for c in connectors.catalog(set())} >= {"index", "business", "warehouse", "tool"}


def test_quickbooks_pnl_invoices_expenses(http):
    creds = {"access_token": "x", "realm_id": "123"}
    api = "https://quickbooks.api.intuit.com/v3/company/123/"

    def query(req):
        q = req.url.params["query"]
        assert req.url.params["minorversion"] == quickbooks.MINOR_VERSION
        if "from Invoice" in q:
            return {"QueryResponse": {"Invoice": [{"Id": "9", "DocNumber": "1001", "TxnDate": "2026-10-02", "TotalAmt": 250.0,
                                                   "Balance": 50.0, "CustomerRef": {"value": "4", "name": "Acme"},
                                                   "CurrencyRef": {"value": "USD"}}]}}
        return {"QueryResponse": {"Purchase": [{"Id": "7", "TxnDate": "2026-10-03", "TotalAmt": 80.0, "PaymentType": "CreditCard",
                                                "EntityRef": {"name": "AWS"}, "AccountRef": {"name": "Amex"},
                                                "Line": [{"AccountBasedExpenseLineDetail": {"AccountRef": {"name": "Hosting"}}}]}]}}

    http({
        ("GET", api + "companyinfo/123"): {"CompanyInfo": {"CompanyName": "Acme Books"}},
        ("GET", api + "query"): query,
        ("GET", api + "reports/ProfitAndLoss"): {
            "Columns": {"Column": [{"ColTitle": ""}, {"ColTitle": "Sep 2026", "MetaData": [{"Name": "StartDate", "Value": "2026-09-01"}]},
                                   {"ColTitle": "Oct 2026", "MetaData": [{"Name": "StartDate", "Value": "2026-10-01"}]},
                                   {"ColTitle": "Total"}]},
            "Rows": {"Row": [{"Header": {"ColData": [{"value": "Income"}]},
                              "Rows": {"Row": [{"ColData": [{"value": "Sales"}, {"value": "100.00"}, {"value": "150.00"}, {"value": "250.00"}]}]},
                              "Summary": {"ColData": [{"value": "Total Income"}, {"value": "100.00"}, {"value": "150.00"}, {"value": "250.00"}]}}]}},
    })
    assert quickbooks.verify({}, creds) == "Acme Books"
    docs = by_id(quickbooks.fetch({}, None, creds))
    pnl = table(docs["quickbooks:pnl"])
    assert list(pnl[0]) == ["section", "account", "line_type", "month", "amount"]
    assert {"section": "Income", "account": "Sales", "line_type": "account", "month": "2026-10-01", "amount": "150.00"} in pnl
    assert len(pnl) == 4  # 2 months × (Sales + Total Income); the Total column is dropped
    assert table(docs["quickbooks:invoices"])[0] == {"invoice_id": "9", "number": "1001", "date": "2026-10-02", "due_date": "",
                                                          "customer": "Acme", "total": "250.0", "balance": "50.0", "currency": "USD"}
    exp = table(docs["quickbooks:expenses"])[0]
    assert exp["payee"] == "AWS" and exp["category"] == "Hosting" and exp["total"] == "80.0"
    with pytest.raises(ValueError, match="realmId"):
        quickbooks.verify({}, {"access_token": "x", "realm_id": "1/../x"})


def test_xero_invoices_and_pnl(http):
    tid = "11111111-2222-3333-4444-555555555555"
    http({
        ("GET", "https://api.xero.com/connections"): [{"tenantId": tid, "tenantType": "ORGANISATION", "tenantName": "Demo Co"}],
        ("GET", xero.API + "Invoices"): lambda r: {"Invoices": [
            {"InvoiceID": "inv-1", "InvoiceNumber": "INV-1", "Type": "ACCREC", "Contact": {"Name": "Acme"},
             "DateString": "2026-10-01T00:00:00", "Status": "PAID", "Total": 115.0, "AmountDue": 0, "CurrencyCode": "NZD"}]
            if r.headers["xero-tenant-id"] == tid else []},
        ("GET", xero.API + "Reports/ProfitAndLoss"): {"Reports": [{"Rows": [
            {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "31 Oct 2026"}, {"Value": "30 Sep 2026"}]},
            {"RowType": "Section", "Title": "Income", "Rows": [
                {"RowType": "Row", "Cells": [{"Value": "Sales"}, {"Value": "500.00"}, {"Value": "400.00"}]},
                {"RowType": "SummaryRow", "Cells": [{"Value": "Total Income"}, {"Value": "500.00"}, {"Value": "400.00"}]}]}]}]},
    })
    assert xero.verify({}, {"access_token": "x"}) == "Demo Co"
    docs = by_id(xero.fetch({}, None, {"access_token": "x"}))
    inv = table(docs["xero:invoices"])[0]
    assert inv["type"] == "sales" and inv["date"] == "2026-10-01" and inv["total"] == "115.0" and inv["currency"] == "NZD"
    pnl = table(docs["xero:pnl"])
    assert {"section": "Income", "account": "Sales", "line_type": "account", "month": "2026-09-01", "amount": "400.00"} in pnl
    assert len(pnl) == 4


def test_shopify_orders_lines_products(http):
    money = lambda v: {"shopMoney": {"amount": v}}  # noqa: E731

    def gql(req):
        assert req.headers["X-Shopify-Access-Token"] == "shpat_x"
        q = json.loads(req.content)["query"]
        if "shop {" in q:
            return {"data": {"shop": {"name": "Acme Store"}}}
        if "orders(" in q:
            cursor = json.loads(req.content)["variables"]["cursor"]
            order = {"id": f"gid://shopify/Order/{1 if not cursor else 2}", "name": "#1001" if not cursor else "#1002",
                     "createdAt": "2026-10-05T10:00:00Z", "currencyCode": "USD", "sourceName": "web",
                     "channelInformation": {"channelDefinition": {"channelName": "Online Store"}}, "retailLocation": None,
                     "displayFinancialStatus": "PAID", "subtotalPriceSet": money("90.0"), "totalPriceSet": money("99.0"),
                     "lineItems": {"nodes": [{"name": "Mug", "sku": "MUG-1", "quantity": 2, "originalTotalSet": money("90.0"),
                                              "discountedTotalSet": money("90.0")}]}}
            return {"data": {"orders": {"nodes": [order], "pageInfo": {"hasNextPage": not cursor, "endCursor": "c1"}}}}
        return {"data": {"products": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "gid://shopify/Product/5", "title": "Mug", "productType": "Kitchen", "vendor": "Acme", "status": "ACTIVE",
             "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/8", "title": "Blue", "sku": "MUG-1", "price": "45.00",
                                     "inventoryQuantity": 12}]}}]}}}

    http({("POST", f"https://acme.myshopify.com/admin/api/{shopify.VERSION}/graphql.json"): gql})
    cfg = {"shop": "https://Acme.myshopify.com/"}
    assert shopify.verify(cfg, {"access_token": "shpat_x"}) == "Acme Store"
    docs = by_id(shopify.fetch(cfg, None, {"access_token": "shpat_x"}))
    orders = table(docs["shopify:orders"])
    assert [o["order"] for o in orders] == ["#1001", "#1002"]  # paginated
    assert orders[0]["channel"] == "Online Store" and orders[0]["total"] == "99.0" and orders[0]["order_id"] == "1"
    assert table(docs["shopify:order_lines"])[0]["quantity"] == "2"
    prod = table(docs["shopify:products"])[0]
    assert prod["variant_id"] == "8" and prod["price"] == "45.00" and prod["inventory"] == "12"
    for bad in ("evil.com", "acme.myshopify.com.evil.com", "acme.myshopify.com/../x"):
        with pytest.raises(ValueError):
            shopify.verify({"shop": bad}, {"access_token": "x"})


def test_square_locations_and_payments(http):
    def payments(req):
        if req.url.params["location_id"] != "L1":
            return {"payments": []}
        if "cursor" not in req.url.params:
            return {"payments": [{"id": "p1", "status": "COMPLETED", "amount_money": {"amount": 1250, "currency": "USD"},
                                  "total_money": {"amount": 1450, "currency": "USD"}, "tip_money": {"amount": 200, "currency": "USD"},
                                  "source_type": "CARD", "card_details": {"card": {"card_brand": "VISA"}}}], "cursor": "n"}
        return {"payments": [{"id": "p2", "amount_money": {"amount": 500, "currency": "JPY"}}]}

    http({
        ("GET", square.API + "merchants/me"): {"merchant": {"business_name": "Corner Cafe"}},
        ("GET", square.API + "locations"): {"locations": [{"id": "L1", "name": "Downtown", "currency": "USD",
                                                           "address": {"locality": "Austin"}}, {"id": "L2", "name": "Airport"}]},
        ("GET", square.API + "payments"): payments,
    })
    assert square.whoami({"access_token": "x"}) == "Corner Cafe"
    docs = by_id(square.fetch({}, None, {"access_token": "x"}))
    assert [r["city"] for r in table(docs["square:locations"])] == ["Austin", ""]
    pays = table(docs["square:payments"])
    assert {k: pays[0][k] for k in ("payment_id", "location", "amount", "tip", "total", "card_brand", "currency")} == {
        "payment_id": "p1", "location": "Downtown", "amount": "12.5", "tip": "2.0", "total": "14.5", "card_brand": "VISA", "currency": "USD"}
    assert pays[1]["amount"] == "500.0"  # yen: no minor unit


def test_stripe_charges_invoices_subscriptions(http):
    def lst(items):
        return lambda req: {"data": items if "starting_after" not in req.url.params else [], "has_more": "starting_after" not in req.url.params}

    http({
        ("GET", stripe.API + "charges"): lst([{"id": "ch_1", "amount": 2000, "amount_refunded": 500, "currency": "usd", "paid": True,
                                               "status": "succeeded", "created": 1793000000}]),
        ("GET", stripe.API + "invoices"): lst([{"id": "in_1", "total": 5000, "amount_paid": 5000, "currency": "jpy", "status": "paid"}]),
        ("GET", stripe.API + "subscriptions"): lst([{"id": "sub_1", "status": "active", "customer": "cus_1", "items": {"data": [
            {"quantity": 3, "price": {"unit_amount": 1500, "currency": "eur", "product": "prod_1",
                                      "recurring": {"interval": "month", "interval_count": 1}}}]}}]),
    })
    assert stripe.whoami({"access_token": "rk_x"}) == "Stripe account"
    docs = by_id(stripe.fetch({}, None, {"access_token": "rk_x"}))
    ch = table(docs["stripe:charges"])[0]
    assert ch["amount"] == "20.0" and ch["amount_refunded"] == "5.0" and ch["currency"] == "USD"
    assert ch["created_at"].startswith("2026-10-26")
    assert table(docs["stripe:invoices"])[0]["total"] == "5000"  # zero-decimal currency stays as-is
    sub = table(docs["stripe:subscriptions"])[0]
    assert sub["unit_amount"] == "15.0" and sub["quantity"] == "3" and sub["interval"] == "month" and sub["currency"] == "EUR"


def test_hubspot_deals_companies_contacts(http):
    def objects(req):
        obj = req.url.path.rsplit("/", 1)[-1]
        props = {"deals": {"dealname": "Big deal", "dealstage": "s2", "pipeline": "default", "amount": "12000",
                           "closedate": "2026-12-01T00:00:00Z", "hubspot_owner_id": "7", "hs_is_closed_won": "false"},
                 "companies": {"name": "Acme", "domain": "acme.com", "hubspot_owner_id": "7"},
                 "contacts": {"firstname": "Ana", "lastname": "Li", "email": "ana@acme.com", "phone": "555"}}[obj]
        if req.url.params.get("after"):
            return {"results": [{"id": "2", "properties": props}]}
        return {"results": [{"id": "1", "properties": props}], "paging": {"next": {"after": "1"}}}

    http({
        ("GET", hubspot.API + "account-info/v3/details"): {"portalId": 42},
        ("GET", hubspot.API + "crm/v3/owners"): {"results": [{"id": "7", "firstName": "Sam", "lastName": "Ray"}]},
        ("GET", hubspot.API + "crm/v3/pipelines/deals"): {"results": [{"id": "default", "label": "Sales",
                                                                       "stages": [{"id": "s2", "label": "Negotiation"}]}]},
        ("GET", hubspot.API + "crm/v3/objects/"): objects,
    })
    assert hubspot.whoami({"access_token": "pat"}) == "HubSpot portal 42"
    docs = by_id(hubspot.fetch({}, None, {"access_token": "pat"}))
    deals = table(docs["hubspot:deals"])
    assert len(deals) == 2 and deals[0]["stage"] == "Negotiation" and deals[0]["pipeline"] == "Sales"
    assert deals[0]["owner"] == "Sam Ray" and deals[0]["amount"] == "12000"
    assert table(docs["hubspot:companies"])[0]["domain"] == "acme.com"
    contacts = table(docs["hubspot:contacts"])
    assert contacts[0]["email"] == "ana@acme.com" and "phone" not in contacts[0]


def test_salesforce_soql_paging_and_instance_guard(http):
    inst = "https://acme.my.salesforce.com"
    q = f"{inst}/services/data/{salesforce.VERSION}/query"

    def query(req):
        if "Opportunity" in req.url.params["q"]:
            return {"done": False, "nextRecordsUrl": f"/services/data/{salesforce.VERSION}/query/01g-2000", "records": [
                {"Id": "006A", "Name": "Renewal", "StageName": "Closed Won", "Amount": 9000.0, "CloseDate": "2026-09-30",
                 "Account": {"Name": "Acme"}, "Owner": {"Name": "Sam"}}]}
        return {"done": True, "records": [{"Id": "001A", "Name": "Acme", "Industry": "Retail", "Owner": None}]}

    http({
        ("GET", f"{inst}/services/oauth2/userinfo"): {"preferred_username": "sam@acme.com"},
        ("GET", q): query,
        ("GET", f"{q}/01g-2000"): {"done": True, "records": [{"Id": "006B", "Name": "Upsell", "Amount": None, "Account": None}]},
    })
    creds = {"access_token": "x", "instance_url": inst}
    assert salesforce.whoami(creds) == "sam@acme.com"
    docs = by_id(salesforce.fetch({}, None, creds))
    opps = table(docs["salesforce:opportunities"])
    assert [o["Name"] for o in opps] == ["Renewal", "Upsell"]
    assert opps[0]["AccountName"] == "Acme" and opps[0]["OwnerName"] == "Sam" and opps[0]["Amount"] == "9000.0"
    assert table(docs["salesforce:accounts"])[0]["Industry"] == "Retail"
    with pytest.raises(ValueError):
        salesforce.whoami({"access_token": "x", "instance_url": "https://evil.com"})


def test_jira_issues_as_documents(http):
    adf = lambda t: {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}  # noqa: E731

    def search(req):
        jql = req.url.params["jql"]
        assert jql.startswith("project in (ENG) AND (labels = bug) AND updated >= ") and req.headers["Authorization"].startswith("Basic ")
        return {"isLast": True, "issues": [{"id": "10001", "key": "ENG-7", "fields": {
            "summary": "Checkout times out", "description": adf("Payments call hangs after 30s"),
            "status": {"name": "In Progress"}, "assignee": {"displayName": "Ana"}, "project": {"name": "Engineering"},
            "comment": {"comments": [{"author": {"displayName": "Bo"}, "body": adf("Raised the timeout")}]},
            "updated": "2026-11-01T09:30:00.000+0000"}}]}

    http({("GET", "https://acme.atlassian.net/rest/api/3/myself"): {"emailAddress": "bot@acme.com"},
          ("GET", "https://acme.atlassian.net/rest/api/3/search/jql"): search})
    cfg, creds = {"site": "acme.atlassian.net", "email": "bot@acme.com", "projects": "eng", "jql": "labels = bug"}, {"access_token": "t"}
    assert jira.verify(cfg, creds) == "bot@acme.com @ acme.atlassian.net"
    [d] = list(jira.fetch(cfg, datetime(2026, 10, 1, tzinfo=timezone.utc), creds))
    assert d.external_id == "10001" and d.url == "https://acme.atlassian.net/browse/ENG-7"
    assert "Status: In Progress" in d.text and "Assignee: Ana" in d.text and "Payments call hangs" in d.text
    assert "Bo: Raised the timeout" in d.text
    with pytest.raises(ValueError):
        list(jira.fetch({**cfg, "projects": "ENG) OR (1=1"}, None, creds))
    with pytest.raises(ValueError):
        jira.verify({**cfg, "site": "169.254.169.254"}, creds)


def test_confluence_pages_since_and_links(http):
    page = lambda pid, ts: {"id": pid, "title": f"Page {pid}", "version": {"createdAt": ts},  # noqa: E731
                            "body": {"storage": {"value": "<h1>Runbook</h1><p>Restart <b>the API</b></p>"}},
                            "_links": {"webui": f"/spaces/ENG/pages/{pid}"}}
    http({
        ("GET", "https://acme.atlassian.net/wiki/api/v2/spaces/"): {"results": [page("1", "2026-11-02T00:00:00.000Z"),
                                                                              page("2", "2026-01-01T00:00:00.000Z")],
                                                                  "_links": {"next": "/wiki/api/v2/spaces/99/pages?cursor=abc"}},
        ("GET", "https://acme.atlassian.net/wiki/api/v2/spaces"): {"results": [{"id": "99", "key": "ENG"}]},
    })
    cfg, creds = {"site": "acme.atlassian.net", "email": "bot@acme.com", "spaces": "ENG"}, {"access_token": "t"}
    assert confluence.verify(cfg, creds) == "bot@acme.com @ acme.atlassian.net · 1 spaces"
    docs = list(confluence.fetch(cfg, datetime(2026, 6, 1, tzinfo=timezone.utc), creds))
    assert [d.external_id for d in docs] == ["1"]  # older page stops the newest-first scan
    assert "Restart the API" in docs[0].text and docs[0].url == "https://acme.atlassian.net/wiki/spaces/ENG/pages/1"
    with pytest.raises(ValueError, match="not found"):
        confluence.verify({**cfg, "spaces": "ENG,HR"}, creds)


def test_microsoft365_files_tables_and_permissions(http, monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest, "extract_pages", lambda name, data: [(1, f"{name}: {data.decode()}")])
    g = microsoft365.GRAPH
    item = lambda iid, name: {"id": iid, "name": name, "file": {}, "size": 10, "webUrl": f"https://x/{iid}",  # noqa: E731
                              "lastModifiedDateTime": "2026-11-01T00:00:00Z"}
    perms = {"w1": [{"grantedToV2": {"siteUser": {"loginName": "i:0#.f|membership|Ana@Contoso.com"}}}],
             "x1": [{"link": {"scope": "organization"}}],
             "p1": [{"grantedToV2": {"siteGroup": {"displayName": "Finance Members", "loginName": "Finance Members"}}}]}
    http({
        ("GET", g + "me"): {"mail": "ana@contoso.com"},
        ("GET", g + "me/drive"): {"id": "D1"},
        ("GET", g + "drives/D1/root/delta"): {"value": [{"id": "root", "name": "root", "folder": {}, "lastModifiedDateTime": "2026-11-01T00:00:00Z"},
                                                        item("w1", "Plan.docx"), item("x1", "Budget.xlsx"), item("p1", "Q3.pdf"),
                                                        item("z1", "photo.png"), {**item("d1", "gone.txt"), "deleted": {}}]},
        ("GET", g + "drives/D1/items/"): lambda r: (
            {"value": perms[r.url.path.split("/")[-2]]} if r.url.path.endswith("/permissions")
            else httpx.Response(200, content=(b"converted" if r.url.params.get("format") == "pdf" else b"raw"))),
    })
    assert microsoft365.whoami({"access_token": "x"}) == "ana@contoso.com"
    docs = by_id(microsoft365.fetch({}, None, {"access_token": "x"}))
    assert set(docs) == {"w1", "x1", "p1"}
    assert docs["w1"].pages == [(1, "Plan.docx.pdf: converted")] and docs["w1"].allowed == ["ana@contoso.com"]
    assert docs["x1"].table_file == ("Budget.xlsx", b"raw") and docs["x1"].allowed is None
    assert docs["p1"].pages == [(1, "Q3.pdf: raw")] and docs["p1"].allowed is None
    with pytest.raises(ValueError):
        list(microsoft365.fetch({"site": "evil.com/sites/x"}, None, {"access_token": "x"}))


def test_oauth_basic_auth_and_kept_token_fields(http, monkeypatch):
    seen = {}

    def token(req):
        seen["auth"], seen["body"] = req.headers.get("authorization"), req.content.decode()
        return {"access_token": "new", "instance_url": "https://acme.my.salesforce.com"}

    http({("POST", quickbooks.OAUTH["token_url"]): token, ("POST", salesforce.OAUTH["token_url"]): token})
    monkeypatch.setattr(oauth, "_app", lambda provider: ("cid", "secret"))
    creds = oauth._exchange("quickbooks", {"grant_type": "refresh_token", "refresh_token": "r"})
    assert seen["auth"] == "Basic " + base64.b64encode(b"cid:secret").decode() and "client_secret" not in seen["body"]
    assert "instance_url" not in creds  # only kept when the provider's spec asks for it
    creds = oauth._exchange("salesforce", {"grant_type": "authorization_code", "code": "c"})
    assert creds["instance_url"] == "https://acme.my.salesforce.com" and "expires_at" in creds  # spec default expiry


def test_refresh_keeps_realm_and_refresh_token(monkeypatch):
    from app.db import connect
    from app.security import encrypt

    old = {"access_token": "old", "refresh_token": "r1", "realm_id": "123", "expires_at": "2020-01-01T00:00:00+00:00"}
    with connect() as conn:
        cid = conn.execute("INSERT INTO connections (kind, name, config, credentials) VALUES ('quickbooks', 'pytest-qb', '{}', %s) "
                           "RETURNING id", (encrypt(old),)).fetchone()[0]
    try:
        monkeypatch.setattr(oauth, "_exchange", lambda kind, form: {"access_token": "new", "expires_at": "2099-01-01T00:00:00+00:00"})
        fresh = oauth.load_credentials(cid)
        assert fresh["access_token"] == "new" and fresh["realm_id"] == "123" and fresh["refresh_token"] == "r1"
        assert oauth.load_credentials(cid)["access_token"] == "new"  # re-saved
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (cid,))


def test_business_sync_loads_queryable_tables(http):
    from app.db import connect

    charge = {"id": "ch_1", "amount": 2000, "currency": "usd", "paid": True, "status": "succeeded", "created": 1793000000}
    http({("GET", stripe.API + "charges"): {"data": [charge], "has_more": False},
          ("GET", stripe.API): {"data": [], "has_more": False}})
    conn_id = connectors.create("stripe", "pytest-stripe", {}, creds={"access_token": "rk_x"})
    try:
        connectors.sync(conn_id)
        with connect() as conn:
            status, err = conn.execute("SELECT status, error FROM connections WHERE id = %s", (conn_id,)).fetchone()
            name, rows = conn.execute("SELECT name, row_count FROM data_tables WHERE connection_id = %s AND title = 'Stripe charges.csv'",
                                      (conn_id,)).fetchone()
            amount = conn.execute(f'SELECT amount FROM data."{name}"').fetchone()[0]
        assert err is None and status.startswith("ok") and rows == 1 and float(amount) == 20.0
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM connections WHERE id = %s", (conn_id,))
