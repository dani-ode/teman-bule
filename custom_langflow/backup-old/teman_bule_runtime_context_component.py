"""Langflow adapter for backend-authorized runtime metadata."""

from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, Output
from lfx.schema.data import Data
from pydantic import ValidationError

from custom_langflow_components.teman_bule_runtime import (
    RuntimeBoundaryError,
    RuntimeClient,
    RuntimeRequest,
    RuntimeSettings,
)


class TemanBuleRuntimeContextComponent(Component):
    display_name = "Teman Bule Runtime Context"
    description = "Resolve authorized runtime metadata without exposing provider keys."
    name = "TemanBuleRuntimeContext"
    icon = "ShieldCheck"
    inputs = [DataInput(name="runtime_request", display_name="Runtime Request", required=True)]
    outputs = [Output(name="context", display_name="Runtime Context", method="resolve_context")]

    async def resolve_context(self) -> Data:
        try:
            request = RuntimeRequest.model_validate(self.runtime_request.data)
        except (ValidationError, AttributeError):
            raise RuntimeBoundaryError("Invalid runtime request input") from None
        client = RuntimeClient(RuntimeSettings.from_environment())
        context = await client.resolve_context(request)
        return Data(data=context.model_dump())
