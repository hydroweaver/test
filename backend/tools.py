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
            "properties": {"summary": {"type": "string", "description": "One-line description of the issue."}},
            "required": ["summary"],
        },
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
        ticket = crm.create_ticket(self.caller_phone, args.get("summary", "(no summary)"))
        return json.dumps({"ticket_number": ticket, "status": "open"})
