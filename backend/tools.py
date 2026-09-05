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
            "Search the crawled content of the company website. Use for questions about "
            "the company, its products, services, coverage, or policies."
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
            "WhatsApp number). Use to greet them by name or check their plan. Takes no arguments."
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
        "description": "List the products/services available, with per-message pricing. Takes no arguments.",
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
            "properties": {"sku": {"type": "string", "description": "Product SKU, e.g. WA-API"}},
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
                "priority": {"type": "string", "description": "low, normal, high or urgent. Use high/urgent only for outages or blocked production traffic."},
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
            "The caller's account overview: plan, this month's send volumes and delivery rates "
            "per channel, and any unpaid invoices. Use for 'how is my account doing' type questions. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_usage_stats",
        "description": (
            "Message volumes and delivery rates for the caller, by month and channel. Use for "
            "questions about how many messages were sent, delivery/failure rates, or usage trends."
        ),
        "parameters": {
            "type": "object",
            "properties": {"month": {"type": "string", "description": "Optional, e.g. 2026-09. Omit for all months."}},
            "required": [],
        },
    },
    {
        "name": "list_invoices",
        "description": "The caller's invoices with amounts, due dates and paid/unpaid status. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_coverage_pricing",
        "description": (
            "Look up the per-message rate card for a country and/or channel (WhatsApp, SMS, Voice OTP). "
            "Use for 'how much does it cost to send to X' questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "e.g. India. Optional."},
                "channel": {"type": "string", "description": "e.g. WhatsApp or SMS. Optional."},
            },
            "required": [],
        },
    },
    {
        "name": "place_order",
        "description": (
            "Raise a new order on the caller's account. Only call this once you have the product SKU "
            "and quantity - ask for anything missing first. The order is created as 'pending "
            "confirmation'; tell them the order number and that sales will confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Product SKU, e.g. WA-API. Use list_products if unsure."},
                "quantity": {"type": "integer", "description": "Number of messages/units."},
                "needed_by": {"type": "string", "description": "Optional date they need it by, e.g. 2026-10-01."},
            },
            "required": ["sku", "quantity"],
        },
    },
    {
        "name": "list_catalogue",
        "description": (
            "List the caller's message templates (their catalogue), with approval status. "
            "Takes no arguments."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_catalogue_item",
        "description": (
            "Submit a new message template to the caller's catalogue for approval. Use {{1}}, {{2}} "
            "for variables. Confirm the wording with the customer before submitting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "item_name": {"type": "string", "description": "lowercase_with_underscores, e.g. order_shipped_v3"},
                "category": {"type": "string", "description": "marketing, utility or authentication"},
                "body": {"type": "string", "description": "The template text, with {{1}} style placeholders."},
            },
            "required": ["item_name", "category", "body"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "Book a callback from the team at a time the customer gives you.",
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
        "name": "check_service_status",
        "description": (
            "Check for ongoing platform incidents or degraded services. Use FIRST when a customer "
            "reports messages failing or being slow. Takes no arguments."
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

    def _get_usage_stats(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        stats = crm.usage_stats(self.caller_phone, args.get("month"))
        return json.dumps(stats) if stats else "No usage recorded for that period."

    def _list_invoices(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        invoices = crm.list_invoices(self.caller_phone)
        return json.dumps(invoices) if invoices else "No invoices on this account."

    def _check_coverage_pricing(self, args: dict) -> str:
        rates = crm.coverage_rates(args.get("country"), args.get("channel"))
        if not rates:
            return "No rate card entry for that country/channel - support can quote it."
        return json.dumps(rates)

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

    def _list_catalogue(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        items = crm.list_catalogue(self.caller_phone)
        return json.dumps(items) if items else "No templates in this account's catalogue yet."

    def _add_catalogue_item(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        name, category, body = args.get("item_name"), args.get("category"), args.get("body")
        if not (name and category and body):
            return "Need item_name, category and body to submit a template."
        return json.dumps(crm.add_catalogue_item(self.caller_phone, name, category, body))

    def _schedule_callback(self, args: dict) -> str:
        if not self.caller_phone:
            return "No caller context available."
        if not args.get("preferred_time"):
            return "Ask the customer what time suits them first."
        return json.dumps(crm.schedule_callback(
            self.caller_phone, args["preferred_time"], args.get("topic")))

    def _check_service_status(self, args: dict) -> str:
        incidents = crm.service_status()
        if not incidents:
            return "All services operating normally - no open incidents."
        return json.dumps(incidents)
