"""RAPPMCP's kernel module is brainstem-mcp's: `from rappmcp import kernel` gives brainstem_mcp.kernel,
set to RAPPMCP's own pin, rappmcp/kernel.json (the same pin distro.json carries).

RAPPMCP is a distro on brainstem-mcp, the one engine.
"""
import os
import pathlib
import sys

from brainstem_mcp import kernel as _kernel

if not os.environ.get(_kernel.PIN_ENV):
    _kernel.use_pin(pathlib.Path(__file__).resolve().parent / "kernel.json")

sys.modules[__name__] = _kernel
