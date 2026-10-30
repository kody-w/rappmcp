"""Wingman Wizard against a fake wingman-cli. Never the real Wingman, never Grok Bot's cloud."""

import importlib.util
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fixtures" / "fake-wingman-cli"
# The pinned kernel's basic_agent.py, found the same way the server finds it: the installed
# Brainstem's copy, the cache, or a sha256-checked fetch. Works on a clean machine.
from rappmcp import kernel as _kernel  # noqa: E402

_kernel.install()

spec = importlib.util.spec_from_file_location("wingman_wizard_agent", ROOT / "agents" / "wingman_wizard_agent.py")
wiz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiz)

BOT = "Helper"
WAITING = {"seq": 9, "author": "bot", "kind": "text", "status": "done", "text": "Action needed: sign in to GitHub"}


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "argv.jsonl"
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    monkeypatch.setenv("WINGMAN_CLI", str(FAKE))
    monkeypatch.setenv("FAKE_WINGMAN_LOG", str(log))
    monkeypatch.setenv("FAKE_WINGMAN_PLAN", str(plan))
    monkeypatch.setenv("WINGMAN_WIZARD_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("RAPPMCP_CALL_TIMEOUT", raising=False)

    class Fake:
        agent = wiz.WingmanWizardAgent()

        def plan(self, **steps):
            plan.write_text(json.dumps({k.replace("__", ":"): v for k, v in steps.items()}))

        def rows(self, rows, others=None, **steps):
            """Rows for BOT; others maps another bot's id to its rows. Every bot is listed by bots --json."""
            others = others or {}
            listed = [{"id": "id-" + BOT.lower(), "name": BOT, "brain": "grokbot-account", "working": False}]
            listed += [{"id": b, "name": b.title(), "brain": "grokbot-account", "working": False} for b in others]
            plan_steps = {"read:json:" + BOT: {"stdout": json.dumps({"ok": True, "rows": rows})},
                          "bots:json": {"stdout": json.dumps({"ok": True, "bots": listed})}}
            for b, their in others.items():
                plan_steps["read:json:" + b] = {"stdout": json.dumps({"ok": True, "rows": their})}
            plan_steps.update({k.replace("__", ":"): v for k, v in steps.items()})
            plan.write_text(json.dumps(plan_steps))

        def calls(self):
            return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

        def run(self, **kw):
            return self.agent.perform(**kw)

    return Fake()


def calm():
    return [{"seq": 1, "author": "bot", "kind": "text", "status": "done", "text": "Here is the page."}]


# --- the agent loads the way the Brainstem loads it ---------------------------------------------

def test_one_public_agent_class_and_valid_metadata():
    public = [n for n, v in vars(wiz).items() if isinstance(v, type) and v.__module__ == wiz.__name__
              and hasattr(v, "perform") and not n.startswith("_")]
    assert public == ["WingmanWizardAgent"]
    agent = wiz.WingmanWizardAgent()
    assert agent.name == "WingmanWizard" == agent.metadata["name"]
    params = agent.metadata["parameters"]
    assert params["type"] == "object" and params["required"] == ["action"]
    assert params["properties"]["action"]["enum"] == wiz.ACTIONS
    assert agent.to_tool()["function"]["name"] == "WingmanWizard"


def test_no_action_reaches_sign_in_or_account():
    forbidden = {"account", "sync", "takeover", "update", "reset", "signin", "sign_in", "login", "authorize"}
    assert not forbidden & {a.split("_", 1)[-1] for a in wiz.ACTIONS}
    assert not forbidden & set(wiz.ACTIONS)


@pytest.mark.parametrize("action", ["account", "sync", "computer_takeover", "computer_update",
                                    "computer_reset", "sign_in", "authorize", ""])
def test_unknown_actions_never_call_the_cli(fake, action):
    assert "cannot do" in fake.run(action=action, bot=BOT)
    assert fake.calls() == []


# --- every action's argv --------------------------------------------------------------------------

@pytest.mark.parametrize("kw, argv", [
    ({"action": "bots"}, ["bots"]),
    ({"action": "read", "bot": BOT}, ["read", "--last", "10", BOT]),
    ({"action": "read", "bot": BOT, "last": 3}, ["read", "--last", "3", BOT]),
    ({"action": "send", "bot": BOT, "text": "--json what is new"}, ["send", "--timeout", "45", BOT, "--", "--json what is new"]),
    ({"action": "computer_look", "bot": BOT}, ["computer", BOT, "look"]),
    ({"action": "computer_wake", "bot": BOT}, ["computer", BOT, "wake"]),
    ({"action": "computer_open", "bot": BOT}, ["computer", BOT, "open"]),
    ({"action": "computer_control", "bot": BOT}, ["computer", BOT, "control"]),
    ({"action": "computer_watch", "bot": BOT}, ["computer", BOT, "watch"]),
    ({"action": "computer_skip", "bot": BOT}, ["computer", BOT, "skip"]),
    ({"action": "computer_snapshot", "bot": BOT, "file": "SHOT"}, ["computer", BOT, "snapshot", "SHOT"]),
])
def test_plain_actions_argv(fake, kw, argv, tmp_path):
    shot = str(tmp_path / "shot.png")
    kw = {k: shot if v == "SHOT" else v for k, v in kw.items()}
    argv = [shot if a == "SHOT" else a for a in argv]
    fake.run(**kw)
    assert fake.calls() == [argv]


@pytest.mark.parametrize("kw, argv", [
    ({"action": "computer_type", "text": "hello world"}, ["computer", BOT, "type", "--", "hello world"]),
    ({"action": "computer_paste", "text": "--not an option"}, ["computer", BOT, "paste", "--", "--not an option"]),
    ({"action": "computer_key", "key": "Ctrl+L"}, ["computer", BOT, "key", "ctrl+l"]),
    ({"action": "computer_key", "key": "A"}, ["computer", BOT, "key", "A"]),
    ({"action": "computer_click", "x": 10, "y": 20}, ["computer", BOT, "click", "10", "20"]),
    ({"action": "computer_done"}, ["computer", BOT, "done"]),
])
def test_input_actions_check_first_then_act(fake, kw, argv):
    fake.rows(calm())
    assert "Done." == fake.run(bot=BOT, **kw)
    calls = fake.calls()
    assert calls[:2] == [["read", "--last", str(wiz.FULL), "--json", BOT], ["bots", "--json"]]
    assert calls[2:] == [argv]


def test_answer_reads_the_question_then_answers(fake):
    fake.rows([{"seq": 2, "author": "bot", "kind": "widget", "status": "done", "text": "", "id": "q1",
                "widget": {"prompt": "Which color?", "options": [{"label": "Red"}, {"label": "Blue"}]}}])
    fake.run(action="answer", bot=BOT, text="2", entry="q1")
    assert fake.calls()[1] == ["answer", "--timeout", "45", "--entry", "q1", BOT, "--", "2"]


def test_snapshot_default_file_is_a_png(fake):
    out = fake.run(action="computer_snapshot", bot=BOT)
    path = fake.calls()[0][3]
    assert path.endswith(".png") and os.path.isabs(path) and path in out
    assert "must end in .png" in fake.run(action="computer_snapshot", bot=BOT, file="/tmp/a.txt")


# --- exit codes ------------------------------------------------------------------------------------

def test_exit_0_returns_the_output(fake):
    fake.plan(bots={"stdout": "b1  grok  Helper"})
    assert fake.run(action="bots") == "b1  grok  Helper"


def test_exit_1_says_wingman_said_no_in_its_words(fake):
    fake.plan(computer__wake={"exit": 1, "stderr": "Another wake is running."})
    assert fake.run(action="computer_wake", bot=BOT) == "Wingman said no: Another wake is running."


def test_exit_2_is_a_command_line_problem(fake):
    fake.plan(read={"exit": 2, "stderr": "read takes one bot"})
    assert "did not accept" in fake.run(action="read", bot=BOT)


def test_exit_3_says_open_wingman(fake):
    fake.plan(bots={"exit": 3, "stderr": "Wingman is not running. Open it, then try again."})
    assert "Open Wingman on this Mac" in fake.run(action="bots")


def test_missing_cli_says_open_wingman(fake, monkeypatch, tmp_path):
    monkeypatch.delenv("WINGMAN_CLI")
    monkeypatch.setattr(wiz, "APPS", [str(tmp_path / "none/wingman-cli")])
    monkeypatch.setattr(wiz, "DEV_BUILDS", [str(tmp_path / "none/*/wingman-cli")])
    assert "Open Wingman on this Mac" in fake.run(action="bots")


def test_exit_4_on_send_says_still_going_never_resend(fake):
    fake.plan(send={"exit": 4, "stderr": "Helper is still answering"})
    out = fake.run(action="send", bot=BOT, text="what is new")
    assert out == wiz.STILL_GOING and "Do not send it again" in out
    assert len(fake.calls()) == 1  # no retry


def test_exit_4_elsewhere_is_try_again(fake):
    fake.plan(computer__look={"exit": 4})
    assert "try again" in fake.run(action="computer_look", bot=BOT)


# --- the help-request lock -------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    {"action": "computer_type", "text": "hello"}, {"action": "computer_paste", "text": "hello"},
    {"action": "computer_key", "key": "enter"}, {"action": "computer_click", "x": 1, "y": 2},
    {"action": "computer_done"},
])
def test_open_help_request_locks_input(fake, kw):
    fake.rows(calm() + [WAITING])
    assert fake.run(bot=BOT, **kw) == wiz.YOURS
    assert {c[0] for c in fake.calls()} <= {"read", "bots"}  # only the check ran


def test_ended_help_request_unlocks(fake):
    fake.rows([dict(WAITING, text="Done: sign in to GitHub"), calm()[0]])
    assert fake.run(action="computer_key", bot=BOT, key="enter") == "Done."


def test_lock_fails_closed_when_rows_cannot_be_read(fake):
    fake.rows(calm(), read__json__Helper={"exit": 3})
    assert wiz.YOURS in fake.run(action="computer_type", bot=BOT, text="hello")
    fake.rows(calm(), read__json__Helper={"stdout": "not json"})
    assert wiz.YOURS in fake.run(action="computer_click", bot=BOT, x=1, y=1)
    fake.rows(calm(), bots__json={"stdout": "not json"})
    assert wiz.YOURS in fake.run(action="computer_click", bot=BOT, x=1, y=1)
    assert {c[0] for c in fake.calls()} <= {"read", "bots"}


def test_skip_is_allowed_while_waiting(fake):
    fake.rows([WAITING])
    fake.run(action="computer_skip", bot=BOT)
    assert fake.calls() == [["computer", BOT, "skip"]]


# --- never sign in, authorize, type secrets or pay -------------------------------------------------

@pytest.mark.parametrize("text", [
    "my password is hunter2", "482913", "123 456", "Tr0ub4dor&3", "sign in to github", "log in please",
    "authorize the app", "enter the verification code", "pay the invoice", "card number 4111", "the 2FA code",
])
@pytest.mark.parametrize("action", ["send", "computer_type", "computer_paste"])
def test_secrets_and_sign_in_text_refused_before_any_call(fake, action, text):
    fake.rows(calm())
    assert fake.run(action=action, bot=BOT, text=text) == wiz.SIGN_IN
    assert fake.calls() == []


def test_bot_asking_to_sign_in_blocks_input(fake):
    fake.rows([{"seq": 3, "author": "bot", "kind": "text", "status": "done",
                "text": "Please enter your password to continue."}])
    assert fake.run(action="computer_type", bot=BOT, text="hello") == wiz.SIGN_IN
    assert fake.run(action="computer_click", bot=BOT, x=5, y=5) == wiz.SIGN_IN
    assert {c[0] for c in fake.calls()} <= {"read", "bots"}


def test_cmd_v_refused_because_the_clipboard_may_hold_a_password(fake):
    assert "clipboard" in fake.run(action="computer_key", bot=BOT, key="cmd+v")
    assert fake.calls() == []


AUTH_Q = {"seq": 4, "author": "bot", "kind": "widget", "status": "done", "text": "", "id": "q9",
          "widget": {"prompt": "Grant access to your Google account?",
                     "options": [{"label": "Authorize"}, {"label": "No, not now"}]}}


def test_answer_cannot_pick_authorize(fake):
    fake.rows([AUTH_Q])
    assert fake.run(action="answer", bot=BOT, text="1") == wiz.SIGN_IN
    assert fake.run(action="answer", bot=BOT, text="#1") == wiz.SIGN_IN
    assert fake.run(action="answer", bot=BOT, text="authorize") == wiz.SIGN_IN
    assert all(c[0] == "read" for c in fake.calls())


def test_answer_can_decline_an_authorize_question(fake):
    fake.rows([AUTH_Q])
    fake.run(action="answer", bot=BOT, text="2")
    assert fake.calls()[-1] == ["answer", "--timeout", "45", BOT, "--", "2"]


@pytest.mark.parametrize("bot", ["", "--json", "-x", "a\nb"])
def test_bad_bot_names_never_reach_the_cli(fake, bot):
    assert "Name the bot" in fake.run(action="read", bot=bot)
    assert fake.calls() == []


def test_option_like_key_and_bad_click_refused(fake):
    assert "one key" in fake.run(action="computer_key", bot=BOT, key="--json")
    assert "Give x and y" in fake.run(action="computer_click", bot=BOT, x=-1, y=3)
    assert fake.calls() == []


def test_cli_override_wins_and_must_be_executable(monkeypatch, tmp_path):
    monkeypatch.setenv("WINGMAN_CLI", str(FAKE))
    assert wiz.find_cli() == (str(FAKE), None)
    plain = tmp_path / "not-executable"
    plain.write_text("")
    monkeypatch.setenv("WINGMAN_CLI", str(plain))
    assert wiz.find_cli()[0] is None


# --- H1: the help-request lock sees every waiting request --------------------------------------------

def test_lock_reads_the_whole_conversation(fake):
    """A request followed by 30 newer rows (sends are not locked) still locks."""
    newer = [dict(calm()[0], seq=10 + i, author="me", text="msg %d" % i) for i in range(40)]
    fake.rows([WAITING] + newer)
    assert fake.run(action="computer_click", bot=BOT, x=1, y=1) == wiz.YOURS
    assert not any(c[0] == "computer" for c in fake.calls())
    assert fake.calls()[0][:3] == ["read", "--last", str(wiz.FULL)]


def test_lock_fails_closed_when_the_conversation_is_longer_than_it_can_read(fake):
    fake.rows([dict(calm()[0], seq=i) for i in range(wiz.FULL)])
    assert wiz.YOURS in fake.run(action="computer_type", bot=BOT, text="hello")
    assert not any(c[0] == "computer" for c in fake.calls())


@pytest.mark.parametrize("kw", [
    {"action": "computer_type", "text": "hello"}, {"action": "computer_click", "x": 1, "y": 2},
    {"action": "computer_done"}, {"action": "computer_key", "key": "enter"},
])
def test_a_request_on_a_shared_computer_locks_input(fake, kw):
    """The bot shows another bot's computer: the request row lives in the other bot's conversation."""
    fake.rows(calm(), others={"owner-1": [WAITING]})
    assert fake.run(bot=BOT, **kw) == wiz.YOURS
    assert ["read", "--last", str(wiz.FULL), "--json", "owner-1"] in fake.calls()
    assert not any(c[0] == "computer" for c in fake.calls())


# --- H2: codes and passwords, however they are split ------------------------------------------------

@pytest.mark.parametrize("action, text", [
    ("send", "the code is 4 8 1 5 2 6"), ("send", "my pw is hunter22"), ("send", "use Hunter2Hunter2 here"),
    ("computer_type", "Hunter2Hunter2"), ("computer_type", "Hunter2.x!"), ("computer_paste", "hunter22"),
    ("computer_type", "481 526"), ("send", "pin 4815"), ("computer_type", "4.8.1.5.2.6"),
])
def test_codes_and_passwords_in_other_shapes_are_refused(fake, action, text):
    fake.rows(calm())
    assert fake.run(action=action, bot=BOT, text=text) == wiz.SIGN_IN
    assert not any(c[0] in ("send", "computer") for c in fake.calls())


@pytest.mark.parametrize("text", ["hello world", "https://github.com/kody-w/rappmcp2", "github.com",
                                  "what is new in 2026?", "Open the settings page", "ok"])
def test_ordinary_text_still_goes(fake, text):
    fake.rows(calm())
    assert fake.run(action="computer_type", bot=BOT, text=text) == "Done."


def test_a_code_typed_in_pieces_is_refused(fake):
    fake.rows(calm())
    assert fake.run(action="computer_type", bot=BOT, text="12") == "Done."
    assert fake.run(action="computer_type", bot=BOT, text="34") == wiz.PIECEMEAL
    typed = [c for c in fake.calls() if c[:3] == ["computer", BOT, "type"]]
    assert typed == [["computer", BOT, "type", "--", "12"]]


def test_a_code_entered_key_by_key_is_refused(fake):
    fake.rows(calm())
    outs = [fake.run(action="computer_key", bot=BOT, key=k) for k in "4815"]
    assert outs[:3] == ["Done."] * 3 and outs[3] == wiz.PIECEMEAL


def test_a_password_entered_key_by_key_is_refused(fake):
    fake.rows(calm())
    outs = [fake.run(action="computer_key", bot=BOT, key=k) for k in "hunter"]
    assert wiz.PIECEMEAL in outs
    assert fake.run(action="computer_key", bot=BOT, key="enter") == "Done."  # named keys are not pieces


# --- H3: every way of saying paste the clipboard ------------------------------------------------------

@pytest.mark.parametrize("key", ["cmd+v", "cmd+shift+v", "shift+cmd+v", "cmd+cmd+v", "Command+V", "ctrl+v",
                                 "ctrl+shift+v", "CMD+V"])
def test_every_clipboard_paste_key_is_refused(fake, key):
    fake.rows(calm())
    assert "clipboard" in fake.run(action="computer_key", bot=BOT, key=key)
    assert fake.calls() == []


def test_other_cmd_keys_still_go(fake):
    fake.rows(calm())
    assert fake.run(action="computer_key", bot=BOT, key="cmd+a") == "Done."


# --- H4: the answer guard reads answers the way Wingman does -----------------------------------------

AUTH_VALUE_Q = {"seq": 5, "author": "bot", "kind": "widget", "status": "done", "text": "", "id": "q7",
                "widget": {"prompt": "How should I go on?",
                           "options": [{"label": "Authorize", "value": "go"}, {"label": "Wait", "value": "wait"}]}}
STEPS_Q = {"seq": 6, "author": "bot", "kind": "widget", "status": "done", "text": "", "id": "q8",
           "widget": {"prompt": "Two quick choices", "options": [],
                      "steps": [{"prompt": "Color?", "options": [{"label": "Red"}, {"label": "Blue"}]},
                                {"prompt": "Next?", "options": [{"label": "Authorize the app"}, {"label": "Later"}]}]}}


@pytest.mark.parametrize("rows, kw", [
    ([AUTH_Q], {"text": "A"}),
    ([AUTH_Q], {"text": "a"}),
    ([AUTH_VALUE_Q], {"text": "go"}),
    ([AUTH_VALUE_Q], {"text": "1"}),
    ([STEPS_Q], {"text": "1; 1"}),
    ([AUTH_Q], {"text": "1", "entry": "Q9"}),
    ([AUTH_Q], {"text": "1", "entry": "cloud-abc-123"}),
    ([AUTH_Q], {"text": "#1"}),
])
def test_answer_guard_cannot_be_talked_round(fake, rows, kw):
    fake.rows(rows)
    out = fake.run(action="answer", bot=BOT, **kw)
    assert out == wiz.SIGN_IN or "cannot find question" in out
    assert all(c[0] == "read" for c in fake.calls())


def test_answer_still_works_for_a_plain_step_question(fake):
    fake.rows([dict(STEPS_Q, widget=dict(STEPS_Q["widget"], steps=[STEPS_Q["widget"]["steps"][0]] * 2))])
    fake.run(action="answer", bot=BOT, text="1; 2")
    assert fake.calls()[-1][0] == "answer"


# --- H5: a slow send says still going, inside the server's timeout -----------------------------------

def test_send_fits_inside_the_server_timeout(fake, monkeypatch):
    monkeypatch.setenv("RAPPMCP_CALL_TIMEOUT", "12")
    fake.plan(send={"sleep": 30})
    import time as clock
    started = clock.monotonic()
    out = fake.run(action="send", bot=BOT, text="what is new")
    assert out == wiz.STILL_GOING and clock.monotonic() - started < 11
    sent = fake.calls()[0]
    assert sent[:2] == ["send", "--timeout"] and int(sent[2]) < 12


# --- M6: finding the right wingman-cli ----------------------------------------------------------------

def _cli(path, can_drive):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"#!/bin/sh\nexit 0\n# usage: " + (b"click <x> <y>" if can_drive else b"look | wake") + b"\n")
    path.chmod(0o755)
    return str(path)


def test_prefers_a_build_that_can_type_and_click(monkeypatch, tmp_path):
    monkeypatch.delenv("WINGMAN_CLI", raising=False)
    old = _cli(tmp_path / "Applications/Wingman.app/Contents/MacOS/wingman-cli", False)
    new = _cli(tmp_path / "dev/b1/out/Products/Release/wingman-cli", True)
    monkeypatch.setattr(wiz, "APPS", [old])
    monkeypatch.setattr(wiz, "DEV_BUILDS", [str(tmp_path / "dev/*/out/Products/*/wingman-cli")])
    assert wiz.find_cli() == (new, None)
    os.remove(new)
    assert wiz.find_cli() == (old, None)
    agent = wiz.WingmanWizardAgent()
    assert agent.perform(action="computer_type", bot=BOT, text="hello") == wiz.TOO_OLD


def test_a_bad_override_says_so_not_open_wingman(fake, monkeypatch, tmp_path):
    plain = tmp_path / "not-executable"
    plain.write_text("")
    monkeypatch.setenv("WINGMAN_CLI", str(plain))
    out = fake.run(action="bots")
    assert "WINGMAN_CLI" in out and "Open Wingman" not in out


# --- L3: odd arguments ---------------------------------------------------------------------------------

def test_numeric_entry_is_handled(fake):
    fake.rows([dict(AUTH_Q, id="5")])
    assert fake.run(action="answer", bot=BOT, text="1", entry=5) == wiz.SIGN_IN


def test_snapshot_never_replaces_a_file(fake, tmp_path):
    existing = tmp_path / "keep.png"
    existing.write_bytes(b"mine")
    assert "never replaces" in fake.run(action="computer_snapshot", bot=BOT, file=str(existing))
    assert fake.calls() == [] and existing.read_bytes() == b"mine"
