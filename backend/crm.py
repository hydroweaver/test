"""A toy CRM the bot makes tool calls against, so the token/cost numbers reflect a
realistic order-taking conversation rather than a single Q&A turn.

Modelled on a neighbourhood kirana (grocery) store: products, orders, a khata
(credit) ledger, delivery-area lookup, stock requests and store notices. All dummy
data - seeded once on startup. Product images are generated locally (simple branded
cards) so the bot has something real to send over WhatsApp.
"""

import os

import db

MEDIA_DIR = os.path.join(os.path.dirname(db.DB_PATH) or ".", "media")

CUSTOMERS = [
    # phone_number, name, plan (customer type), customer_since
    # +32477874767 is the number this bot has actually been tested against - keep it.
    ("+32477874767", "Karan", "Regular", "2023-04-11"),
    ("+919820012345", "Priya Deshmukh", "Loyalty Member", "2024-09-02"),
    ("+919845098450", "Arjun Rao", "New Customer", "2025-12-19"),
]

PRODUCTS = [
    # sku, name, price_inr, description
    ("RICE-BASMATI-5KG", "Basmati Rice 5kg", 649.0, "Aged, long-grain basmati rice. 5kg pack."),
    ("DAL-TOOR-1KG", "Toor Dal 1kg", 148.0, "Unpolished toor dal. 1kg pack."),
    ("OIL-SUNFLOWER-1L", "Sunflower Oil 1L", 168.0, "Refined sunflower cooking oil. 1 litre pouch."),
    ("MILK-AMUL-1L", "Amul Gold Milk 1L", 72.0, "Full-cream milk, delivered fresh daily. 1 litre pouch."),
    ("ATTA-CHAKKI-5KG", "Chakki Atta 5kg", 245.0, "Stone-ground whole wheat flour. 5kg pack."),
    ("TEA-MASALA-250G", "Masala Tea 250g", 95.0, "Blended CTC tea leaves with cardamom. 250g pack."),
]

ORDERS = [
    # order_number, phone_number, sku, quantity, status, eta
    ("ORD-4412", "+32477874767", "RICE-BASMATI-5KG", 1, "out for delivery", "2026-09-05"),
    ("ORD-4413", "+32477874767", "MILK-AMUL-1L", 2, "delivered", "2026-08-28"),
    ("ORD-5120", "+919820012345", "ATTA-CHAKKI-5KG", 1, "packed", "2026-09-09"),
]

# Khata: the running credit tab a lot of regular kirana customers keep instead of
# paying per visit. Reuses the invoices table - period is the billing month.
KHATA = [
    # invoice_number, phone, period, amount_inr, status, due_date
    ("KHT-20268", "+32477874767", "2026-08", 640.0, "paid", "2026-09-01"),
    ("KHT-20341", "+32477874767", "2026-09", 385.0, "unpaid", "2026-10-01"),
    ("KHT-20355", "+919820012345", "2026-09", 210.0, "unpaid", "2026-10-01"),
]

# Delivery areas and fees. Reuses the coverage table: country -> area, channel ->
# delivery type, rate_usd -> fee_inr.
DELIVERY_AREAS = [
    ("Koramangala", "Home delivery", 0.0, "Free on orders above ₹300, within 3km"),
    ("Koramangala", "Express (1hr)", 25.0, "Available 9am-9pm"),
    ("HSR Layout", "Home delivery", 20.0, "Usually delivered within 45 minutes"),
    ("Indiranagar", "Home delivery", 30.0, "Next-day for orders placed after 8pm"),
    ("Outside these areas", "Courier", 60.0, "2-3 working days, prepaid only"),
]

# Store notices: stock shortages, timing changes, festival closures. Reuses the
# incidents table: reference -> notice id, service -> category.
STORE_NOTICES = [
    ("NTC-101", "Stock", "active",
     "Amul Gold Milk is temporarily out of stock - restocking tomorrow morning.",
     "2026-09-05 07:00"),
]

# Stock requests: a customer asking the shop to start carrying something. Reuses the
# catalogue table: item_name -> requested item, body -> the customer's note.
STOCK_REQUESTS_SEED = [
    ("+32477874767", "Organic Jaggery 500g", "Sweeteners",
     "Would love this in the regular lineup, not just around festivals.", "pending review"),
]

_PALETTE = [(217, 119, 6), (5, 150, 105), (79, 70, 229), (219, 39, 119)]


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
    draw.text((80, 260), f"₹{price:.0f}", fill=(255, 255, 255))
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
                "VALUES (?, ?, ?, ?, ?, ?)", KHATA)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_coverage").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_coverage (country, channel, rate_usd, notes) VALUES (?, ?, ?, ?)",
                DELIVERY_AREAS)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_incidents").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_incidents (reference, service, status, summary, started_at) "
                "VALUES (?, ?, ?, ?, ?)", STORE_NOTICES)
        if conn.execute("SELECT COUNT(*) AS n FROM crm_catalogue").fetchone()["n"] == 0:
            conn.executemany(
                "INSERT INTO crm_catalogue (phone_number, item_name, category, body, status) "
                "VALUES (?, ?, ?, ?, ?)", STOCK_REQUESTS_SEED)


def lookup_customer(phone_number: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT name, plan AS customer_type, customer_since FROM crm_customers WHERE phone_number = ?",
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
            "SELECT sku, name, price_usd AS price_inr, description, image_file FROM crm_products ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_product(sku: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sku, name, price_usd AS price_inr, description, image_file FROM crm_products WHERE sku = ?",
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
    """Customer type, this month's orders and spend, and any khata (credit) due."""
    customer = lookup_customer(phone_number)
    if not customer:
        return None
    with db.get_conn() as conn:
        month = conn.execute("SELECT strftime('%Y-%m', 'now') AS m").fetchone()["m"]
        orders_this_month = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(o.quantity * p.price_usd), 0) AS spend "
            "FROM crm_orders o LEFT JOIN crm_products p ON p.sku = o.sku "
            "WHERE o.phone_number = ? AND o.eta LIKE ?",
            (phone_number, f"{month}%"),
        ).fetchone()
        unpaid = conn.execute(
            "SELECT invoice_number, amount_usd AS amount_inr, due_date FROM crm_invoices "
            "WHERE phone_number = ? AND status != 'paid'", (phone_number,)).fetchall()
    return {
        **customer, "month": month,
        "orders_this_month": orders_this_month["n"],
        "spend_this_month_inr": round(orders_this_month["spend"], 2),
        "khata_due": [dict(i) for i in unpaid],
    }


def list_khata(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT invoice_number, period, amount_usd AS amount_inr, status, due_date FROM crm_invoices "
            "WHERE phone_number = ? ORDER BY period DESC", (phone_number,)).fetchall()
    return [dict(r) for r in rows]


def delivery_areas(area: str | None = None) -> list[dict]:
    sql = ("SELECT country AS area, channel AS delivery_type, rate_usd AS fee_inr, notes "
           "FROM crm_coverage WHERE 1=1")
    args = []
    if area:
        sql += " AND lower(country) LIKE ?"
        args.append(f"%{area.strip().lower()}%")
    with db.get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY area LIMIT 20", args).fetchall()
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
        "estimated_cost_inr": round(quantity * product["price_inr"], 2),
    }


def list_stock_requests(phone_number: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT item_name, category, body AS note, status FROM crm_catalogue "
            "WHERE phone_number = ? ORDER BY id", (phone_number,)).fetchall()
    return [dict(r) for r in rows]


def request_stock_item(phone_number: str, item_name: str, category: str, note: str) -> dict:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_catalogue (phone_number, item_name, category, body) VALUES (?, ?, ?, ?)",
            (phone_number, item_name, category, note),
        )
    return {"item_name": item_name, "category": category, "status": "pending review",
            "review_eta": "usually within 2-3 days"}


def schedule_callback(phone_number: str, preferred_time: str, topic: str | None) -> dict:
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO crm_callbacks (phone_number, preferred_time, topic) VALUES (?, ?, ?)",
            (phone_number, preferred_time, topic),
        )
    return {"preferred_time": preferred_time, "topic": topic, "status": "scheduled"}


def store_notices() -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT reference AS notice_id, service AS category, status, summary, started_at "
            "FROM crm_incidents WHERE status != 'resolved'").fetchall()
    return [dict(r) for r in rows]
