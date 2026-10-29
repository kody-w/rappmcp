"""The short-lived worker. The server starts one per tools/list scan and one per tools/call.

    python -m rappmcp.runner list <file-or-folder>
    python -m rappmcp.runner call <agent-file> <tool-name>   (arguments as JSON on stdin)

It prints one JSON object on stdout. Anything the agent prints goes to stderr,
so it can never corrupt the answer.
"""
import json
import os
import sys


def _answer_channel():
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def _list(target):
    from . import loader

    agents, where, quarantined, errors = loader.load(target)
    tools, instructions = [], []
    for name, instance in agents.items():
        tool = loader.tool_of(instance)
        tool["file"] = where[name]
        tools.append(tool)
        try:
            context = instance.system_context()
        except Exception as error:
            errors.append({"file": os.path.basename(where[name]), "error": f"system_context: {error}"})
            context = None
        if isinstance(context, str) and context.strip():
            instructions.append(context.strip())
    return {"tools": tools, "quarantined": quarantined, "errors": errors, "instructions": instructions}


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
    return {"ok": True, "text": str(result)}


def main(argv):
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
