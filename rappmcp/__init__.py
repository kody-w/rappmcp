"""RAPPMCP: one MCP server for a whole folder of RAPP agent.py files.

RAPPMCP is a Brainstem distro on brainstem-mcp, the one engine: distro.json pins the kernel and
RAPPMCP's agent files by sha256. `rappmcp serve <folder>` is a thin alias over that engine.
"""
import os

__version__ = "0.2.0"

# Older settings keep working: a fetched kernel file is still kept in ~/.cache/rappmcp
# (or $XDG_CACHE_HOME/rappmcp, or $RAPPMCP_CACHE), as the README has always said.
if not (os.environ.get("RAPPMCP_CACHE") or os.environ.get("BRAINSTEM_MCP_CACHE")):
    os.environ["RAPPMCP_CACHE"] = os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "rappmcp")
