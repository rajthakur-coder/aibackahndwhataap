from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCallResult:
    tool_name: str
    status: str
    message: str = ""
    data: dict[str, Any] | list[dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestratorResponse:
    reply: str
    intent: str
    selected_tool: str
    confidence: float
    tool_result: ToolCallResult
    source: str = "ai_orchestrator"
