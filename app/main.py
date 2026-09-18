import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import model_summary, run_agent
from .db import audit, connection, init_db, now, uid, verify_password
from .schemas import AgentInput, CustomerInput, KnowledgeInput, Login, OrderTransition, Payment, QuoteInput, SearchInput
from .services import AGENTS, STAGES, calculate_quote, metrics, money, orders_list, owned, retrieve, visible_documents

LOGGER = logging.getLogger("harbor")
ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Harbor OS · 跨境电商 AI 工作台", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = secrets.token_hex(12)
    start = time.perf_counter()
    origin = request.headers.get("origin")
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and origin and urlparse(origin).netloc != request.url.netloc:
        return JSONResponse({"detail": "拒绝跨站写入请求"}, status_code=403)
    declared = request.headers.get("content-length", "0")
    if not declared.isdigit() or int(declared) > 1_000_000:
        return JSONResponse({"detail": "请求体过大或格式错误"}, status_code=413)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1_000_000:
                return JSONResponse({"detail": "请求体不能超过 1 MB"}, status_code=413)
        request._body = bytes(body)
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception("request_failed request_id=%s", request.state.request_id)
        response = JSONResponse({"detail": "服务暂时不可用", "request_id": request.state.request_id}, status_code=500)
    response.headers.update({"X-Request-ID": request.state.request_id,
                             "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "same-origin", "Cache-Control": "no-store"})
    if not request.url.path.startswith(("/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    LOGGER.info("request id=%s method=%s path=%s status=%s duration_ms=%d", request.state.request_id, request.method, request.url.path, response.status_code, int((time.perf_counter()-start)*1000))
    return response


def current_user(request: Request):
    token = request.cookies.get("harbor_session", "")
    if not token:
        raise HTTPException(401, "请先登录")
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connection() as conn:
        row = conn.execute("SELECT u.id,u.tenant_id,u.email,u.name,u.role FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token_hash=? AND s.expires_at>?", (digest, now())).fetchone()
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    return dict(row)


def roles(*allowed):
    def check(user=Depends(current_user)):
        if user["role"] not in allowed:
            raise HTTPException(403, "当前角色没有操作权限")
        return user
    return check


@app.get("/api/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": app.version}


@app.get("/api/config")
def config():
    return {"demo": os.getenv("DEMO_MODE", "true").lower() == "true", "llm": "ollama" if os.getenv("OLLAMA_URL") else "rules"}


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response):
    key = hashlib.sha256(((request.client.host if request.client else "local")+":"+body.email.lower()).encode()).hexdigest()
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM login_attempts WHERE reset_at<?", (now(),))
        attempt = conn.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
        if attempt and attempt["count"] >= 8:
            raise HTTPException(429, "登录尝试过多，请 15 分钟后重试")
        row = conn.execute("SELECT * FROM users WHERE email=?", (body.email.lower(),)).fetchone()
        valid = row is not None and verify_password(body.password, row["password"])
        if not valid:
            reset = (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1", (key, reset))
            conn.commit()
            raise HTTPException(401, "邮箱或密码错误")
        conn.execute("DELETE FROM login_attempts WHERE key=?", (key,))
        conn.execute("DELETE FROM sessions WHERE expires_at<?", (now(),))
        token = secrets.token_urlsafe(48)
        expires = (datetime.now(timezone.utc)+timedelta(hours=int(os.getenv("SESSION_HOURS", "8")))).isoformat(timespec="seconds")
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], expires))
        audit(conn, row, "auth.login", row["id"], request_id=request.state.request_id)
        user = {k: row[k] for k in ["id", "tenant_id", "email", "name", "role"]}
    response.set_cookie("harbor_session", token, httponly=True, secure=os.getenv("COOKIE_SECURE") == "true", samesite="strict", max_age=int(os.getenv("SESSION_HOURS", "8"))*3600)
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(request.cookies.get("harbor_session", "").encode()).hexdigest(),))
    response.delete_cookie("harbor_session")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/dashboard")
def dashboard(user=Depends(current_user)):
    with connection() as conn:
        result = metrics(conn, user)
        result["orders"] = orders_list(conn, user)
        result["recent_runs"] = [dict(r) for r in conn.execute("SELECT id,agent,status,model_mode,duration_ms,created_at FROM agent_runs WHERE tenant_id=? AND created_by=? ORDER BY created_at DESC LIMIT 5", (user["tenant_id"], user["id"]))]
        result["products"] = [dict(r) for r in conn.execute("SELECT id,name,sku,color,growth,sales_30d FROM products WHERE tenant_id=? ORDER BY growth DESC LIMIT 3", (user["tenant_id"],))]
        if user["role"] != "admin":
            for key in ["margin", "estimated_profit", "received", "receivable"]:
                result.pop(key, None)
            for order in result["orders"]:
                order.pop("unit_cost_usd", None)
        return result


@app.get("/api/products")
def products(user=Depends(current_user)):
    with connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM products WHERE tenant_id=? ORDER BY sales_30d DESC", (user["tenant_id"],))]
    if user["role"] not in {"admin", "sales"}:
        for row in rows:
            row.pop("cost_cny", None)
    return rows


@app.get("/api/customers")
def customers(user=Depends(roles("admin", "sales", "operations"))):
    with connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM customers WHERE tenant_id=? ORDER BY total_usd*1 DESC", (user["tenant_id"],))]


@app.post("/api/customers", status_code=201)
def create_customer(body: CustomerInput, request: Request, user=Depends(roles("admin", "sales"))):
    key = uid("cus")
    with connection() as conn:
        conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?,?)", (key, user["tenant_id"], body.name, body.country, body.channel, body.email, "新线索", "0", date.today().isoformat(), user["name"]))
        audit(conn, user, "customer.create", key, request_id=request.state.request_id)
    return {"id": key}


@app.post("/api/customers/{key}/contact")
def contact_customer(key: str, request: Request, user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        owned(conn, "customers", key, user)
        conn.execute("UPDATE customers SET last_contact=? WHERE id=? AND tenant_id=?", (date.today().isoformat(), key, user["tenant_id"]))
        audit(conn, user, "customer.contact_recorded", key, request_id=request.state.request_id)
    return {"ok": True, "message": "仅记录已完成的联系，不发送消息"}


@app.get("/api/orders")
def orders(user=Depends(current_user)):
    with connection() as conn:
        rows = orders_list(conn, user)
    if user["role"] != "admin":
        for row in rows:
            row.pop("unit_cost_usd", None)
    return rows


@app.patch("/api/orders/{key}")
def transition(key: str, body: OrderTransition, request: Request, user=Depends(roles("admin", "operations"))):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = owned(conn, "orders", key, user)
        if order["version"] != body.version:
            raise HTTPException(409, "订单已被更新，请刷新后重试")
        current, target = STAGES.index(order["stage"]), STAGES.index(body.stage)
        if target not in {current, current+1} or order["stage"] == "closed":
            raise HTTPException(422, "仅允许更新当前节点或流转至下一节点")
        if body.progress < order["progress"]:
            raise HTTPException(422, "累计生产进度不可倒退")
        if target >= STAGES.index("inspection") and body.progress != 100:
            raise HTTPException(422, "验货及后续环节要求累计生产进度达到 100%")
        if body.stage == "shipping" and order["stage"] != "shipping" and not body.quality_passed:
            raise HTTPException(422, "出运前必须确认验货通过")
        if body.stage == "closed" and Decimal(order["paid"]) < Decimal(order["total"]):
            raise HTTPException(422, "货款未结清，不能关闭订单")
        conn.execute("UPDATE orders SET stage=?,progress=?,notes=?,version=version+1 WHERE id=? AND tenant_id=?", (body.stage, body.progress, body.note, key, user["tenant_id"]))
        audit(conn, user, "order.transition", key, {"from": order["stage"], **body.model_dump()}, request.state.request_id)
    return {"ok": True, "version": body.version+1}


@app.post("/api/orders/{key}/payments")
def payment(key: str, body: Payment, request: Request, user=Depends(roles("admin"))):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        order = owned(conn, "orders", key, user)
        if body.version != order["version"]:
            raise HTTPException(409, "订单已更新；本次收款未入账，请刷新后核对")
        paid = Decimal(order["paid"]) + body.amount
        if paid > Decimal(order["total"]):
            raise HTTPException(422, "收款不能超过订单总额")
        conn.execute("UPDATE orders SET paid=?,version=version+1 WHERE id=? AND tenant_id=?", (money(paid), key, user["tenant_id"]))
        audit(conn, user, "payment.record", key, {"amount": str(body.amount), "paid": money(paid)}, request.state.request_id)
    return {"ok": True, "paid": money(paid)}


@app.post("/api/quotes/preview")
def preview_quote(body: QuoteInput, user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        product = owned(conn, "products", body.product_id, user)
        owned(conn, "customers", body.customer_id, user)
        return calculate_quote(product, body)


@app.post("/api/quotes", status_code=201)
def create_quote(body: QuoteInput, request: Request, user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        product = owned(conn, "products", body.product_id, user)
        owned(conn, "customers", body.customer_id, user)
        result = calculate_quote(product, body)
        key = uid("quo")
        conn.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (key, user["tenant_id"], body.product_id, body.customer_id, body.quantity, result["unit_price"], result["total"], str(body.margin), "USD", json.dumps(result, ensure_ascii=False), "pending", user["id"], None, now()))
        audit(conn, user, "quote.create", key, {"total": result["total"], "margin": str(body.margin)}, request.state.request_id)
    return {"id": key, **result, "status": "pending"}


@app.get("/api/quotes")
def quotes(user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        return [dict(r) for r in conn.execute("SELECT q.*,p.name product_name,c.name customer_name FROM quotes q JOIN products p ON q.product_id=p.id AND q.tenant_id=p.tenant_id JOIN customers c ON q.customer_id=c.id AND q.tenant_id=c.tenant_id WHERE q.tenant_id=? ORDER BY q.created_at DESC", (user["tenant_id"],))]


@app.post("/api/quotes/{key}/approve")
def approve_quote(key: str, request: Request, user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        quote = owned(conn, "quotes", key, user)
        if Decimal(quote["margin"]) < Decimal("0.25") and user["role"] != "admin":
            raise HTTPException(403, "低毛利报价必须由管理员审批")
        if quote["status"] == "converted":
            raise HTTPException(409, "报价已转订单")
        if quote["status"] == "approved":
            return {"ok": True, "status": "approved"}
        conn.execute("UPDATE quotes SET status='approved',approved_by=? WHERE id=? AND tenant_id=?", (user["id"], key, user["tenant_id"]))
        audit(conn, user, "quote.approve", key, request_id=request.state.request_id)
    return {"ok": True, "status": "approved"}


@app.post("/api/quotes/{key}/convert", status_code=201)
def convert_quote(key: str, request: Request, user=Depends(roles("admin", "sales"))):
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        quote = owned(conn, "quotes", key, user)
        existing = conn.execute("SELECT id FROM orders WHERE quote_id=? AND tenant_id=?", (key, user["tenant_id"])).fetchone()
        if existing:
            return {"id": existing["id"], "reused": True}
        if quote["status"] != "approved":
            raise HTTPException(422, "报价审批通过后才能创建订单")
        product = owned(conn, "products", quote["product_id"], user)
        customer = owned(conn, "customers", quote["customer_id"], user)
        order_id = uid("ord")
        breakdown = json.loads(quote["breakdown"])
        conn.execute("INSERT INTO orders(id,tenant_id,number,customer_id,product_id,quote_id,quantity,total,unit_cost_usd,stage,progress,due_date,created_at,channel) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            order_id, user["tenant_id"], f"HB-{date.today():%y%m}-{secrets.token_hex(3).upper()}", quote["customer_id"], quote["product_id"], key, quote["quantity"], quote["total"], breakdown["unit_cost_usd"], "sampling", 0, (date.today()+timedelta(days=product["lead_days"]+7)).isoformat(), now(), customer["channel"]))
        conn.execute("UPDATE quotes SET status='converted' WHERE id=? AND tenant_id=?", (key, user["tenant_id"]))
        audit(conn, user, "quote.convert", order_id, {"quote_id": key}, request.state.request_id)
    return {"id": order_id, "reused": False}


@app.get("/api/knowledge")
def knowledge(user=Depends(current_user)):
    with connection() as conn:
        return visible_documents(conn, user)


@app.post("/api/knowledge", status_code=201)
def add_knowledge(body: KnowledgeInput, request: Request, user=Depends(roles("admin"))):
    key = uid("doc")
    with connection() as conn:
        conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", (key, user["tenant_id"], body.title, body.category, body.content, body.min_role, 1, now()))
        audit(conn, user, "knowledge.create", key, {"title": body.title, "min_role": body.min_role}, request.state.request_id)
    return {"id": key}


@app.post("/api/knowledge/search")
async def search_knowledge(body: SearchInput, request: Request, user=Depends(current_user)):
    with connection() as conn:
        sources = retrieve(conn, user, body.question)
        audit(conn, user, "knowledge.search", "knowledge", {"source_ids": [s["document_id"] for s in sources]}, request.state.request_id)
    answer, mode, error = await model_summary(body.question, {"sources": sources}) if sources else (None, "rules", None)
    return {"sources": sources, "answer": answer or ("检索到相关知识，以下为原文证据。" if sources else "当前权限下未找到相关知识，无法可靠回答。"), "model_mode": mode, "model_error": error}


@app.get("/api/agents")
def agents(user=Depends(current_user)):
    return [{**a, "available": user["role"] in a["roles"]} for a in AGENTS]


@app.post("/api/agents/run")
async def agent_run(body: AgentInput, request: Request, user=Depends(current_user)):
    return await run_agent(user, body, request.state.request_id)


@app.get("/api/agents/runs")
def agent_runs(user=Depends(current_user)):
    with connection() as conn:
        rows = conn.execute("SELECT * FROM agent_runs WHERE tenant_id=? AND created_by=? ORDER BY created_at DESC LIMIT 30", (user["tenant_id"], user["id"])).fetchall()
    return [{**dict(r), "result": json.loads(r["result"]), "trace": json.loads(r["trace"])} for r in rows]


@app.get("/api/audit")
def audit_logs(user=Depends(roles("admin"))):
    with connection() as conn:
        return [dict(r) for r in conn.execute("SELECT a.*,u.name actor_name FROM audit_logs a LEFT JOIN users u ON a.actor=u.id AND a.tenant_id=u.tenant_id WHERE a.tenant_id=? ORDER BY a.created_at DESC,a.rowid DESC LIMIT 100", (user["tenant_id"],))]


@app.get("/api/integrations")
def integrations(user=Depends(roles("admin"))):
    return [
        {"name": "Shopify", "status": "configured" if os.getenv("SHOPIFY_WEBHOOK_SECRET") and os.getenv("SHOPIFY_SHOP_DOMAIN") else "not_configured", "detail": "商品更新 Webhook：HMAC 验签、店铺校验、事件去重、更新已有 SKU 的售价；不导入订单。"},
        {"name": "Ollama", "status": "configured" if os.getenv("OLLAMA_URL") else "not_configured", "detail": "可选模型摘要；未配置时使用规则与词项检索。已配置不代表连接健康。"},
        *[{"name": name, "status": "planned", "detail": "预留适配器边界；当前版本尚未实现真实连接。"} for name in ["ERP / WMS", "Faire", "企业微信", "邮件"]],
    ]


@app.post("/api/webhooks/shopify")
async def shopify_hook(request: Request):
    import base64
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET")
    shop = os.getenv("SHOPIFY_SHOP_DOMAIN")
    if not secret or not shop:
        raise HTTPException(503, "Shopify Webhook 未配置")
    raw = await request.body()
    signature = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
    if not secrets.compare_digest(signature, request.headers.get("x-shopify-hmac-sha256", "")):
        raise HTTPException(401, "Webhook 签名无效")
    if request.headers.get("x-shopify-shop-domain") != shop:
        raise HTTPException(403, "店铺不匹配")
    event = request.headers.get("x-shopify-webhook-id", "")
    topic = request.headers.get("x-shopify-topic", "")
    if not event or len(event) > 200 or topic != "products/update":
        raise HTTPException(422, "需要事件 ID；当前仅支持 products/update")
    try:
        payload = json.loads(raw)
        variants = payload["variants"]
        if not isinstance(variants, list) or len(variants) > 250:
            raise ValueError()
        updates = []
        for variant in variants:
            price = Decimal(str(variant["price"]))
            if not price.is_finite() or price <= 0 or price > 1000000 or not isinstance(variant["sku"], str):
                raise ValueError()
            updates.append((money(price), variant["sku"]))
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise HTTPException(422, "商品数据格式错误")
    tenant = os.getenv("SHOPIFY_TENANT_ID", "harbor")
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM webhook_events WHERE tenant_id=? AND event_id=?", (tenant, event)).fetchone():
            return {"ok": True, "duplicate": True}
        count = 0
        for price, sku in updates:
            count += conn.execute("UPDATE products SET price_usd=? WHERE tenant_id=? AND sku=?", (price, tenant, sku)).rowcount
        conn.execute("INSERT INTO webhook_events VALUES(?,?,?,?)", (tenant, event, topic, now()))
        audit(conn, {"id": "shopify", "tenant_id": tenant}, "webhook.products_update", event, {"matched": count}, request.state.request_id)
    return {"ok": True, "duplicate": False, "updated": count}


app.mount("/static", StaticFiles(directory=ROOT/"web"), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT/"web"/"index.html")
