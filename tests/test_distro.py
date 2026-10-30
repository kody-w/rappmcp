"""RAPPMCP is a distro on brainstem-mcp: distro.json pins the kernel and every agent file by sha256.

brainstem-mcp serves it unchanged with --distro, and `rappmcp serve agents` serves the same tools.
"""
import json
import os
import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from brainstem_mcp import distro
from conftest import ROOT, connect

MANIFEST = ROOT / "distro.json"


def test_distro_pins_every_agent_file_and_rappmcps_own_kernel_pin():
    data = json.loads(MANIFEST.read_text())
    assert data["schema"] == distro.SCHEMA and data["name"] == "rappmcp"
    assert data["kernel"] == json.loads((ROOT / "rappmcp" / "kernel.json").read_text())
    listed = sorted(item["path"] for item in data["agents"])
    assert listed == sorted(f"agents/{p.name}" for p in (ROOT / "agents").glob("*_agent.py"))
    for path, ok, reason in distro.check(MANIFEST):
        assert ok, f"{path}: {reason}. Run: python -m brainstem_mcp.distro pin distro.json"


@pytest.mark.anyio
async def test_brainstem_mcp_serves_the_distro_and_rappmcp_serve_serves_the_same_tools():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "brainstem_mcp", "--distro", str(MANIFEST)],
        env={**os.environ, "BRAINSTEM_URL": "http://127.0.0.1:1"}, cwd=str(ROOT),
    )
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params, errlog=quiet) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                served = {t.name: t for t in (await session.list_tools()).tools}
    async with connect(ROOT / "agents") as (session, _):
        old = {t.name: t for t in (await session.list_tools()).tools}
    assert list(served)[:3] == ["chat", "job_status", "capabilities"]
    assert old and {name: served[name].input_schema for name in old} == {n: t.input_schema for n, t in old.items()}
