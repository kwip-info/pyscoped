"""Tests for MCP resource definitions."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp")


class TestMCPResources:
    @pytest.mark.anyio
    async def test_list_resources(self, mcp_server):
        resources = await mcp_server.list_resources()
        uris = [str(r.uri) for r in resources]
        assert "scoped://principals" in uris
        assert "scoped://health" in uris
        assert "scoped://audit/recent" in uris

    @pytest.mark.anyio
    async def test_principals_resource(self, mcp_server, mcp_user):
        result = await mcp_server.read_resource("scoped://principals")
        text = result[0].content if hasattr(result[0], "content") else str(result[0])
        data = json.loads(text)
        assert isinstance(data, list)
        assert any(p["id"] == mcp_user.id for p in data)

    @pytest.mark.anyio
    async def test_health_resource(self, mcp_server):
        result = await mcp_server.read_resource("scoped://health")
        text = result[0].content if hasattr(result[0], "content") else str(result[0])
        data = json.loads(text)
        assert "healthy" in data
        assert "checks" in data

    @pytest.mark.anyio
    async def test_audit_resource(self, mcp_server, mcp_user, mcp_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.types import ActionType

        writer = AuditWriter(mcp_backend)
        writer.record(
            actor_id=mcp_user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t1",
        )

        result = await mcp_server.read_resource("scoped://audit/recent")
        text = result[0].content if hasattr(result[0], "content") else str(result[0])
        data = json.loads(text)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["action"] == "create"
