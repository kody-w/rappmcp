"""rappmcp serve <file-or-folder> [--timeout SECONDS] [--scan-timeout SECONDS] [--kernel PATH] [--chat-url URL]"""
import argparse
import os
import sys

import anyio


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rappmcp", description="Serve agent.py files as MCP tools over stdio.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve one agent file or one folder of *_agent.py files.")
    serve.add_argument("path", help="An agent file, or a folder you choose. RAPPMCP serves nothing you did not name.")
    serve.add_argument("--timeout", type=float, default=float(os.environ.get("RAPPMCP_TIMEOUT", 120)),
                       help="Seconds one tool call may run before it is stopped (default 120). Agents read it as RAPPMCP_CALL_TIMEOUT.")
    serve.add_argument("--chat-url", default=os.environ.get("RAPPMCP_BRAINSTEM_URL"),
                       help="Also offer brainstem_chat, which forwards to this Brainstem's POST /chat.")
    serve.add_argument("--scan-timeout", type=float, default=float(os.environ.get("RAPPMCP_SCAN_TIMEOUT", 10)),
                       help="Seconds one agent file may take to load at startup or on a rescan before it is "
                            "skipped (default 10). Keep it well under your MCP app's startup wait.")
    serve.add_argument("--kernel", default=None,
                       help="Use this basic_agent.py instead of the installed Brainstem's or the fetched pinned copy.")
    serve.add_argument("--poll", type=float, default=1.0, help="Seconds between folder checks (default 1).")
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True  # RAPPMCP never writes into the kernel install or the agents' folder
    if args.kernel:
        os.environ["RAPPMCP_KERNEL"] = os.path.abspath(os.path.expanduser(args.kernel))

    from .server import serve_stdio

    try:
        anyio.run(serve_stdio, args.path, args.timeout, args.chat_url, args.poll, args.scan_timeout)
    except FileNotFoundError as error:
        print(f"rappmcp: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0
