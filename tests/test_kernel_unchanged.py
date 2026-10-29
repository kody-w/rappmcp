"""Contract: RAPPMCP uses the grail kernel unchanged.

The pinned kernel file at kernel.json's commit, the installed Brainstem's copy,
and the copy RAPPMCP actually loads must be byte-identical. RAPPMCP keeps no
copy of its own. Mirrors brainstem-mcp's contract tests against the grail.
"""
import hashlib
import inspect
import json
import subprocess
import urllib.request

import pytest

from conftest import ROOT
from rappmcp import kernel

PIN = json.loads((ROOT / "kernel.json").read_text())
FILE = PIN["files"][0]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_pin_is_well_formed():
    assert PIN["repository"] == "https://github.com/kody-w/rapp-installer"
    assert len(PIN["commit"]) == 40 and int(PIN["commit"], 16) >= 0
    assert FILE["path"] == "rapp_brainstem/agents/basic_agent.py" and len(FILE["sha256"]) == 64


def test_installed_kernel_matches_the_pin():
    if not kernel.INSTALLED.is_file():
        pytest.skip("no Brainstem installed on this machine")
    data = kernel.INSTALLED.read_bytes()
    assert digest(data) == FILE["sha256"] and len(data) == FILE["bytes"]


def test_grail_at_the_pinned_commit_matches_installed():
    try:
        with urllib.request.urlopen(kernel.pinned_url(), timeout=20) as reply:
            upstream = reply.read()
    except Exception as error:
        pytest.skip(f"GitHub not reachable: {error}")
    assert digest(upstream) == FILE["sha256"]
    if kernel.INSTALLED.is_file():
        assert upstream == kernel.INSTALLED.read_bytes()


def test_the_loaded_base_class_is_the_pinned_file():
    base = kernel.install()
    assert digest(open(inspect.getsourcefile(base), "rb").read()) == FILE["sha256"]
    assert base.__name__ == "BasicAgent"


def test_rappmcp_keeps_no_copy_of_the_kernel():
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
    untracked = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if ".venv" not in p.parts]
    for name in tracked + untracked:
        assert not name.endswith("basic_agent.py"), name
        assert not name.endswith("brainstem.py"), name


def test_serving_does_not_touch_the_kernel():
    if not kernel.INSTALLED.is_file():
        pytest.skip("no Brainstem installed on this machine")
    before = kernel.INSTALLED.stat()
    subprocess.run(
        [__import__("sys").executable, "-m", "rappmcp.runner", "list", str(ROOT / "examples")],
        cwd=str(ROOT), capture_output=True, check=True,
    )
    after = kernel.INSTALLED.stat()
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
