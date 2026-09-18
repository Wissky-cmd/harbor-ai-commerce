"""Business rules are deterministic; models never set prices or mutate orders."""
import math
import re
from collections import Counter
from datetime import date
from decimal import Decimal, ROUND_UP
from fastapi import HTTPException

STAGES = ["sampling", "production", "inspection", "shipping", "delivered", "closed"]
STAGE_NAMES = dict(zip(STAGES, ["打样确认", "大货生产", "质量检验", "物流出运", "已交付", "已结清"]))
DOC_ACCESS = {
    "admin": {"viewer", "sales", "operations", "admin"},
    "sales": {"viewer", "sales"},
    "operations": {"viewer", "operations"},
    "viewer": {"viewer"},
}
AGENTS = [
    {"id": "buyer", "name": "AI 买手", "desc": "从销量、增长与库存中发现选品机会", "tool": "rank_products", "roles": ["admin", "sales"]},
    {"id": "sales", "name": "AI 业务员", "desc": "识别客户机会，准备有依据的跟进建议", "tool": "customer_opportunities", "roles": ["admin", "sales"]},
    {"id": "merchandiser", "name": "AI 跟单员", "desc": "检查生产节点，提前识别交期风险", "tool": "delivery_risks", "roles": ["admin", "operations", "sales"]},
    {"id": "quote", "name": "AI 报价助手", "desc": "校验成本构成与毛利审批规则", "tool": "quote_review", "roles": ["admin", "sales"]},
    {"id": "support", "name": "AI 客服", "desc": "检索企业 SOP，生成带来源的答复素材", "tool": "knowledge_search", "roles": ["admin", "sales", "operations", "viewer"]},
    {"id": "analyst", "name": "AI 经营分析", "desc": "核算订单金额、毛利和待收款", "tool": "business_metrics", "roles": ["admin"]},
    {"id": "retro", "name": "AI 复盘助手", "desc": "汇总履约异常，形成待验证的改进建议", "tool": "exception_review", "roles": ["admin", "operations"]},
]


def owned(conn, table, key, user):
    if table not in {"products", "customers", "orders", "quotes", "documents", "agent_runs"}:
        raise ValueError("Unknown resource")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND tenant_id=?", (key, user["tenant_id"])).fetchone()
    if not row:
        raise HTTPException(404, "记录不存在")
    return dict(row)


def money(value):
    return str(Decimal(value).quantize(Decimal("0.01")))


def calculate_quote(product, body):
    if body.quantity < product["moq"]:
        raise HTTPException(422, f"采购数量不得低于起订量 {product['moq']}")
    factory = Decimal(product["cost_cny"])
    base = (factory + body.packaging_cny + body.domestic_cny) / body.fx_rate
    freight = body.freight_usd if body.incoterm == "DDP" else Decimal(0)
    duty = (base + freight) * body.duty_rate if body.incoterm == "DDP" else Decimal(0)
    cost = base + freight + duty
    price = (cost / (1 - body.margin)).quantize(Decimal("0.01"), rounding=ROUND_UP)
    return {
        "unit_price": str(price), "total": money(price * body.quantity),
        "unit_cost_usd": str(cost), "actual_margin": str((price-cost)/price),
        "currency": "USD", "incoterm": body.incoterm,
        "components": [
            {"name": "面辅料及加工成本", "value": money(factory/body.fx_rate)},
            {"name": "包装", "value": money(body.packaging_cny/body.fx_rate)},
            {"name": "国内物流", "value": money(body.domestic_cny/body.fx_rate)},
            {"name": "国际运费", "value": money(freight)},
            {"name": "估算关税", "value": money(duty)},
        ],
        "inputs": body.model_dump(mode="json"),
        "requires_admin": body.margin < Decimal("0.25"),
        "warnings": ["汇率与关税为手工输入，发送前须人工核实。", "DDP 使用成本加运费作为简化计税基础，未计进口增值税及目的地杂费，非报关报价。"] if body.incoterm == "DDP" else ["FOB 不包含国际运费、进口关税及目的地费用。"],
    }


def risk_for(order):
    days = (date.fromisoformat(order["due_date"]) - date.today()).days
    if order["stage"] in {"shipping", "delivered", "closed"}:
        return {"level": "normal", "label": "正常", "reason": "已出运或完成交付", "days": days}
    if days < 0:
        return {"level": "high", "label": "已逾期", "reason": f"超过约定出运日 {abs(days)} 天，尚未出运", "days": days}
    if days <= 7 and order["progress"] < 80:
        return {"level": "high", "label": "高风险", "reason": f"距出运 {days} 天，生产进度仅 {order['progress']}%", "days": days}
    if days <= 3:
        return {"level": "medium", "label": "需关注", "reason": f"距出运 {days} 天，请确认验货与订舱", "days": days}
    return {"level": "normal", "label": "正常", "reason": "未触发交期预警规则", "days": days}


def orders_list(conn, user):
    rows = conn.execute("""SELECT o.*,c.name customer_name,c.country,p.name product_name,p.sku,p.color
        FROM orders o JOIN customers c ON o.customer_id=c.id AND o.tenant_id=c.tenant_id
        JOIN products p ON o.product_id=p.id AND o.tenant_id=p.tenant_id
        WHERE o.tenant_id=? ORDER BY o.due_date,o.number""", (user["tenant_id"],)).fetchall()
    return [{**dict(row), "risk": risk_for(row), "stage_name": STAGE_NAMES[row["stage"]]} for row in rows]


def metrics(conn, user):
    orders = orders_list(conn, user)
    total = sum((Decimal(o["total"]) for o in orders), Decimal(0))
    paid = sum((Decimal(o["paid"]) for o in orders), Decimal(0))
    cost = sum((Decimal(o["unit_cost_usd"])*o["quantity"] for o in orders), Decimal(0))
    by_channel = {}
    for order in orders:
        by_channel[order["channel"]] = by_channel.get(order["channel"], Decimal(0)) + Decimal(order["total"])
    return {"order_value": money(total), "received": money(paid), "receivable": money(total-paid),
            "estimated_profit": money(total-cost), "margin": float((total-cost)/total) if total else 0,
            "order_count": len(orders), "active_orders": sum(o["stage"] not in {"delivered", "closed"} for o in orders),
            "at_risk": sum(o["risk"]["level"] == "high" for o in orders),
            "channels": [{"name": name, "value": float(value)} for name, value in by_channel.items()],
            "funnel": [{"stage": s, "name": STAGE_NAMES[s], "count": sum(o["stage"] == s for o in orders)} for s in STAGES],
            "definition": "全部已录入订单的合同金额；估算毛利按订单成本快照计算，不等同财务确认收入或净利润。"}


def tokens(text):
    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    out = []
    for part in parts:
        if re.match(r"[\u4e00-\u9fff]", part):
            out.extend(part[i:i+2] for i in range(len(part)-1))
            if len(part) == 1:
                out.append(part)
        else:
            out.append(part)
    return Counter(out)


def visible_documents(conn, user):
    rows = conn.execute("SELECT * FROM documents WHERE tenant_id=? ORDER BY created_at DESC", (user["tenant_id"],)).fetchall()
    return [dict(r) for r in rows if r["min_role"] in DOC_ACCESS[user["role"]]]


def retrieve(conn, user, question, limit=4):
    """Permission-filter first, then Chinese bigram lexical retrieval (no vector claims)."""
    docs = visible_documents(conn, user)
    chunks = []
    for doc in docs:
        for offset in range(0, len(doc["content"]), 420):
            chunk = doc["content"][offset:offset+500]
            chunks.append({"document_id": doc["id"], "title": doc["title"], "version": doc["version"],
                           "chunk_id": f"{doc['id']}:{offset}", "excerpt": chunk, "terms": tokens(doc["title"]+" "+chunk)})
    query = tokens(question)
    freq = Counter(t for chunk in chunks for t in chunk["terms"])
    ranked = []
    for chunk in chunks:
        terms = chunk.pop("terms")
        score = sum((1+math.log(terms[t]))*math.log(1+len(chunks)/(1+freq[t])) for t in query if t in terms)
        if score > 0:
            ranked.append({**chunk, "score": round(score, 4)})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]
