"""Wingman Wizard's guardrails hold through the real MCP server, over stdio, with a fake wingman-cli.

Never the real Wingman, never Grok Bot's cloud.
"""
import json
import time

import pytest

from conftest import FIXTURES, ROOT, connect, text_of

AGENTS = ROOT / "agents"
FAKE = FIXTURES / "fake-wingman-cli"
BOT = "Helper"
WAITING = {"seq": 9, "author": "bot", "kind": "text", "status": "done", "text": "Action needed: sign in to GitHub"}


@pytest.fixture
def fake(tmp_path):
    log, plan_file = tmp_path / "argv.jsonl", tmp_path / "plan.json"

    class Fake:
        env = {"WINGMAN_CLI": str(FAKE), "FAKE_WINGMAN_LOG": str(log), "FAKE_WINGMAN_PLAN": str(plan_file),
               "WINGMAN_WIZARD_STATE": str(tmp_path / "state")}

        def plan(self, rows=(), **steps):
            bots = [{"id": "id-helper", "name": BOT, "brain": "grokbot-account", "working": False}]
            data = {"read:json:" + BOT: {"stdout": json.dumps({"ok": True, "rows": list(rows)})},
                    "bots:json": {"stdout": json.dumps({"ok": True, "bots": bots})}}
            data.update(steps)
            plan_file.write_text(json.dumps(data))

        def calls(self):
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return Fake()


async def call(session, **args):
    result = await session.call_tool("WingmanWizard", args)
    return result, text_of(result)


@pytest.mark.anyio
async def test_guardrails_hold_through_the_server(fake):
    fake.plan(rows=[WAITING])
    async with connect(AGENTS, env=fake.env) as (session, _):
        _, out = await call(session, action="computer_click", bot=BOT, x=3, y=4)
        assert "This step is yours" in out
        _, out = await call(session, action="send", bot=BOT, text="the code is 4 8 1 5 2 6")
        assert "never does that for you" in out
        _, out = await call(session, action="computer_key", bot=BOT, key="shift+cmd+v")
        assert "clipboard" in out
    assert not any(c[0] in ("send", "computer") for c in fake.calls())


@pytest.mark.anyio
async def test_a_slow_send_says_still_going_before_the_server_stops_it(fake):
    fake.plan(send={"sleep": 40})
    async with connect(AGENTS, "--timeout", "12", env=fake.env) as (session, _):
        started = time.monotonic()
        result, out = await call(session, action="send", bot=BOT, text="what is new")
        assert time.monotonic() - started < 12
    assert not result.is_error, out
    assert "Do not send it again" in out
    assert [c[0] for c in fake.calls()] == ["send"]  # sent once, never again
