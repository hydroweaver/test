"""Tools the model can call: website search plus the toy CRM.

Schemas are provider-neutral here; each agent in agent.py converts them to its own
wire format. `ToolBox` binds the caller's phone number so the model never has to
pass it (and can't spoof another customer's records).
"""

import json

import crm
import db

TOOL_SCHEMAS = [
    {
        "name": "search_website",
        "description": (
            "Search the crawled content of the shop's website or FAQ page, if one is "
            "configured. Use for general questions about the shop, its policies, or offers."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Key terms from the question."}},
            "required": ["query"],
        },
    },
    {
        "name": "lookup_customer",
        "description": (
            "Look up the customer record for whoever is messaging right now (matched on their "
            "WhatsApp number). Use to greet them by name or check their customer type. Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_my_orders",
        "description": "List all orders belonging to the person messaging right now. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_order_status",
        "description": "Look up one specific order by its order number, e.g. ORD-4412.",
        "parameters": {
            "type": "object",
            "properties": {"order_number": {"type": "string", "description": "e.g. ORD-4412"}},
            "required": ["order_number"],
        },
    },
    {
        "name": "list_products",
        "description": "List the products in stock, with prices in rupees. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "send_product_image",
        "description": (
            "Send the customer a picture of one product. Use when showing off a product helps. "
            "The image is attached to your reply automatically - just mention it naturally."
        ),
        "parameters": {
            "type": "object",
            "properties": {"sku": {"type": "string", "description": "Product SKU, e.g. RICE-BASMATI-5KG"}},
            "required": ["sku"],
        },
    },
    {
        "name": "create_ticket",
        "description": (
            "Log a support ticket when the customer reports a problem you can't resolve yourself. "
            "Returns a ticket number to give them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One-line description of the issue."},
                "priority": {"type": "string", "description": "low, normal, high or urgent. Use high/urgent only for a wrong/missing delivery or a spoiled item."},
            },
            "required": ["summary"],
        },
    },
    {
        "name": "get_ticket_status",
        "description": "Check the status of an existing support ticket by number, e.g. TKT-7001.",
        "parameters": {
            "type": "object",
            "properties": {"ticket_number": {"type": "string", "description": "e.g. TKT-7001"}},
            "required": ["ticket_number"],
        },
    },
    {
        "name": "list_my_tickets",
        "description": "List the caller's recent support tickets and their statuses. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_account_summary",
        "description": (
            "The caller's account overview: customer type, orders and spend this month, and any "
            "khata (credit tab) due. Use for 'how much do I owe' or 'how's my account' type questions. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_khata",
        "description": "The caller's khata (credit tab) entries with amounts, due dates and paid/unpaid status. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_delivery_area",
        "description": (
            "Look up delivery options and fees for an area. Use for 'do you deliver to X' or "
            "'how much is delivery' questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "description": "Locality name, e.g. Koramangala. Optional - omit to list all."},
            },
            "required": [],
        },
    },
    {
        "name": "place_order",
        "description": (
            "Place a new order on the caller's account. Only call this once you have the product SKU "
            "and quantity - ask for anything missing first. The order is created as 'pending "
            "confirmation'; tell them the order number and that the shop will confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Product SKU, e.g. RICE-BASMATI-5KG. Use list_products if unsure."},
                "quantity": {"type": "integer", "description": "Number of units/packs."},
                "needed_by": {"type": "string", "description": "Optional date they need it by, e.g. 2026-10-01."},
            },
            "required": ["sku", "quantity"],
        },
    },
    {
        "name": "list_stock_requests",
        "description": (
            "List items the caller has asked the shop to start stocking, with review status. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "request_stock_item",
        "description": (
            "Ask the shop to start stocking an item it doesn't currently carry. Confirm the item "
            "name with the customer before submitting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "e.g. Organic Jaggery 500g"},
                "category": {"type": "string", "description": "e.g. Sweeteners, Snacks, Personal Care"},
                "note": {"type": "string", "description": "Why they want it, or how much they'd typically buy."},
            },
            "required": ["item_name", "category", "note"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "Book a callback from the shop owner at a time the customer gives you.",
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_time": {"type": "string", "description": "What the customer said, e.g. 'tomorrow 3pm IST'."},
                "topic": {"type": "string", "description": "Optional - what it's about."},
            },
            "required": ["preferred_time"],
        },
    },
    {
        "name": "check_store_notices",
        "description": (
            "Check for current store notices - stock shortages, timing changes, festival closures. "
            "Use FIRST when a customer asks why an item isn't available or the shop seems closed. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


class ToolBox:
    """Dispatches tool calls for one conversation, collecting any media to send back."""

    def __init__(self, caller_phone: str | None = None):
        self.caller_phone = caller_phone
        self.pending_media: list[str] = []  # filenames in the public media dir

    def call(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return f"Unknown tool '{name}'."
        try:
            return handler(args)
        except Exception as e:
            return f"Tool '{name}' failed: {e}"

    # --- website ---------------------------------------------------------

    def _search_website(self, args: dict) -> str:
        results = db.search_kb(args.get("query", ""), 5)
        if not results:
            return "No matching content found on the website for this query."
        return "\n\n".join(f"Source: {r['url']}\n{r['content']}" for r in results)

    # --- CRM -------------------------------------------------------------

    def _lookup_customer(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available (this request didn't come from WhatsApp)."
        record = crm.lookup_customer(self.caller_phone)
        if not record:
            return f"No customer record found for {self.caller_phone}. They're not registered yet."
        return json.dumps(record)

    def _list_my_orders(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available (this request didn't come from WhatsApp)."
        orders = crm.list_orders(self.caller_phone)
        return json.dumps(orders) if orders else "This customer has no orders on record."

    def _get_order_status(self, args: dict) -> str:
        order = crm.get_order(args.get("order_number", ""))
        if not order:
            return f"No order found with number {args.get('order_number')}."
        if self.caller_phone and order["phone_number"] != self.caller_phone:
            return "That order belongs to a different account - don't share its details."
        order.pop("phone_number", None)
        return json.dumps(order)

    def _list_products(self, args: dict) -> str:
        products = [
            {k: v for k, v in p.items() if k != "image_file"} for p in crm.list_products()
        ]
        return json.dumps(products)

    def _send_product_image(self, args: dict) -> str:
        product = crm.get_product(args.get("sku", ""))
        if not product:
            return f"No product with SKU {args.get('sku')}."
        if not product.get("image_file"):
            return f"No image available for {product['name']}."
        self.pending_media.append(product["image_file"])
        return f"Image of {product['name']} queued and will be attached to your reply."

    def _create_ticket(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available (this request didn't come from WhatsApp)."
        ticket = crm.create_ticket(
            self.caller_phone, args.get("summary", "(no summary)"), args.get("priority", "normal"))
        return json.dumps({"ticket_number": ticket, "status": "open"})

    def _get_ticket_status(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        ticket = crm.get_ticket(args.get("ticket_number", ""), self.caller_phone)
        if not ticket:
            return f"No ticket {args.get('ticket_number')} found on this account."
        return json.dumps(ticket)

    def _list_my_tickets(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        tickets = crm.list_tickets(self.caller_phone)
        return json.dumps(tickets) if tickets else "No support tickets on this account."

    def _get_account_summary(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        summary = crm.account_summary(self.caller_phone)
        return json.dumps(summary) if summary else "No account found for this number."

    def _list_khata(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        khata = crm.list_khata(self.caller_phone)
        return json.dumps(khata) if khata else "No khata entries on this account."

    def _check_delivery_area(self, args: dict) -> str:
        areas = crm.delivery_areas(args.get("area"))
        if not areas:
            return "No delivery info for that area - the shop can confirm directly."
        return json.dumps(areas)

    def _place_order(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        try:
            quantity = int(args.get("quantity") or 0)
        except (TypeError, ValueError):
            return "Quantity must be a whole number."
        if quantity <= 0:
            return "Quantity must be greater than zero - ask the customer how many they need."
        order = crm.place_order(self.caller_phone, args.get("sku", ""), quantity, args.get("needed_by"))
        if not order:
            return f"No product with SKU {args.get('sku')}. Use list_products to see valid SKUs."
        return json.dumps(order)

    def _list_stock_requests(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        items = crm.list_stock_requests(self.caller_phone)
        return json.dumps(items) if items else "No stock requests from this account yet."

    def _request_stock_item(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        name, category, note = args.get("item_name"), args.get("category"), args.get("note")
        if not (name and category and note):
            return "Need item_name, category and note to submit a stock request."
        return json.dumps(crm.request_stock_item(self.caller_phone, name, category, note))

    def _schedule_callback(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        if not args.get("preferred_time"):
            return "Ask the customer what time suits them first."
        return json.dumps(crm.schedule_callback(
            self.caller_phone, args["preferred_time"], args.get("topic")))

    def _check_store_notices(self, args: dict) -> str:
        notices = crm.store_notices()
        if not notices:
            return "No current store notices - everything's normal."
        return json.dumps(notices)
