import json

from app.modules.ai.orchestrator.response_schema import ToolCallResult


def tool_context_text(tool_result: ToolCallResult) -> str:
    parts = [
        f"Tool used: {tool_result.tool_name}",
        f"Tool status: {tool_result.status}",
    ]
    if tool_result.message:
        parts.append(f"Tool message: {tool_result.message}")
    if tool_result.data:
        parts.append("Tool data:\n" + json.dumps(tool_result.data, ensure_ascii=True, default=str)[:5000])
    return "\n".join(parts)


def fallback_reply(tool_result: ToolCallResult) -> str:
    if tool_result.message:
        return tool_result.message
    if tool_result.status == "needs_input":
        return "I need one more detail to help with that."
    if tool_result.status == "not_found":
        return "I could not find a matching record. Please share a little more detail."
    return "I checked the available information. What would you like to do next?"
