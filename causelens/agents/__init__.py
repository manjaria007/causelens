"""Investigation orchestration (Task 8): shared state, logged MCP calls, three roles, a structured result.

See ``docs/agents.md``. The analytics run behind the MCP server; nothing here computes a statistic.
"""
from causelens.agents.claims import ClaimEligibility, assess_claim
from causelens.agents.orchestrator import Investigation, investigate, investigate_sync
from causelens.agents.reasoner import Context, Reasoner, TemplateReasoner
from causelens.agents.result import InvestigationResult
from causelens.agents.roles import (
    DETECTIVE,
    EXPERIMENT_DESIGNER,
    ROLES,
    SCIENTIST,
    Role,
)
from causelens.agents.state import (
    InvestigationRequest,
    InvestigationState,
    LoggedCall,
    Prior,
    Window,
)
from causelens.agents.toolbox import (
    McpToolClient,
    Toolbox,
    ToolCallError,
    ToolNotAllowed,
    stdio_target,
)
from causelens.agents.trace import (
    Ref,
    Statement,
    Traced,
    TracedInterval,
    TraceError,
    verify_traces,
)

__all__ = [
    "DETECTIVE",
    "EXPERIMENT_DESIGNER",
    "ROLES",
    "SCIENTIST",
    "ClaimEligibility",
    "Context",
    "Investigation",
    "InvestigationRequest",
    "InvestigationResult",
    "InvestigationState",
    "LoggedCall",
    "McpToolClient",
    "Prior",
    "Reasoner",
    "Ref",
    "Role",
    "Statement",
    "TemplateReasoner",
    "ToolCallError",
    "ToolNotAllowed",
    "Toolbox",
    "TraceError",
    "Traced",
    "TracedInterval",
    "Window",
    "assess_claim",
    "investigate",
    "investigate_sync",
    "stdio_target",
    "verify_traces",
]
