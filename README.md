# RAPPMCP

One MCP server for a whole folder of RAPP `agent.py` files.

MCP made every capability a server. agent.py makes every capability a file.
One runner gives you both. Drop a file in the folder and it is a tool. No
server per tool, no config per tool.

## Set up

Python 3.10 or newer, on Linux or macOS. Install it from git with pip or uv:

```
pip install "git+https://github.com/kody-w/rappmcp"
uv tool install "git+https://github.com/kody-w/rappmcp"
```

Either gives you a `rappmcp` command. To run it without installing:
`uvx --from "git+https://github.com/kody-w/rappmcp" rappmcp serve <folder>`.

For work on RAPPMCP itself:

```
git clone https://github.com/kody-w/rappmcp && cd rappmcp
python3 -m venv .venv && .venv/bin/pip install -e .
```

No Brainstem is needed. If `~/.brainstem` has the pinned kernel file, RAPPMCP
uses it. If not, it fetches `basic_agent.py` once over HTTPS from the grail at
the commit in `kernel.json`, checks its sha256, and keeps it in
`~/.cache/rappmcp` (or `$XDG_CACHE_HOME/rappmcp`, or `$RAPPMCP_CACHE`). With
`--kernel /path/to/basic_agent.py` it uses that file.

## Use it

Point it at one agent file, or at a folder you choose. It serves nothing you
did not name.

### Use it from Grok Bot

Grok Bot runs stdio MCP servers on its own computer, a Linux machine, not on
your Mac. So RAPPMCP and the agent folder both live there.

1. Ask the bot to run this on its computer:

   ```
   pip install "git+https://github.com/kody-w/rappmcp" && mkdir -p /home/box/rapp-agents
   ```

   Then have it put your `*_agent.py` files in `/home/box/rapp-agents`.

2. Add this server in Grok Bot:

   ```json
   {"mcpServers":{"rappmcp":{"type":"stdio","command":"rappmcp","args":["serve","/home/box/rapp-agents"],"startupTimeoutSec":60}}}
   ```

Status: not yet tested in Grok Bot itself. The suite checks what Grok Bot
needs: tool names match `^[a-zA-Z0-9_-]{1,64}$`, every input schema is an
object, read-only agents carry read hints, and results are text.

Grok Bot ignores `tools/list_changed`. After you add, change or remove an
agent file, restart the server in Grok Bot (or ask the bot to restart its MCP
servers) to see the new tool list.

### Claude Code

```
claude mcp add rappmcp -- rappmcp serve /path/to/my-agents
```

Tested on macOS: `claude mcp list` shows it Connected.

### Codex

```
codex mcp add rappmcp -- rappmcp serve /path/to/my-agents
```

Not yet tested in a Codex session. `codex mcp add` accepts this line.

### Antigravity CLI (agy)

Not yet tested. Add the "Any MCP app" block below to agy's MCP settings.
(Gemini CLI was retired for personal accounts on 2026-06-18; agy replaces it.)

### Any MCP app

```json
{
  "mcpServers": {
    "rappmcp": {
      "command": "rappmcp",
      "args": ["serve", "/path/to/my-agents"]
    }
  }
}
```

If `rappmcp` is not on the app's PATH, give the full path to the command
(for a clone: `/path/to/rappmcp/.venv/bin/rappmcp`).

### Options

- `--timeout 120`: seconds one call may run before it is stopped.
- `--scan-timeout 10`: seconds one agent file may take to load before it is
  skipped, with a log line. Startup answers at once either way, and
  `tools/list` is ready within this time, well under the 30 to 60 seconds
  most MCP apps wait for a server to start.
- `--kernel PATH`: use this `basic_agent.py`.
- `--chat-url http://localhost:7071`: also offer `brainstem_chat`, which asks
  the Brainstem at that address through its own `/chat`.

Try it with the two examples: `rappmcp serve examples`.

### When the tool list changes

RAPPMCP sends `notifications/tools/list_changed` when a file is added, changed
or removed. Apps that do not act on it (Grok Bot is one) show the new list
after a restart of the server.

## Wingman Wizard

`agents/wingman_wizard_agent.py` drives the Wingman Mac app through its own
`wingman-cli`: `rappmcp serve agents`. It never signs in, authorizes, types
passwords or codes, or pays, and while a bot waits on you it leaves the step
to you. The guardrails are in [docs/WINGMAN.md](docs/WINGMAN.md).

## How it works

Each agent becomes a tool: its name, description and parameters come from its
metadata, and a call runs `perform()`. Each file is scanned, and each call
runs, in its own short-lived process with a timeout, so a broken agent cannot
stop the server or hide the other tools. Keep state on disk. The full mapping is in [docs/BINDING.md](docs/BINDING.md).

RAPPMCP uses the RAPP Brainstem kernel unchanged and only reads it: it never
writes a file (not even `__pycache__`) into a Brainstem install. `kernel.json`
pins it by commit and checksum, and a test proves the copy in use is
byte-identical.

## Test

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
