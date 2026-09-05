"""A toy CRM the bot makes tool calls against, so the token/cost numbers reflect a
realistic support conversation rather than a single Q&A turn.

All dummy data - seeded once on startup. Product images are generated locally
(simple branded cards) so the bot has something real to send over WhatsApp.
"""

import os

import db

MEDIA_DIR = os.path.join(os.path.dirname(db.DB_PATH) or ".", "media")

CUSTOMERS = [
    # phone_number, name, plan, customer_since
    ("+32477874767", "Karan", "Enterprise", "2023-04-11"),
    ("+15551234567", "Priya Sharma", "Growth", "2024-09-02"),
    ("+447700900123", "Tom Baker", "Starter", "2025-12-19"),
]

PRODUCTS = [
    # sku, name, price_usd, description
    ("SMS-BULK", "Bulk SMS", 0.0075, "High-volume SMS delivery with global carrier routing. Priced per message."),
    ("WA-API", "WhatsApp Business API", 0.0135, "Official WhatsApp Business messaging with templates and 2-way chat."),
    ("VOICE-OTP", "Voice OTP", 0.0210, "Automated voice one-time-passcodes for login and payment verification."),
    ("RCS-PRO", "RCS Messaging", 0.0180, "Rich cards, carousels and branded sender for Android users."),
]

ORDERS = [
    # order_number, phone_number, sku, quantity, status, eta
    ("ORD-4412", "+32477874767", "WA-API", 250000, "in transit", "2026-09-05"),
    ("ORD-4413", "+32477874767", "SMS-BULK", 1000000, "delivered", "2026-08-28"),
    ("ORD-5120", "+15551234567", "VOICE-OTP", 50000, "processing", "2026-09-09"),
]

INVOICES = [
    # invoice_number, phone, period, amount_usd, status, due_date
    ("INV-20268", "+32477874767", "2026-08", 7500.00, "paid", "2026-09-01"),
    ("INV-20341", "+32477874767", "2026-09", 3375.00, "unpaid", "2026-10-01"),
    ("INV-20355", "+15551234567", "2026-09", 1050.00, "unpaid", "2026-10-01"),
]

USAGE_STATS = [
    # phone, month, channel, sent, delivered, failed
    ("+32477874767", "2026-09", "WhatsApp", 182400, 179933, 2467),
    ("+32477874767", "2026-09", "SMS", 44100, 42388, 1712),
    ("+32477874767", "2026-08", "WhatsApp", 210500, 207866, 2634),
    ("+32477874767", "2026-08", "SMS", 998000, 961072, 36928),
    ("+15551234567", "2026-09", "Voice OTP", 12800, 12203, 597),
]

COVERAGE = [
    # country, channel, rate_usd, notes
    ("India", "WhatsApp", 0.0088, "Marketing and utility templates billed separately"),
    ("India", "SMS", 0.0021, "DLT registration required"),
    ("United States", "WhatsApp", 0.0140, ""),
    ("United States", "SMS", 0.0079, "10DLC registration required"),
    ("Brazil", "WhatsApp", 0.0625, ""),
    ("United Kingdom", "SMS", 0.0410, ""),
    ("Indonesia", "WhatsApp", 0.0386, ""),
    ("Nigeria", "SMS", 0.0320, "Sender ID pre-registration required"),
]

INCIDENTS = [
    # reference, service, status, summary, started_at
    ("INC-881", "SMS - India routes", "monitoring",
     "Elevated latency on one upstream carrier; delivery delayed 3-8 min. Failover applied.",
     "2026-09-05 06:12"),
]

CATALOGUE_SEED = [
    # phone, item_name, category, body, status
    ("+32477874767", "order_shipped_v2", "utility",
     "Hi {{1}}, your order {{2}} has shipped and arrives by {{3}}.", "approved"),
    ("+32477874767", "otp_login", "authentication",
     "{{1}} is your verification code. It expires in 10 minutes.", "approved"),
]

_PALETTE = [(79, 70, 229), (5, 150, 105), (219, 39, 119), (217, 119, 6)]


def _generate_product_image(sku: str, name: str, price: float, index: int) -> str | None:
    """Draws a simple product card PNG so the bot has an image to send. Returns filename."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    os.makedirs(MEDIA_DIR, exist_ok=True)
    filename = f"product_{sku.lower()}.png"
    path = os.path.join(MEDIA_DIR, filename)
    if os.path.exists(path):
        return filename

    bg = _PALETTE[index % len(_PALETTE)]
    img = Image.new("RGB", (800, 600), bg)
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 760, 560], outline=(255, 255, 255), width=3)
    draw.text((80, 200), name, fill=(255, 255, 255))
    draw.text((80, 260), f"${price:.4f} per message", fill=(255, 255, 255))
    draw.text((80, 320), sku, fill=(230, 230, 230))
    img.save(path)
    return filename


def seed() -> None:
    """Idempotent - only fills tables that are empty."""
    with db.get_conn() as conn:
        if conn.execute("SELECT COUNT(*) AS n FROM crm_customers").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_customers (phone_number, name, plan, customer_since) VALUES (?, ?, ?, ?)",
                CUSTOMERS,
            )
        if conn.execute("SELECT COUNT(*) AS n FROM crm_products").fetchone()["n"] == 0:
            rows = []
            for i, (sku, name, price, desc) in enumerate(PRODUCTS):
                rows.append((sku, name, price, desc, _generate_product_image(sku, name, price, i)))
            conn.executemany(
                "INSERT INTO crm_products (sku, name, price_usd, description, image_file) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        if conn.execute("SELECT COUNT(*) AS n FROM crm_orders").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_orders (order_number, phone_number, sku, quantity, status, eta) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ORDERS,
            )
        if conn.execute("SELECT COUNT(*) AS n FROM crm_invoices").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_invoices (invoice_number, phone_number, period, amount_usd, status, due_date) "
                "VALUES (?, ?, ?, ?, ?, ?)", INVOICES)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_usage_stats").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_usage_stats (phone_number, month, channel, sent, delivered, failed) "
                "VALUES (?, ?, ?, ?, ?, ?)", USAGE_STATS)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_coverage").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_coverage (country, channel, rate_usd, notes) VALUES (?, ?, ?, ?)", COVERAGE)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_incidents").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_incidents (reference, service, status, summary, started_at) "
                "VALUES (?, ?, ?, ?, ?)", INCIDENTS)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_catalogue").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_catalogue (phone_number, item_name, category, body, status) "
                "VALUES (?, ?, ?, ?, ?)", CATALOGUE_SEED)


def lookup_customer(phone_number: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT name, plan, customer_since FROM crm_customers WHERE phone_number = ?",
            (phone_number,),
        ).fetchone()
    return dict(row) if row else None


def list_orders(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT o.order_number, o.quantity, o.status, o.eta, p.name AS product "
            "FROM crm_orders o LEFT JOIN crm_products p ON p.sku = o.sku "
            "WHERE o.phone_number = ? ORDER BY o.id DESC",
            (phone_number,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_order(order_number: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT o.order_number, o.quantity, o.status, o.eta, o.phone_number, p.name AS product "
            "FROM crm_orders o LEFT JOIN crm_products p ON p.sku = o.sku "
            "WHERE o.order_number = ?",
            (order_number.strip().upper(),),
        ).fetchone()
    return dict(row) if row else None


def list_products() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT sku, name, price_usd, description, image_file FROM crm_products ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_product(sku: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sku, name, price_usd, description, image_file FROM crm_products WHERE sku = ?",
            (sku.strip().upper(),),
        ).fetchone()
    return dict(row) if row else None


def create_ticket(phone_number: str, summary: str, priority: str = "normal") -> str:
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM crm_tickets").fetchone()["n"]
        ticket_number = f"TKT-{7000 + count + 1}"
        conn.execute(
            "INSERT INTO crm_tickets (ticket_number, phone_number, summary, priority) VALUES (?, ?, ?, ?)",
            (ticket_number, phone_number, summary, priority),
        )
    return ticket_number


def get_ticket(ticket_number: str, phone_number: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT ticket_number, summary, status, priority, created_at FROM crm_tickets "
            "WHERE ticket_number = ? AND phone_number = ?",
            (ticket_number.strip().upper(), phone_number),
        ).fetchone()
    return dict(row) if row else None


def list_tickets(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT ticket_number, summary, status, priority, created_at FROM crm_tickets "
            "WHERE phone_number = ? ORDER BY id DESC LIMIT 10", (phone_number,)).fetchall()
    return [dict(r) for r in rows]


def account_summary(phone_number: str) -> dict | None:
    """Plan, this month's volumes and delivery rate, and anything unpaid."""
    customer = lookup_customer(phone_number)
    if not customer:
        return None
    with db.get_conn() as conn:
        month = conn.execute(
            "SELECT MAX(month) AS m FROM crm_usage_stats WHERE phone_number = ?", (phone_number,)
        ).fetchone()["m"]
        usage = conn.execute(
            "SELECT channel, sent, delivered, failed FROM crm_usage_stats "
            "WHERE phone_number = ? AND month = ?", (phone_number, month)).fetchall()
        unpaid = conn.execute(
            "SELECT invoice_number, amount_usd, due_date FROM crm_invoices "
            "WHERE phone_number = ? AND status != 'paid'", (phone_number,)).fetchall()
    channels = []
    for u in usage:
        rate = round(100 * u["delivered"] / u["sent"], 2) if u["sent"] else None
        channels.append({**dict(u), "delivery_rate_pct": rate})
    return {
        **customer, "month": month, "channels": channels,
        "unpaid_invoices": [dict(i) for i in unpaid],
    }


def list_invoices(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT invoice_number, period, amount_usd, status, due_date FROM crm_invoices "
            "WHERE phone_number = ? ORDER BY period DESC", (phone_number,)).fetchall()
    return [dict(r) for r in rows]


def usage_stats(phone_number: str, month: str | None = None) -> list[dict]:
    with db.get_conn() as conn:
        if month:
            rows = conn.execute(
                "SELECT month, channel, sent, delivered, failed FROM crm_usage_stats "
                "WHERE phone_number = ? AND month = ?", (phone_number, month)).fetchall()
        else:
            rows = conn.execute(
                "SELECT month, channel, sent, delivered, failed FROM crm_usage_stats "
                "WHERE phone_number = ? ORDER BY month DESC", (phone_number,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["delivery_rate_pct"] = round(100 * d["delivered"] / d["sent"], 2) if d["sent"] else None
        out.append(d)
    return out


def coverage_rates(country: str | None = None, channel: str | None = None) -> list[dict]:
    sql = "SELECT country, channel, rate_usd, notes FROM crm_coverage WHERE 1=1"
    args = []
    if country:
        sql += " AND lower(country) LIKE ?"
        args.append(f"%{country.strip().lower()}%")
    if channel:
        sql += " AND lower(channel) LIKE ?"
        args.append(f"%{channel.strip().lower()}%")
    with db.get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY country, channel LIMIT 20", args).fetchall()
    return [dict(r) for r in rows]


def place_order(phone_number: str, sku: str, quantity: int, needed_by: str | None) -> dict | None:
    """Raises a real new order against the account."""
    product = get_product(sku)
    if not product:
        return None
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM crm_orders").fetchone()["n"]
        order_number = f"ORD-{5500 + count + 1}"
        conn.execute(
            "INSERT INTO crm_orders (order_number, phone_number, sku, quantity, status, eta) "
            "VALUES (?, ?, ?, ?, 'pending confirmation', ?)",
            (order_number, phone_number, product["sku"], quantity, needed_by),
        )
    return {
        "order_number": order_number, "product": product["name"], "quantity": quantity,
        "status": "pending confirmation", "needed_by": needed_by,
        "estimated_cost_usd": round(quantity * product["price_usd"], 2),
    }


def list_catalogue(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT item_name, category, body, status FROM crm_catalogue "
            "WHERE phone_number = ? ORDER BY id", (phone_number,)).fetchall()
    return [dict(r) for r in rows]


def add_catalogue_item(phone_number: str, item_name: str, category: str, body: str) -> dict:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_catalogue (phone_number, item_name, category, body) VALUES (?, ?, ?, ?)",
            (phone_number, item_name, category, body),
        )
    return {"item_name": item_name, "category": category, "status": "pending review",
            "review_eta": "usually within 24 hours"}


def schedule_callback(phone_number: str, preferred_time: str, topic: str | None) -> dict:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_callbacks (phone_number, preferred_time, topic) VALUES (?, ?, ?)",
            (phone_number, preferred_time, topic),
        )
    return {"preferred_time": preferred_time, "topic": topic, "status": "scheduled"}


def service_status() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT reference, service, status, summary, started_at FROM crm_incidents "
            "WHERE status != 'resolved'").fetchall()
    return [dict(r) for r in rows]
