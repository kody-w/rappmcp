"""One MCP server for a whole folder of agent.py files: a thin alias over brainstem-mcp's engine.

The engine never imports agent code. Short-lived workers scan the folder, one file per worker, and
a fresh worker runs each call, each with a timeout. A crashing or hanging agent costs one worker,
never the server. Everything here keeps RAPPMCP's names and wording; the work is brainstem_mcp.engine.
"""
import asyncio
import json
import pathlib
import urllib.request

import mcp_types as types
from brainstem_mcp import engine
from brainstem_mcp.engine import SCAN_TIMEOUT, fingerprint, run_worker  # noqa: F401  (old names keep working)

from . import __version__, kernel

CHAT_TOOL = "brainstem_chat"
PIN = pathlib.Path(__file__).resolve().parent / "kernel.json"

engine.LOG_PREFIX = "rappmcp"


def forward_chat(url, user_input, session_id=None, timeout=120):
    """POST to an existing Brainstem's /chat, unchanged. Returns its 'response' field."""
    body = {"user_input": user_input}
    if session_id:
        body["session_id"] = session_id
    request = urllib.request.Request(
        url.rstrip("/") + "/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        data = json.loads(reply.read().decode())
    return data.get("response", "")


CHAT_TOOL_DEF = {
    "name": CHAT_TOOL,
    "description": "Ask the Brainstem running on this machine a question in plain language. It picks its own agents.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "user_input": {"type": "string", "description": "What to ask the Brainstem."},
            "session_id": {"type": "string", "description": "Optional. Reuse to continue one conversation."},
        },
        "required": ["user_input"],
    },
}


class ChatTool(engine.BaseTools):
    """brainstem_chat, offered only with --chat-url: forwards to that Brainstem's POST /chat."""

    def __init__(self, chat_url):
        self.chat_url = chat_url
        self.names = frozenset({CHAT_TOOL}) if chat_url else frozenset()

    async def tools(self):
        if not self.chat_url:
            return []
        return [types.Tool(name=CHAT_TOOL, description=CHAT_TOOL_DEF["description"],
                           input_schema=CHAT_TOOL_DEF["inputSchema"])]

    async def call(self, name, arguments):
        try:
            text = await asyncio.to_thread(
                forward_chat, self.chat_url, (arguments or {}).get("user_input", ""), (arguments or {}).get("session_id")
            )
            failed = False
        except Exception as error:
            text, failed = f"The Brainstem at {self.chat_url} did not answer: {error}", True
        return types.CallToolResult(content=[types.TextContent(text=text)], is_error=failed)


class Rappmcp(engine.Engine):
    def __init__(self, target, timeout=120.0, chat_url=None, poll=1.0, scan_timeout=SCAN_TIMEOUT):
        kernel.use_pin(PIN)  # RAPPMCP's own pin, for this server and every worker
        super().__init__(target, timeout=timeout, poll=poll, scan_timeout=scan_timeout, base=ChatTool(chat_url),
                         name="rappmcp", version=__version__, stopped_by="RAPPMCP", base_first=False)
        self.chat_url = chat_url


async def serve_stdio(target, timeout=120.0, chat_url=None, poll=1.0, scan_timeout=SCAN_TIMEOUT):
    """Answer initialize at once, serve the healthy tools, and skip a file that hangs."""
    await engine.serve_stdio(Rappmcp(target, timeout=timeout, chat_url=chat_url, poll=poll, scan_timeout=scan_timeout))
