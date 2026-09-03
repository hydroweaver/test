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


def create_ticket(phone_number: str, summary: str) -> str:
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM crm_tickets").fetchone()["n"]
        ticket_number = f"TKT-{7000 + count + 1}"
        conn.execute(
            "INSERT INTO crm_tickets (ticket_number, phone_number, summary) VALUES (?, ?, ?)",
            (ticket_number, phone_number, summary),
        )
    return ticket_number
