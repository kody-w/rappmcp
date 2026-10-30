"""RAPPMCP's loader is brainstem-mcp's: `from rappmcp import loader` gives brainstem_mcp.loader.

Same Brainstem loading rules as before. One shared naming rule now also skips an agent named chat,
job_status, capabilities or setup: those names belong to the Brainstem.
"""
import sys

from brainstem_mcp import loader as _loader

sys.modules[__name__] = _loader
