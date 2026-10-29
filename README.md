# RAPPMCP

One MCP server for a whole folder of RAPP `agent.py` files.

MCP made every capability a server. agent.py makes every capability a file.
One runner gives you both. Drop a file in the folder and it is a tool. No
server per tool, no config per tool.

## Set up

```
git clone <this repo> rappmcp && cd rappmcp
python3 -m venv .venv && .venv/bin/pip install -e .
```

## Use it

Point it at one agent file, or at a folder you choose. It serves nothing you
did not name.

Claude Code:

```
claude mcp add rappmcp -- /path/to/rappmcp/.venv/bin/rappmcp serve /path/to/my-agents
```

Codex:

```
codex mcp add rappmcp -- /path/to/rappmcp/.venv/bin/rappmcp serve /path/to/my-agents
```

Any MCP app:

```json
{
  "mcpServers": {
    "rappmcp": {
      "command": "/path/to/rappmcp/.venv/bin/rappmcp",
      "args": ["serve", "/path/to/my-agents"]
    }
  }
}
```

Options:

- `--timeout 60`: seconds one call may run before it is stopped.
- `--chat-url http://localhost:7071`: also offer `brainstem_chat`, which asks
  the Brainstem at that address through its own `/chat`.

Try it with the two examples: `rappmcp serve examples`.

## How it works

Each agent becomes a tool: its name, description and parameters come from its
metadata, and a call runs `perform()`. Each call runs in its own short-lived
process with a timeout, so a broken agent cannot stop the server. Keep state
on disk. The full mapping is in [docs/BINDING.md](docs/BINDING.md).

RAPPMCP uses the RAPP Brainstem kernel unchanged. `kernel.json` pins it by
commit and checksum, and a test proves the copy in use is byte-identical.

## Test

```
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
