import os
import pathlib
import sys
from contextlib import asynccontextmanager

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def connect(path, *flags, env=None, on_message=None, errlog=None):
    """Start the real rappmcp server over stdio with the official MCP client.

    Pass an open file as errlog to keep the server's log lines."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rappmcp", "serve", str(path), *flags],
        env={"PYTHONPATH": str(ROOT), **(env or {})},
        cwd=str(ROOT),
    )
    kwargs = {"message_handler": on_message} if on_message else {}
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params, errlog=errlog or quiet) as (read, write):
            async with ClientSession(read, write, **kwargs) as session:
                init = await session.initialize()
                yield session, init


def text_of(result):
    return "".join(block.text for block in result.content if getattr(block, "type", "") == "text")
