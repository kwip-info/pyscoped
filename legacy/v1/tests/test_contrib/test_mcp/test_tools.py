"""Tests for MCP tool definitions."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")


class TestMCPTools:
    @pytest.mark.anyio
    async def test_list_tools(self, mcp_server):
        tools = await mcp_server.list_tools()
        tool_names = [t.name for t in tools]
        assert "create_principal" in tool_names
        assert "create_object" in tool_names
        assert "get_object" in tool_names
        assert "create_scope" in tool_names
        assert "list_audit" in tool_names
        assert "health_check" in tool_names

    @pytest.mark.anyio
    async def test_create_principal(self, mcp_server):
        result = await mcp_server.call_tool(
            "create_principal",
            {"kind": "user", "display_name": "Tool User"},
        )
        assert len(result) > 0
        # Result is a list of TextContent
        text = result[0][0].text
        assert "id" in text
        assert "user" in text

    @pytest.mark.anyio
    async def test_create_object(self, mcp_server, mcp_user):
        result = await mcp_server.call_tool(
            "create_object",
            {
                "object_type": "document",
                "owner_id": mcp_user.id,
                "data": {"title": "MCP Doc"},
            },
        )
        text = result[0][0].text
        assert "object_id" in text
        assert "version" in text

    @pytest.mark.anyio
    async def test_get_object(self, mcp_server, mcp_user, mcp_backend):
        from scoped.objects.manager import ScopedManager

        manager = ScopedManager(mcp_backend)
        obj, _ = manager.create(
            object_type="doc", owner_id=mcp_user.id, data={"x": 1}
        )

        result = await mcp_server.call_tool(
            "get_object",
            {"object_id": obj.id, "principal_id": mcp_user.id},
        )
        text = result[0][0].text
        assert obj.id in text

    @pytest.mark.anyio
    async def test_get_object_not_found(self, mcp_server, mcp_user):
        result = await mcp_server.call_tool(
            "get_object",
            {"object_id": "nonexistent", "principal_id": mcp_user.id},
        )
        text = result[0][0].text
        assert "not found" in text.lower() or "denied" in text.lower()

    @pytest.mark.anyio
    async def test_create_scope(self, mcp_server, mcp_user):
        result = await mcp_server.call_tool(
            "create_scope",
            {"name": "MCP Scope", "owner_id": mcp_user.id},
        )
        text = result[0][0].text
        assert "scope_id" in text
        assert "MCP Scope" in text

    @pytest.mark.anyio
    async def test_list_audit(self, mcp_server, mcp_user, mcp_backend):
        from scoped.audit.writer import AuditWriter
        from scoped.types import ActionType

        writer = AuditWriter(mcp_backend)
        writer.record(
            actor_id=mcp_user.id,
            action=ActionType.CREATE,
            target_type="test",
            target_id="t1",
        )

        result = await mcp_server.call_tool("list_audit", {"limit": 10})
        text = result[0][0].text
        assert "create" in text

    @pytest.mark.anyio
    async def test_health_check(self, mcp_server):
        result = await mcp_server.call_tool("health_check", {})
        text = result[0][0].text
        assert "healthy" in text
