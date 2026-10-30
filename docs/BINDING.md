# Binding: agent.py to MCP

MCP made every capability a server. agent.py makes every capability a file.
One runner gives you both: RAPPMCP is one MCP server for a whole folder of
agent.py files.

## The mapping

| agent.py (RAPP Brainstem)        | MCP                                         |
|----------------------------------|---------------------------------------------|
| `name` (normally the same as `metadata.name`) | tool `name`                    |
| `metadata.description`           | tool `description`                          |
| `metadata.parameters`            | tool `inputSchema` (default: empty object)  |
| `perform(**arguments)`           | `tools/call`                                |
| return value, as `str(...)`      | one `text` content block                    |
| an exception in `perform`        | result with `isError: true`, text `Error: <message>` |
| `system_context()`, if it returns text | server `instructions`, updated on each rescan |
| a file added, changed or removed | `notifications/tools/list_changed`, once per client |

The tool comes from the kernel's own `BasicAgent.to_tool()`, so the name,
description and schema are exactly what a Brainstem sends to its model. When
`metadata.name` and the agent's `name` differ, the agent's `name` wins, as in a
Brainstem: it is the name the kernel sends and the name calls are routed by.

A call to a tool that does not exist is a JSON-RPC error, `-32602`
(`Unknown tool: <name>`), as MCP clients expect. An error inside `perform` is a
tool result with `isError: true`.

## Which agents load

The same rules as the Brainstem's loader:

- A folder serves every `*_agent.py` in it, sorted by name. Subfolders are not
  served. A single named file serves just that file.
- A class loads when it is defined in that file, has `perform()`, is not
  `BasicAgent`, and its name does not start with `_`.
- A name that is not tool-safe (`^[a-zA-Z0-9_-]+$`) or bad metadata
  quarantines that class. The rest still load.
- A constructor that throws stops loading that file. Classes before it in
  the file stay; later ones are not tried.
- Two classes with one name in one file: both are quarantined. A name an
  earlier file already took: the later one is quarantined.
- Agents import the base class as `from agents.basic_agent import BasicAgent`
  (or `openrappter.agents.basic_agent`), and siblings as `agents.<file>`.

Two differences, both on purpose. RAPPMCP never pip-installs a missing
package; it reports the missing module. And it has no cloud storage shims
(`utils.azure_file_storage`); an agent that needs them should run in a
Brainstem.

## Isolation

The server never imports agent code. A short-lived worker scans the folder,
and a fresh worker runs each `tools/call`, the way a Brainstem reloads its
agents on every request. A crash, a hang or stray printing costs that one
call. The server keeps going.

- Each call has a timeout, 120 seconds by default (`--timeout`). The worker
  reads it as `RAPPMCP_CALL_TIMEOUT`, so a slow agent can answer before it.
- A call ends when its worker exits. A helper the agent starts in the
  background keeps running; it is the agent's business.
- A timeout, a cancel from the client, a disconnect, or a killed server stops
  the worker and everything it started.
- A stopped call never claims nothing happened. The agent may have done part
  of its work (a message may already be sent), so the result says to check
  before trying again.
- The worker runs from RAPPMCP's own folder. A helper file in the served
  folder (a `json.py`) can never replace a module RAPPMCP imports.

So an agent keeps no memory between calls. State lives on disk, or in the
app the agent drives. A tool reloaded every call keeps its state on disk.

## The kernel

RAPPMCP is userland: a distro that wraps the unchanged grail
(`kody-w/rapp-installer`). It never edits, forks or patches it. `kernel.json`
pins the grail commit and the sha256 of the one file RAPPMCP uses,
`agents/basic_agent.py`. At start it uses the installed Brainstem's copy if the
bytes match, or fetches the file at the pinned commit and checks it. The
contract test proves the pinned file, the installed file and the loaded file
are byte-identical.

## /chat

RAPPMCP does not touch a Brainstem's `/chat`. With `--chat-url`, it adds one
tool, `brainstem_chat`, that sends `{"user_input", "session_id"}` to that
Brainstem's `POST /chat` unchanged and returns its `response` field.
