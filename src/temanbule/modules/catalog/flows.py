"""Published registry lookup for deployed Langflow workflows."""

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import AiFlowRegistry
from temanbule.platform.errors import DependencyUnavailableError


@dataclass(frozen=True)
class FlowBinding:
    flow_id: str
    flow_version: str
    input_schema_version: str
    output_schema_version: str
    timeout_ms: int
    tool_allowlist: tuple[str, ...] = ()
    prompt_version: str | None = None


async def resolve_flow(
    session: AsyncSession,
    *,
    environment: str,
    purpose: str,
    flow_version: str | None = None,
) -> FlowBinding:
    """Only active deployments execute; pinned jobs must match their exact version."""
    statement = select(AiFlowRegistry).where(
        AiFlowRegistry.environment == environment,
        AiFlowRegistry.purpose == purpose,
    )
    if flow_version is None:
        statement = statement.where(AiFlowRegistry.status == "active")
    else:
        statement = statement.where(
            AiFlowRegistry.flow_version == flow_version,
            AiFlowRegistry.status == "active",
        )
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None or not row.langflow_flow_id.strip() or row.timeout_ms <= 0:
        raise DependencyUnavailableError(
            "Workflow belum tersedia di registry untuk environment/purpose/version ini.",
            code="FLOW_NOT_CONFIGURED",
        )
    try:
        tools = json.loads(row.tool_allowlist or "[]")
        if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools):
            raise ValueError("Invalid allowlist")
    except (ValueError, TypeError) as exc:
        raise DependencyUnavailableError(
            "Workflow registry memiliki allowlist tidak valid.", code="FLOW_NOT_CONFIGURED",
        ) from exc
    return FlowBinding(
        flow_id=row.langflow_flow_id,
        flow_version=row.flow_version,
        input_schema_version=row.input_schema_version,
        output_schema_version=row.output_schema_version,
        timeout_ms=row.timeout_ms,
        tool_allowlist=tuple(tools),
        prompt_version=row.prompt_version,
    )
