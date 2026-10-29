"""brainstem_chat forwards to an existing Brainstem's POST /chat, unchanged.

Uses a fake Brainstem on 127.0.0.1. It never talks to a real one.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import EXAMPLES, connect, text_of


@pytest.fixture
def fake_brainstem():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            reply = json.dumps({"response": "hello from the fake brainstem", "agent_logs": "", "session_id": "s1"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(reply.encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()


@pytest.mark.anyio
async def test_chat_forwards_and_returns_response_field(fake_brainstem):
    url, seen = fake_brainstem
    async with connect(EXAMPLES, "--chat-url", url) as (session, _):
        assert "brainstem_chat" in [t.name for t in (await session.list_tools()).tools]
        result = await session.call_tool("brainstem_chat", {"user_input": "hi", "session_id": "s1"})
    assert text_of(result) == "hello from the fake brainstem" and not result.is_error
    assert seen == [("/chat", {"user_input": "hi", "session_id": "s1"})]


@pytest.mark.anyio
async def test_no_chat_tool_unless_asked():
    async with connect(EXAMPLES) as (session, _):
        assert "brainstem_chat" not in [t.name for t in (await session.list_tools()).tools]
