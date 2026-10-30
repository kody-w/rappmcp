"""The short-lived worker. The server starts one per tools/list scan and one per tools/call.

    python -m rappmcp.runner list <file-or-folder>
    python -m rappmcp.runner call <agent-file> <tool-name>   (arguments as JSON on stdin)

It prints one JSON object on stdout. Anything the agent prints goes to stderr,
so it can never corrupt the answer.
"""
import json
import os
import signal
import sys
import threading
import time

# Never write __pycache__ anywhere: not into the kernel install, not into the agents' folder.
sys.dont_write_bytecode = True


def _watch_parent():
    """Stop this worker, and whatever its agent started, if the server that started it goes away.

    The server stops its workers on a timeout, a cancel or a disconnect. This covers the rest:
    a server killed outright. The worker runs in its own session, so this kills only that.
    """
    parent = os.getppid()

    def watch():
        while True:
            time.sleep(0.5)
            if os.getppid() != parent:
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                finally:
                    os._exit(1)

    threading.Thread(target=watch, name="rappmcp-parent-watch", daemon=True).start()


def _answer_channel():
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def _list(target):
    """Scan one file or folder. The server scans one file per worker, so a file that
    exits or hangs at import costs only its own tools."""
    from . import loader

    agents, where, quarantined, errors = loader.load(target)
    tools = []
    for name, instance in agents.items():
        tool = loader.tool_of(instance)
        tool["file"] = where[name]
        tool["class"] = type(instance).__name__
        try:
            context = instance.system_context()
        except Exception as error:
            errors.append({"file": os.path.basename(where[name]), "error": f"system_context: {error}"})
            context = None
        if isinstance(context, str) and context.strip():
            tool["context"] = context.strip()
        tools.append(tool)
    return {"tools": tools, "quarantined": quarantined, "errors": errors}


def _structured(result):
    """The JSON object an agent answered with, or None. The text answer stays str(result)."""
    if isinstance(result, dict):
        value = result
    else:
        try:
            value = json.loads(str(result))
        except ValueError:
            return None
    if not isinstance(value, dict):
        return None
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None


def _call(path, name, args):
    from . import loader

    quarantined, errors = [], []
    agents = loader.load_file(path, quarantined, errors)
    agent = agents.get(name)
    if agent is None:
        reason = errors[0]["error"] if errors else f"No agent named {name!r} in {os.path.basename(path)}."
        return {"ok": False, "text": reason}
    if not isinstance(args, dict):
        return {"ok": False, "text": "Error: Tool arguments must be a valid JSON object."}
    try:
        result = agent.perform(**args)
    except Exception as error:
        return {"ok": False, "text": f"Error: {error}"}
    reply = {"ok": True, "text": str(result)}
    structured = _structured(result)
    if structured is not None:
        reply["structured"] = structured
    return reply


def main(argv):
    if os.getpgrp() == os.getpid():  # only in its own session; never kill a group it does not lead
        _watch_parent()
    out = _answer_channel()
    try:
        if len(argv) == 2 and argv[0] == "list":
            reply = _list(argv[1])
        elif len(argv) == 3 and argv[0] == "call":
            raw = sys.stdin.read()
            reply = _call(argv[1], argv[2], json.loads(raw) if raw.strip() else {})
        else:
            reply = {"ok": False, "text": "usage: runner list <path> | runner call <file> <tool>"}
    except Exception as error:
        reply = {"ok": False, "text": f"Error: {type(error).__name__}: {error}"}
    out.write(json.dumps(reply))
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
