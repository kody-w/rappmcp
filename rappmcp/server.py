"""One MCP server for a whole folder of agent.py files.

The server never imports agent code. Short-lived workers (rappmcp.runner) scan
the folder, one file per worker, and a fresh worker runs each call, each with
a timeout. A crashing or hanging agent costs one worker, never the server.
"""
import asyncio
import json
import os
import pathlib
import signal
import sys
import tempfile
import urllib.request

import anyio
import mcp_types as types
from mcp_types.jsonrpc import INVALID_PARAMS
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged

from . import __version__, kernel, loader

CHAT_TOOL = "brainstem_chat"
SCAN_TIMEOUT = 10.0  # seconds one file may take to import; well under a client's startup wait
STARTUP_WAIT = 2.0  # how long initialize waits for the first scan, so instructions can ride along
SCAN_WORKERS = max(1, min(8, os.cpu_count() or 1))

# RAPPMCP only reads the kernel install. Neither the server nor any worker writes bytecode.
sys.dont_write_bytecode = True


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


PACKAGE_HOME = str(pathlib.Path(__file__).resolve().parents[1])
TIMEOUT_ENV = "RAPPMCP_CALL_TIMEOUT"


def _stop(proc):
    """Stop a worker and everything it started in its own session. Never touches other processes."""
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def run_worker(args, stdin_text, timeout, cwd=None):
    """Run `python -m rappmcp.runner <args>` and return (reply dict or None, note).

    The call is over when the worker exits, not when its pipes close: a helper the agent
    started may keep stderr open, and that is the agent's business. The worker gets its own
    session, so a timeout, a cancel or a disconnect stops it and whatever it started then.
    It runs from RAPPMCP's own folder, so a file in the served folder (a json.py) can never
    replace a module RAPPMCP itself imports.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [PACKAGE_HOME, env.get("PYTHONPATH")]))
    env[TIMEOUT_ENV] = f"{timeout:g}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryFile() as errors:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "rappmcp.runner", *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errors,
            cwd=cwd or PACKAGE_HOME,
            env=env,
            start_new_session=True,
        )
        out = b""

        async def finish():
            nonlocal out
            try:
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass
            out = await proc.stdout.read()  # the answer channel is the worker's alone
            await proc.wait()

        try:
            await asyncio.wait_for(finish(), timeout)
        except asyncio.TimeoutError:
            _stop(proc)
            await proc.wait()
            return None, f"did not finish within {timeout:g} seconds, so RAPPMCP stopped it"
        finally:
            _stop(proc)  # a cancel or a disconnect lands here too: stop it before going away
        errors.seek(0)
        err = errors.read()
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


def _tool(t):
    hints = t.get("annotations")
    return types.Tool(
        name=t["name"],
        description=t["description"],
        input_schema=t["inputSchema"],
        annotations=types.ToolAnnotations(
            read_only_hint=hints.get("readOnlyHint"), destructive_hint=hints.get("destructiveHint")
        ) if hints else None,
    )


class Rappmcp:
    def __init__(self, target, timeout=120.0, chat_url=None, poll=1.0, scan_timeout=SCAN_TIMEOUT):
        self.target = pathlib.Path(target).expanduser().resolve()
        if not self.target.exists():
            raise FileNotFoundError(f"{self.target} does not exist")
        self.folder = self.target if self.target.is_dir() else self.target.parent
        self.timeout = timeout
        self.chat_url = chat_url
        self.poll = poll
        self.scan_timeout = scan_timeout
        self.ready = asyncio.Event()  # set once the first scan is done
        self.tools = []
        self.report = {}
        self.mark = None
        self.connections = []
        self.server = None
        self.options = None
        self.bus = InMemorySubscriptionBus()

    async def scan_file(self, path, limit):
        """Scan one agent file in its own worker. A file that exits or hangs loses only its own tools."""
        async with limit:
            reply, note = await run_worker(["list", str(path)], "", self.scan_timeout)
        if reply is not None and "tools" in reply:
            return reply
        if note.startswith("did not finish"):
            reason = f"skipped {path.name}: it did not finish loading within {self.scan_timeout:g} seconds (--scan-timeout)"
        else:
            reason = f"skipped {path.name}: {note or (reply or {}).get('text', 'scan failed')}"
        return {"tools": [], "quarantined": [], "errors": [{"file": path.name, "error": reason}]}

    async def scan(self):
        self.mark = fingerprint(self.target)
        try:
            files = loader.agent_files(self.target)
        except FileNotFoundError as error:
            files = []
            _log(str(error))
        limit = asyncio.Semaphore(SCAN_WORKERS)
        scans = await asyncio.gather(*(self.scan_file(path, limit) for path in files))
        reply = loader.merge(scans)
        self.tools = reply["tools"]
        self.report = reply
        for item in reply["quarantined"]:
            _log(f"quarantined {item['class']} in {item['file']}: {item['reason']}")
        for item in reply["errors"]:
            text = item["error"]
            _log(text if text.startswith("skipped ") else f"could not load {item['file']}: {text}")
        _log(f"{len(self.tools)} tool(s) from {self.target}")
        if self.server is not None:
            self.server.instructions = self.instructions()
        if self.options is not None:
            self.options.instructions = self.instructions()
        return self.tools

    async def first_scan(self):
        try:
            await self.scan()
        finally:
            self.ready.set()

    def instructions(self):
        return "\n\n".join(self.report.get("instructions", [])) or None

    def listed(self):
        tools = [_tool(t) for t in self.tools]
        if self.chat_url:
            tools.append(types.Tool(name=CHAT_TOOL, description=CHAT_TOOL_DEF["description"], input_schema=CHAT_TOOL_DEF["inputSchema"]))
        return tools

    async def call(self, name, arguments):
        if self.chat_url and name == CHAT_TOOL:
            try:
                text = await asyncio.to_thread(
                    forward_chat, self.chat_url, (arguments or {}).get("user_input", ""), (arguments or {}).get("session_id")
                )
                return False, text, None
            except Exception as error:
                return True, f"The Brainstem at {self.chat_url} did not answer: {error}", None
        await self.ready.wait()
        tool = next((t for t in self.tools if t["name"] == name), None)
        if tool is None:
            # MCP: an unknown tool is a protocol error (-32602), not a tool result.
            raise MCPError(INVALID_PARAMS, f"Unknown tool: {name}")
        reply, note = await run_worker(["call", tool["file"], name], json.dumps(arguments or {}), self.timeout)
        if reply is None:
            return True, (f"The agent {name} {note}. It may have done part of its work before it stopped "
                          "(a message may already be sent). Check before you try again, and do not repeat "
                          "an action that changes something."), None
        return (not reply.get("ok")), reply.get("text", ""), reply.get("structured")

    def remember(self, ctx):
        """Keep one entry per client connection. Request contexts are new every time; the
        connection behind them is not."""
        session = getattr(ctx, "session", None)
        connection = getattr(session, "_connection", None) or session
        if connection is not None and all(c is not connection for c in self.connections):
            self.connections.append(connection)

    async def rescan(self):
        await self.scan()
        await self.bus.publish(ToolsListChanged())
        for connection in list(self.connections):
            try:
                await connection.send_tool_list_changed()
            except Exception as error:
                _log(f"could not send tools/list_changed: {error}")
                self.connections.remove(connection)

    async def watch(self):
        await self.ready.wait()
        while True:
            await anyio.sleep(self.poll)
            if fingerprint(self.target) != self.mark:
                await self.rescan()

    def build(self):
        async def on_list_tools(ctx, params):
            self.remember(ctx)
            await self.ready.wait()  # the first scan; each file has --scan-timeout at most
            return types.ListToolsResult(tools=self.listed())

        async def on_call_tool(ctx, params):
            self.remember(ctx)
            failed, text, structured = await self.call(params.name, params.arguments)
            return types.CallToolResult(
                content=[types.TextContent(text=text)], structured_content=structured, is_error=failed
            )

        async def on_initialized(ctx, params):
            self.remember(ctx)

        self.server = Server(
            "rappmcp",
            version=__version__,
            instructions=self.instructions(),
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
            on_subscriptions_listen=ListenHandler(self.bus),
        )
        self.server.add_notification_handler("notifications/initialized", types.NotificationParams, on_initialized)
        return self.server


def _warm_kernel():
    """Find (or fetch once) the pinned kernel file before any worker needs it."""
    try:
        return kernel.locate()
    except Exception as error:
        _log(f"kernel: {error}")
        return None


async def serve_stdio(target, timeout=120.0, chat_url=None, poll=1.0, scan_timeout=SCAN_TIMEOUT):
    """Answer initialize at once, serve the healthy tools, and skip a file that hangs.

    The first scan runs in the background. initialize waits for it at most STARTUP_WAIT
    seconds (so instructions usually ride along); tools/list waits for it to finish,
    which is at most --scan-timeout plus a moment.
    """
    app = Rappmcp(target, timeout=timeout, chat_url=chat_url, poll=poll, scan_timeout=scan_timeout)
    server = app.build()
    options = app.options = server.create_initialization_options(NotificationOptions(tools_changed=True))
    async with stdio_server() as (read, write):
        async with anyio.create_task_group() as group:

            async def startup():
                await asyncio.to_thread(_warm_kernel)
                await app.first_scan()

            group.start_soon(startup)
            with anyio.move_on_after(min(STARTUP_WAIT, scan_timeout)):
                await app.ready.wait()
            group.start_soon(app.watch)
            await server.run(read, write, options)
            group.cancel_scope.cancel()
