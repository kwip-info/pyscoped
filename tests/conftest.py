import pytest
from asgiref.sync import sync_to_async
from django.db import connections


@pytest.fixture(autouse=True)
async def close_async_database_connections(request):
    yield
    if request.node.get_closest_marker("asyncio"):
        await sync_to_async(connections.close_all, thread_sensitive=True)()
