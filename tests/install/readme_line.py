"""Install RAPPMCP from GitHub the way the README says, serve the examples, and list the tools.
Then serve RAPPMCP as a distro with brainstem-mcp --distro distro.json, as the README says.

Used by CI on clean machines with no Brainstem installed. Pass the ref (branch or commit) as the first argument.
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REF = sys.argv[1] if len(sys.argv) > 1 else "main"
EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples")
DISTRO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "distro.json"))
PYPROJECT = open(os.path.join(os.path.dirname(DISTRO), "pyproject.toml"), encoding="utf-8").read()
ENGINE = PYPROJECT.split("git+https://github.com/kody-w/brainstem-mcp@", 1)[1].split('"', 1)[0]


async def main() -> None:
    server = StdioServerParameters(
        command="uvx",
        args=["--from", f"git+https://github.com/kody-w/rappmcp@{REF}", "rappmcp", "serve", os.path.abspath(EXAMPLES)],
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(tool.name for tool in (await session.list_tools()).tools)
            print("tools:", names)
            if "Echo" not in names:
                sys.exit("the install line started, but the Echo example is missing")
            result = await session.call_tool("Echo", {"text": "clean machine"})
            print("Echo said:", result.content[0].text)
    print("the README install line works on this machine")

    # The distro line: brainstem-mcp, at the commit RAPPMCP pins, serving distro.json.
    distro = StdioServerParameters(
        command="uvx",
        args=["--from", f"git+https://github.com/kody-w/brainstem-mcp@{ENGINE}", "brainstem-mcp", "--distro", DISTRO],
    )
    async with stdio_client(distro) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
            print("tools with --distro:", names)
            if names[:3] != ["chat", "job_status", "capabilities"] or "WingmanWizard" not in names:
                sys.exit("brainstem-mcp --distro distro.json started, but RAPPMCP's agents are missing")
    print("the README distro line works on this machine")


asyncio.run(asyncio.wait_for(main(), timeout=300))
