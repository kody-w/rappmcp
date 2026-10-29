"""One MCP server for a whole folder of agent.py files.

The server never imports agent code. It asks a short-lived worker
(rappmcp.runner) to scan the folder and to run each call, with a timeout. A
crashing or hanging agent costs one worker, never the server.
"""
import asyncio
import json
import os
import pathlib
import signal
import sys
import urllib.request

import anyio
import mcp_types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged

from . import __version__

CHAT_TOOL = "brainstem_chat"
SCAN_TIMEOUT = 30.0


def _log(message):
    print(f"[rappmcp] {message}", file=sys.stderr, flush=True)


def fingerprint(target):
    """What changed on disk: every .py file next to the agents (name, size, mtime)."""
    target = pathlib.Path(target)
    files = [target] if target.is_file() else sorted(target.glob("*.py"))
    marks = []
    for path in files:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        marks.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(marks)


async def run_worker(args, stdin_text, timeout, cwd):
    """Run `python -m rappmcp.runner <args>` and return (reply dict or None, note)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(pathlib.Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")]))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "rappmcp.runner", *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        return None, f"timed out after {timeout:g} seconds"
    if err:
        _log(err.decode(errors="replace").strip()[-2000:])
    try:
        return json.loads(out.decode()), ""
    except ValueError:
        return None, f"stopped without an answer (exit code {proc.returncode})"


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


class Rappmcp:
    def __init__(self, target, timeout=60.0, chat_url=None, poll=1.0):
        self.target = pathlib.Path(target).expanduser().resolve()
        if not self.target.exists():
            raise FileNotFoundError(f"{self.target} does not exist")
        self.folder = self.target if self.target.is_dir() else self.target.parent
        self.timeout = timeout
        self.chat_url = chat_url
        self.poll = poll
        self.tools = []
        self.report = {}
        self.mark = None
        self.sessions = []
        self.bus = InMemorySubscriptionBus()

    async def scan(self):
        self.mark = fingerprint(self.target)
        reply, note = await run_worker(["list", str(self.target)], "", SCAN_TIMEOUT, str(self.folder))
        if reply is None or "tools" not in reply:
            reason = note or (reply or {}).get("text", "scan failed")
            _log(f"could not read the agents: {reason}")
            reply = {"tools": [], "quarantined": [], "errors": [{"file": str(self.target), "error": reason}], "instructions": []}
        self.tools = reply["tools"]
        self.report = reply
        for item in reply["quarantined"]:
            _log(f"quarantined {item['class']} in {item['file']}: {item['reason']}")
        for item in reply["errors"]:
            _log(f"could not load {item['file']}: {item['error']}")
        _log(f"{len(self.tools)} tool(s) from {self.target}")
        return self.tools

    def instructions(self):
        return "\n\n".join(self.report.get("instructions", [])) or None

    def listed(self):
        tools = [
            types.Tool(name=t["name"], description=t["description"], input_schema=t["inputSchema"])
            for t in self.tools
        ]
        if self.chat_url:
            tools.append(types.Tool(name=CHAT_TOOL, description=CHAT_TOOL_DEF["description"], input_schema=CHAT_TOOL_DEF["inputSchema"]))
        return tools

    async def call(self, name, arguments):
        if self.chat_url and name == CHAT_TOOL:
            try:
                text = await asyncio.to_thread(
                    forward_chat, self.chat_url, (arguments or {}).get("user_input", ""), (arguments or {}).get("session_id")
                )
                return False, text
            except Exception as error:
                return True, f"The Brainstem at {self.chat_url} did not answer: {error}"
        tool = next((t for t in self.tools if t["name"] == name), None)
        if tool is None:
            return True, f"Agent '{name}' not found."
        reply, note = await run_worker(
            ["call", tool["file"], name], json.dumps(arguments or {}), self.timeout, str(self.folder)
        )
        if reply is None:
            return True, f"The agent {name} {note}. Nothing else was affected."
        return (not reply.get("ok")), reply.get("text", "")

    def remember(self, ctx):
        session = getattr(ctx, "session", None)
        if session is not None and all(s is not session for s in self.sessions):
            self.sessions.append(session)

    async def watch(self):
        while True:
            await anyio.sleep(self.poll)
            if fingerprint(self.target) == self.mark:
                continue
            await self.scan()
            await self.bus.publish(ToolsListChanged())
            for session in list(self.sessions):
                try:
                    await session.send_tool_list_changed()
                except Exception as error:
                    _log(f"could not send tools/list_changed: {error}")

    def build(self):
        async def on_list_tools(ctx, params):
            self.remember(ctx)
            return types.ListToolsResult(tools=self.listed())

        async def on_call_tool(ctx, params):
            self.remember(ctx)
            failed, text = await self.call(params.name, params.arguments)
            return types.CallToolResult(content=[types.TextContent(text=text)], is_error=failed)

        return Server(
            "rappmcp",
            version=__version__,
            instructions=self.instructions(),
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
            on_subscriptions_listen=ListenHandler(self.bus),
        )


async def serve_stdio(target, timeout=60.0, chat_url=None, poll=1.0):
    app = Rappmcp(target, timeout=timeout, chat_url=chat_url, poll=poll)
    await app.scan()
    server = app.build()
    options = server.create_initialization_options(NotificationOptions(tools_changed=True))
    async with stdio_server() as (read, write):
        async with anyio.create_task_group() as group:
            group.start_soon(app.watch)
            await server.run(read, write, options)
            group.cancel_scope.cancel()
