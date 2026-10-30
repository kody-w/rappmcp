"""Find the grail's basic_agent.py, prove it is the pinned one, and expose it.

RAPPMCP never edits, forks or patches the kernel. It reads one file,
agents/basic_agent.py, at the commit pinned in kernel.json, and checks its
sha256 before using it. Agents import it the same ways they do inside a
Brainstem: `from agents.basic_agent import BasicAgent` or
`from openrappter.agents.basic_agent import BasicAgent`.
"""
import hashlib
import json
import os
import pathlib
import sys
import types
import urllib.request

# kernel.json ships inside the package, so a pip or uvx install from git has it too.
# The repo root keeps a link to it under the same name.
PIN = json.loads((pathlib.Path(__file__).resolve().parent / "kernel.json").read_text())
BASE = next(f for f in PIN["files"] if f["path"].endswith("agents/basic_agent.py"))
INSTALLED = pathlib.Path(os.path.expanduser(PIN["installed"]["path"]))


def cache_dir():
    """Where a fetched kernel file is kept: $RAPPMCP_CACHE, else $XDG_CACHE_HOME/rappmcp, else ~/.cache/rappmcp."""
    base = os.environ.get("RAPPMCP_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "rappmcp"
    )
    return pathlib.Path(base) / "kernel" / PIN["commit"]


class KernelError(RuntimeError):
    pass


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def pinned_url():
    repo = PIN["repository"].replace("https://github.com/", "")
    return f"https://raw.githubusercontent.com/{repo}/{PIN['commit']}/{BASE['path']}"


def _fetch_pinned():
    target = cache_dir() / "basic_agent.py"
    if target.exists() and sha256(target) == BASE["sha256"]:
        return target
    with urllib.request.urlopen(pinned_url(), timeout=20) as reply:
        data = reply.read()
    if hashlib.sha256(data).hexdigest() != BASE["sha256"]:
        raise KernelError("The kernel file from GitHub does not match kernel.json. Nothing was loaded.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Several workers may fetch at once: write a private file, then swap it in whole.
    part = target.with_name(f".{target.name}.{os.getpid()}.part")
    part.write_bytes(data)
    os.replace(part, target)
    return target


def locate():
    """Return the path of a basic_agent.py whose bytes match the pin.

    In order: $RAPPMCP_KERNEL (set by --kernel), the installed Brainstem's copy,
    the cached copy, then a fresh fetch from GitHub at the pinned commit.
    """
    override = os.environ.get("RAPPMCP_KERNEL")
    if override:
        path = pathlib.Path(os.path.expanduser(override))
        if not path.is_file():
            raise KernelError(f"RAPPMCP_KERNEL points at {path}, which is not a file.")
        if sha256(path) != BASE["sha256"]:
            print(f"[rappmcp] warning: {path} is not the pinned kernel file", file=sys.stderr)
        return path
    if INSTALLED.is_file() and sha256(INSTALLED) == BASE["sha256"]:
        return INSTALLED
    try:
        return _fetch_pinned()
    except KernelError:
        raise
    except Exception as error:
        raise KernelError(
            "Could not find the pinned basic_agent.py. Install a Brainstem, or connect to the "
            f"internet once so RAPPMCP can fetch it ({error})."
        ) from error


_installed_class = None


def install(agent_dir=None):
    """Register agents.basic_agent and openrappter.agents.basic_agent. Returns BasicAgent."""
    global _installed_class
    if _installed_class is None:
        path = locate()
        # Run it from the bytes just read, and never through the import system: that way
        # nothing (no __pycache__, no .pyc) is ever written into the kernel install.
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != BASE["sha256"] and not os.environ.get("RAPPMCP_KERNEL"):
            raise KernelError(f"{path} changed after it was checked. Nothing was loaded.")
        module = types.ModuleType("agents.basic_agent")
        module.__file__ = str(path)
        sys.modules["agents.basic_agent"] = module
        exec(compile(data, str(path), "exec"), module.__dict__)
        _installed_class = module.BasicAgent
        agents_pkg = sys.modules.get("agents") or types.ModuleType("agents")
        agents_pkg.__path__ = []
        agents_pkg.basic_agent = module
        sys.modules["agents"] = agents_pkg
        sys.modules["agents.basic_agent"] = module
        rapper = types.ModuleType("openrappter")
        rapper.__path__ = []
        rapper_agents = types.ModuleType("openrappter.agents")
        rapper_agents.__path__ = []
        rapper_agents.basic_agent = module
        rapper.agents = rapper_agents
        sys.modules.setdefault("openrappter", rapper)
        sys.modules.setdefault("openrappter.agents", rapper_agents)
        sys.modules.setdefault("openrappter.agents.basic_agent", module)
    if agent_dir:
        # Same as a Brainstem: `from agents.other_agent import X` finds sibling files.
        path = str(agent_dir)
        if path not in sys.modules["agents"].__path__:
            sys.modules["agents"].__path__.insert(0, path)
    return _installed_class
