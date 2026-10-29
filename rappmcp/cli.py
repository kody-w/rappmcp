"""rappmcp serve <file-or-folder> [--timeout SECONDS] [--chat-url URL]"""
import argparse
import os
import sys

import anyio


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rappmcp", description="Serve agent.py files as MCP tools over stdio.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve one agent file or one folder of *_agent.py files.")
    serve.add_argument("path", help="An agent file, or a folder you choose. RAPPMCP serves nothing you did not name.")
    serve.add_argument("--timeout", type=float, default=float(os.environ.get("RAPPMCP_TIMEOUT", 60)),
                       help="Seconds one tool call may run before it is stopped (default 60).")
    serve.add_argument("--chat-url", default=os.environ.get("RAPPMCP_BRAINSTEM_URL"),
                       help="Also offer brainstem_chat, which forwards to this Brainstem's POST /chat.")
    serve.add_argument("--poll", type=float, default=1.0, help="Seconds between folder checks (default 1).")
    args = parser.parse_args(argv)

    from .server import serve_stdio

    try:
        anyio.run(serve_stdio, args.path, args.timeout, args.chat_url, args.poll)
    except FileNotFoundError as error:
        print(f"rappmcp: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0
