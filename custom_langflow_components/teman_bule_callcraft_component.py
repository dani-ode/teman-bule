"""Dispatch validated tool arguments from Langflow to the Teman Bule backend.

The historical component identity is retained for saved-flow compatibility.
This component calls the backend gateway, not CallCraft. CallCraft REST extraction
must run upstream; its JSON does not carry trusted execution context or prove
that a domain operation has already succeeded.
"""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from pydantic import ValidationError

from custom_langflow_components.teman_bule_runtime import (
    RuntimeBoundaryError,
    RuntimeClient,
    RuntimeSettings,
    ToolRequest,
)


class TemanBuleCallcraftComponent(Component):
    display_name = "Teman Bule CallCraft"
    description = (
        "Dispatch tool arguments to the Teman Bule backend with a trusted execution grant. "
        "CallCraft JSON extraction runs upstream; no CallCraft callback is used."
    )
    name = "TemanBuleCallcraft"
    icon = "Workflow"
    inputs = [DataInput(name="tool_request", display_name="Tool Request", required=True)]
    outputs = [Output(name="result", display_name="Tool Outcome", method="execute_tool")]

    async def execute_tool(self) -> Data:
        try:
            request = ToolRequest.model_validate(self.tool_request.data)
        except (ValidationError, AttributeError):
            raise RuntimeBoundaryError("Invalid tool request input") from None
        client = RuntimeClient(RuntimeSettings.from_environment())
        response = await client.execute_tool(request)
        return Data(data=response.model_dump())
