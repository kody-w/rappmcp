"""Conformance: for every agent in a folder, tools/list == metadata and tools/call == perform().

Drives the real server over stdio with the official MCP Python client.
"""
import pytest

from conftest import EXAMPLES, FIXTURES, ROOT, connect, text_of
from rappmcp import loader

CALL_FOLDERS = [EXAMPLES, FIXTURES / "rules"]
# agents/ is listed only: its agents may drive real apps, so this test never calls them.
LIST_FOLDERS = CALL_FOLDERS + ([ROOT / "agents"] if any((ROOT / "agents").glob("*_agent.py")) else [])


def sample(schema):
    """Smallest arguments that satisfy an agent's required parameters."""
    args = {}
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        prop = props.get(key, {})
        if "enum" in prop:
            args[key] = prop["enum"][0]
        else:
            args[key] = {"string": "hello", "integer": 2, "number": 1.5, "boolean": True,
                         "array": [], "object": {}}.get(prop.get("type"), "hello")
    return args


def direct(folder):
    agents, _, _, _ = loader.load(folder)
    return agents


@pytest.mark.anyio
@pytest.mark.parametrize("folder", LIST_FOLDERS, ids=lambda p: p.name)
async def test_tools_list_matches_metadata(folder):
    agents = direct(folder)
    assert agents, f"no agents in {folder}"
    async with connect(folder) as (session, _):
        listed = {t.name: t for t in (await session.list_tools()).tools}
    assert set(listed) == set(agents)
    for name, agent in agents.items():
        meta = agent.metadata
        tool = listed[name]
        assert tool.name == meta.get("name", agent.name)
        assert (tool.description or "") == meta.get("description", "")
        assert tool.input_schema == meta.get("parameters", {"type": "object", "properties": {}})


@pytest.mark.anyio
@pytest.mark.parametrize("folder", CALL_FOLDERS, ids=lambda p: p.name)
async def test_tools_call_matches_perform(folder, tmp_path, monkeypatch):
    served_data, direct_data = tmp_path / "served", tmp_path / "direct"
    monkeypatch.setenv("RAPPMCP_DATA_DIR", str(direct_data))
    agents = direct(folder)
    expected = {}
    for name, agent in agents.items():
        args = sample(agent.metadata.get("parameters", {}))
        try:
            expected[name] = (args, False, str(agent.perform(**args)))
        except Exception as error:
            expected[name] = (args, True, f"Error: {error}")
    async with connect(folder, env={"RAPPMCP_DATA_DIR": str(served_data)}) as (session, _):
        for name, (args, failed, text) in expected.items():
            result = await session.call_tool(name, args)
            assert bool(result.is_error) == failed, name
            assert text_of(result) == text, name
