"""SQLite unit of work, schema and repeatable development fixtures."""
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid(prefix):
    return f"{prefix}_{secrets.token_hex(8)}"


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()
    return f"{salt}${value}"


def verify_password(password, stored):
    return secrets.compare_digest(password_hash(password, stored.split("$")[0]), stored)


@contextmanager
def connection():
    path = Path(os.getenv("DATABASE_PATH", "data/harbor.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
 name TEXT NOT NULL, role TEXT NOT NULL, password TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, reset_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL,
 category TEXT NOT NULL, material TEXT NOT NULL, color TEXT NOT NULL, stock INTEGER NOT NULL,
 cost_cny TEXT NOT NULL, price_usd TEXT NOT NULL, moq INTEGER NOT NULL,
 sales_30d INTEGER NOT NULL, growth REAL NOT NULL, lead_days INTEGER NOT NULL,
 UNIQUE(tenant_id,sku));
CREATE TABLE IF NOT EXISTS customers(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL, country TEXT NOT NULL,
 channel TEXT NOT NULL, email TEXT NOT NULL, stage TEXT NOT NULL, total_usd TEXT NOT NULL,
 last_contact TEXT NOT NULL, owner TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, product_id TEXT NOT NULL REFERENCES products(id),
 customer_id TEXT NOT NULL REFERENCES customers(id), quantity INTEGER NOT NULL,
 unit_price TEXT NOT NULL, total TEXT NOT NULL, margin TEXT NOT NULL, currency TEXT NOT NULL,
 breakdown TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL,
 approved_by TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, number TEXT NOT NULL,
 customer_id TEXT NOT NULL REFERENCES customers(id), product_id TEXT NOT NULL REFERENCES products(id),
 quote_id TEXT UNIQUE REFERENCES quotes(id), quantity INTEGER NOT NULL, total TEXT NOT NULL,
 unit_cost_usd TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
 due_date TEXT NOT NULL, created_at TEXT NOT NULL, paid TEXT NOT NULL DEFAULT '0',
 version INTEGER NOT NULL DEFAULT 1, channel TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
 UNIQUE(tenant_id,number));
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
 content TEXT NOT NULL, min_role TEXT NOT NULL DEFAULT 'viewer', version INTEGER NOT NULL,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_runs(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, agent TEXT NOT NULL, question TEXT NOT NULL,
 status TEXT NOT NULL, result TEXT NOT NULL, trace TEXT NOT NULL,
 model_mode TEXT NOT NULL, duration_ms INTEGER NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(
 id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
 entity_id TEXT NOT NULL, detail TEXT NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS webhook_events(
 tenant_id TEXT NOT NULL, event_id TEXT NOT NULL, topic TEXT NOT NULL, received_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,event_id));
CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id,stage);
CREATE INDEX IF NOT EXISTS idx_runs_tenant ON agent_runs(tenant_id,created_at);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_logs(tenant_id,created_at);
CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents(tenant_id);
"""


def audit(conn, user, action, entity_id, detail=None, request_id="system"):
    conn.execute("INSERT INTO audit_logs VALUES(?,?,?,?,?,?,?,?)", (
        uid("aud"), user["tenant_id"], user["id"], action, entity_id,
        json.dumps(detail or {}, ensure_ascii=False), request_id, now()))


def init_db():
    production = os.getenv("APP_ENV") == "production"
    demo = os.getenv("DEMO_MODE", "true").lower() == "true"
    admin_pw = os.getenv("ADMIN_PASSWORD", "")
    if production and (demo or len(admin_pw) < 16 or os.getenv("COOKIE_SECURE") != "true"):
        raise RuntimeError("Production requires DEMO_MODE=false, ADMIN_PASSWORD>=16 characters and COOKIE_SECURE=true")
    if not demo and len(admin_pw) < 16:
        raise RuntimeError("Set ADMIN_PASSWORD (at least 16 characters) when demo mode is disabled")
    with connection() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO schema_versions VALUES(1,?)", (now(),))
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
                "usr_admin", "harbor", "admin@harbor.local", "林予安", "admin",
                password_hash(admin_pw or "HarborDemo2026!")))
            if demo:
                for key, name, role in [("sales", "陈思远", "sales"), ("ops", "周可宁", "operations"), ("viewer", "访客", "viewer")]:
                    conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (
                        f"usr_{key}", "harbor", f"{key}@harbor.local", name, role, password_hash("HarborDemo2026!")))
        if demo and not conn.execute("SELECT 1 FROM products LIMIT 1").fetchone():
            seed(conn)


def seed(conn):
    today = date.today()
    products = [
        ("p_linen", "HB-LN-001", "法式亚麻混纺衬衫", "衬衫", "55% 亚麻 / 45% 棉", "sage", 2480, "68.50", "24.90", 100, 1260, 24.8, 18),
        ("p_knit", "HB-KN-002", "轻量美利奴针织开衫", "针织", "100% 美利奴羊毛", "sand", 860, "112.00", "39.00", 80, 640, 18.6, 25),
        ("p_dress", "HB-DR-003", "度假系带连衣裙", "连衣裙", "100% 有机棉", "rose", 1320, "89.00", "32.00", 120, 920, 32.1, 21),
        ("p_jacket", "HB-JK-004", "城市轻户外夹克", "外套", "再生聚酯 / 防泼水", "blue", 420, "158.00", "56.00", 60, 380, 42.5, 30),
        ("p_tee", "HB-TS-005", "重磅有机棉基础 T 恤", "T 恤", "100% 有机棉 240g", "lavender", 4200, "36.00", "14.50", 200, 2100, 12.4, 14),
        ("p_pants", "HB-PT-006", "宽松直筒休闲裤", "裤装", "98% 棉 / 2% 氨纶", "slate", 1750, "76.00", "28.00", 100, 870, -4.2, 20),
    ]
    for row in products:
        conn.execute("INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row[0], "harbor", *row[1:]))
    customers = [
        ("c_nord", "Nord & Co.", "德国", "Faire", "buying@nord.example", "复购客户", "84200", 3, "陈思远"),
        ("c_maison", "Maison June", "法国", "独立站", "hello@maison.example", "报价中", "46500", 8, "陈思远"),
        ("c_oak", "Oak & Tide", "美国", "Shopify", "sourcing@oak.example", "复购客户", "126800", 2, "林予安"),
        ("c_studio", "Studio Ecru", "英国", "展会", "team@ecru.example", "样品确认", "18300", 15, "周可宁"),
        ("c_hana", "Hana Market", "日本", "Faire", "buy@hana.example", "待唤醒", "32900", 62, "陈思远"),
    ]
    for row in customers:
        conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?,?,?,?)", (
            row[0], "harbor", *row[1:7], (today - timedelta(days=row[7])).isoformat(), row[8]))
    stages = [("c_nord", "p_linen", 800, "19920", "10.20", "production", 68, 5, "9960", "Faire"),
              ("c_oak", "p_jacket", 360, "20160", "23.50", "inspection", 92, 2, "10080", "Shopify"),
              ("c_maison", "p_dress", 600, "19200", "13.20", "sampling", 25, 22, "5760", "独立站"),
              ("c_studio", "p_knit", 240, "9360", "16.40", "production", 40, -2, "2808", "展会"),
              ("c_hana", "p_tee", 1200, "17400", "5.40", "shipping", 100, 8, "17400", "Faire"),
              ("c_oak", "p_pants", 500, "14000", "11.20", "delivered", 100, -8, "14000", "Shopify")]
    for i, (customer, product, qty, total, cost, stage, progress, due, paid, channel) in enumerate(stages):
        conn.execute("INSERT INTO orders(id,tenant_id,number,customer_id,product_id,quantity,total,unit_cost_usd,stage,progress,due_date,created_at,paid,channel) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            f"o_{i+1}", "harbor", f"HB-{today:%y%m}-{1048+i}", customer, product, qty, total, cost, stage, progress,
            (today + timedelta(days=due)).isoformat(), (today - timedelta(days=28-i*4)).isoformat(), paid, channel))
    docs = [
        ("doc_quote", "外贸报价与毛利审批 SOP", "报价规则", "报价以人民币成本为基础，汇率采用人民币/美元。FOB 单件成本包括面料、加工、包装、国内物流与分摊杂费；DDP 还须加入国际运费、按指定计税基础计算的关税和目的地费用。本系统 DDP 为简化估算，不包含进口增值税。报价=美元成本/(1-目标毛利率)。毛利率低于 25% 必须由管理员审批，所有对客报价在发送前都需要人工确认。汇率和税率由业务人员提供并核实，系统不提供实时税率。", "sales"),
        ("doc_delivery", "生产跟单与交期异常处理", "履约管理", "订单经过打样 sampling、生产 production、验货 inspection、出运 shipping、交付 delivered、回款结清 closed。生产进度低于 80% 且距离交期不超过 7 天，标记高风险；未出运且已超过交期，标记逾期。高风险订单需确认面辅料到位情况、工厂产能与验货档期，制定补救计划；未经负责人批准，不向客户承诺新的交期。", "viewer"),
        ("doc_quality", "服装验货 AQL 与样品标准", "质量标准", "大货生产前应完成产前样确认、尺寸表和面辅料封样。演示质量标准采用 AQL 2.5（主要缺陷）和 AQL 4.0（次要缺陷），实际标准以客户合同为准。验货重点包括色差、缩水率、缝线、尺寸偏差与标签合规。验货不通过不得进入出运环节，应记录整改原因并复验。", "viewer"),
        ("doc_customer", "小 B 客户跟进与唤醒手册", "客户运营", "首次询盘应收集销售国家、渠道、目标零售价、采购数量及交期。向客户推荐产品时必须核对 MOQ、库存和供货周期。超过 45 天未联系的历史客户进入唤醒候选名单。建议根据历史采购推荐 2 至 3 款相关产品，先生成邮件草稿，由业务员审核后发送。不得自动外发或承诺未经确认的折扣。", "sales"),
        ("doc_tax", "出口退税资料归档清单", "财务合规", "出口退税准备资料包括销售合同、出口报关单、增值税进项发票、物流运输凭证和收汇证明。单证金额、商品编码、数量与主体需要一致。具体税率、申报资格及期限以主管税务机关最新政策和财务人员核定为准。系统只记录准备状态，不进行自动申报。", "admin"),
    ]
    for key, title, cat, content, role in docs:
        conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", (key, "harbor", title, cat, content, role, 1, now()))
