"""Install RAPPMCP from GitHub the way the README says, serve the examples, and list the tools.

Used by CI on clean machines with no Brainstem installed. Pass the ref (branch or commit) as the first argument.
"""
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REF = sys.argv[1] if len(sys.argv) > 1 else "main"
EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples")


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


asyncio.run(asyncio.wait_for(main(), timeout=300))
