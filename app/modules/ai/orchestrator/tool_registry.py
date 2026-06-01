from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    tenant_required_integrations: tuple[str, ...] = ()
    rate_limit: str = "100/min/tenant"
    fallback: str = "create_support_ticket"
    destructive: bool = False
    requires_confirmation: bool = False


TOOLS: dict[str, ToolDefinition] = {
    "get_order_status": ToolDefinition(
        name="get_order_status",
        description="Fetch the latest known order status for a customer phone or order id.",
        input_schema={"order_id": "string|null", "phone": "string"},
        tenant_required_integrations=("oms",),
    ),
    "get_dispatch_details": ToolDefinition(
        name="get_dispatch_details",
        description="Fetch courier, fulfillment, dispatch, and shipment details for an order.",
        input_schema={"order_id": "string|null", "phone": "string"},
        tenant_required_integrations=("oms",),
    ),
    "get_tracking_link": ToolDefinition(
        name="get_tracking_link",
        description="Fetch the best available live tracking URL for an order.",
        input_schema={"order_id": "string|null", "phone": "string"},
        tenant_required_integrations=("oms",),
    ),
    "search_catalog": ToolDefinition(
        name="search_catalog",
        description="Search catalog products by user query and filters.",
        input_schema={"query": "string", "limit": "integer"},
        tenant_required_integrations=("catalog",),
    ),
    "get_product": ToolDefinition(
        name="get_product",
        description="Fetch a specific product by SKU, product id, title, or query.",
        input_schema={"sku": "string|null", "query": "string|null"},
        tenant_required_integrations=("catalog",),
    ),
    "get_policy": ToolDefinition(
        name="get_policy",
        description="Fetch policy or FAQ context for returns, shipping, warranty, COD, and care questions.",
        input_schema={"topic": "string", "message": "string"},
    ),
    "get_bundle_recommendations": ToolDefinition(
        name="get_bundle_recommendations",
        description="Recommend complementary products for a selected SKU or product query.",
        input_schema={"sku": "string|null", "query": "string"},
        tenant_required_integrations=("catalog",),
    ),
    "create_support_ticket": ToolDefinition(
        name="create_support_ticket",
        description="Create an async support ticket with issue and conversation context.",
        input_schema={"issue": "string", "conversation_history": "string", "email": "string|null"},
    ),
    "add_to_cart": ToolDefinition(
        name="add_to_cart",
        description="Add a product to a recoverable WhatsApp cart draft.",
        input_schema={"sku": "string|null", "query": "string|null", "qty": "integer"},
        tenant_required_integrations=("catalog",),
    ),
    "generate_checkout_link": ToolDefinition(
        name="generate_checkout_link",
        description="Generate or retrieve checkout details for a cart draft.",
        input_schema={"cart_id": "integer|null", "user_phone": "string"},
        tenant_required_integrations=("oms",),
    ),
    "apply_discount": ToolDefinition(
        name="apply_discount",
        description="Apply a configured discount code to a cart draft.",
        input_schema={"code": "string", "cart_id": "integer|null"},
    ),
    "get_return_eligibility": ToolDefinition(
        name="get_return_eligibility",
        description="Check whether an order appears eligible for return based on configured policy.",
        input_schema={"order_id": "string|null", "phone": "string"},
        tenant_required_integrations=("oms",),
    ),
    "initiate_return": ToolDefinition(
        name="initiate_return",
        description="Create a return request after eligibility check and user confirmation.",
        input_schema={"order_id": "string", "reason": "string|null", "item_ids": "array"},
        tenant_required_integrations=("oms",),
        destructive=True,
        requires_confirmation=True,
    ),
    "log_bulk_lead": ToolDefinition(
        name="log_bulk_lead",
        description="Log a gifting, bulk, corporate, event, or hospitality lead.",
        input_schema={"name": "string|null", "occasion": "string|null", "qty": "string|null", "timeline": "string|null", "email": "string|null"},
    ),
}


LEGACY_TOOL_ALIASES = {
    "search_products": "search_catalog",
    "get_policy_or_faq": "get_policy",
    "search_knowledge": "get_policy",
    "get_services": "get_policy",
    "checkout": "generate_checkout_link",
    "return_eligibility": "get_return_eligibility",
    "bulk_lead": "log_bulk_lead",
    "tracking": "get_tracking_link",
    "dispatch": "get_dispatch_details",
}


def normalize_tool_name(name: str | None) -> str:
    candidate = (name or "").strip()
    candidate = LEGACY_TOOL_ALIASES.get(candidate, candidate)
    return candidate if candidate in TOOLS else "get_policy"


def is_core_tool(name: str | None) -> bool:
    candidate = (name or "").strip()
    candidate = LEGACY_TOOL_ALIASES.get(candidate, candidate)
    return candidate in TOOLS


def is_destructive_tool(name: str | None) -> bool:
    tool = TOOLS.get(LEGACY_TOOL_ALIASES.get((name or "").strip(), (name or "").strip()))
    return bool(tool and tool.destructive)


def requires_confirmation(name: str | None) -> bool:
    tool = TOOLS.get(LEGACY_TOOL_ALIASES.get((name or "").strip(), (name or "").strip()))
    return bool(tool and tool.requires_confirmation)


def tool_json_schema(tool: ToolDefinition) -> dict:
    properties = {}
    required = []
    for key, value in (tool.input_schema or {}).items():
        schema = _schema_value(value)
        properties[key] = schema
        if "null" not in str(value):
            required.append(key)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _schema_value(value) -> dict:
    text = str(value or "string").lower()
    if "integer" in text or text == "int":
        return {"type": "integer"}
    if "number" in text or "float" in text:
        return {"type": "number"}
    if "array" in text or "list" in text:
        return {"type": "array", "items": {"type": "string"}}
    if "boolean" in text or text == "bool":
        return {"type": "boolean"}
    if "null" in text:
        return {"type": ["string", "null"]}
    return {"type": "string"}


def get_tool(name: str) -> ToolDefinition | None:
    return TOOLS.get(normalize_tool_name(name))


def list_tools() -> list[ToolDefinition]:
    return list(TOOLS.values())
