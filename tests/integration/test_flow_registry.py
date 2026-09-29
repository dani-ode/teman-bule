"""Registry selection against real PostgreSQL; fixtures are rolled back."""

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from temanbule.modules.catalog.flows import resolve_flow
from temanbule.modules.catalog.models import AiFlowRegistry
from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.security import new_ulid

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_registry_environment_version_and_lifecycle():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL tidak diset")
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            purpose = f"test-{new_ulid()}"
            for environment, version, status in (
                ("test", "1", "staged"), ("test", "2", "active"),
                ("test", "3", "staged"), ("test", "4", "disabled"),
                ("production", "2", "active"),
            ):
                session.add(AiFlowRegistry(
                    id=new_ulid(), environment=environment, purpose=purpose,
                    flow_version=version, langflow_flow_id=f"{environment}-{version}",
                    input_schema_version="1", output_schema_version="1",
                    timeout_ms=5000, status=status,
                ))
            await session.flush()
            active = await resolve_flow(session, environment="test", purpose=purpose)
            assert active.flow_id == "test-2"
            pinned = await resolve_flow(
                session, environment="test", purpose=purpose, flow_version="2",
            )
            assert pinned.flow_id == "test-2"
            for version in ("1", "3", "4", "missing"):
                with pytest.raises(DependencyUnavailableError) as error:
                    await resolve_flow(
                        session, environment="test", purpose=purpose, flow_version=version,
                    )
                assert error.value.code == "FLOW_NOT_CONFIGURED"
            await session.rollback()
    finally:
        await engine.dispose()
