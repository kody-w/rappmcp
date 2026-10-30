"""Load agent.py files the way the Brainstem does.

Same rules as the kernel's loader:
- a folder serves every *_agent.py in it, sorted by name (not subfolders);
- a class loads when it is defined in that file, has perform(), is not
  BasicAgent, and its name does not start with _;
- a bad name or bad metadata quarantines that class, the rest still load;
- a constructor that throws stops loading that file (classes before it stay);
- a duplicate name inside one file quarantines every copy; a name already
  taken by an earlier file quarantines the later one.

One difference: RAPPMCP never pip-installs a missing package for an agent. It
reports the missing module instead.

This module runs agent code. The server only calls it inside a subprocess.
"""
import importlib.util
import pathlib
import re

from . import kernel

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def agent_files(target):
    target = pathlib.Path(target).expanduser().resolve()
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.glob("*_agent.py") if p.name != "basic_agent.py")
    raise FileNotFoundError(f"{target} does not exist")


def _schema_problem(schema, path):
    if not isinstance(schema, dict):
        return f"{path} must be a schema object"
    if "type" in schema:
        kind = schema["type"]
        if not (isinstance(kind, str) or (isinstance(kind, list) and kind and all(isinstance(k, str) for k in kind))):
            return f"{path}.type must be a string or array of strings"
    if "description" in schema and not isinstance(schema["description"], str):
        return f"{path}.description must be a string"
    if "required" in schema and (
        not isinstance(schema["required"], list) or not all(isinstance(r, str) for r in schema["required"])
    ):
        return f"{path}.required must be an array of strings"
    if "properties" in schema:
        props = schema["properties"]
        if not isinstance(props, dict):
            return f"{path}.properties must be a dict"
        for key, sub in props.items():
            if not isinstance(key, str) or not isinstance(sub, dict):
                return f"{path}.properties must map string names to schema objects"
            problem = _schema_problem(sub, f"{path}.properties[{key!r}]")
            if problem:
                return problem
    if "items" in schema:
        problem = _schema_problem(schema["items"], f"{path}.items")
        if problem:
            return problem
    for word in ("allOf", "anyOf", "oneOf"):
        if word in schema:
            branches = schema[word]
            if not isinstance(branches, list) or not branches:
                return f"{path}.{word} must be a non-empty array of schema objects"
            for i, branch in enumerate(branches):
                problem = _schema_problem(branch, f"{path}.{word}[{i}]")
                if problem:
                    return problem
    if "not" in schema:
        problem = _schema_problem(schema["not"], f"{path}.not")
        if problem:
            return problem
    extra = schema.get("additionalProperties")
    if extra is not None and not isinstance(extra, bool):
        problem = _schema_problem(extra, f"{path}.additionalProperties")
        if problem:
            return problem
    return None


def problem_with(instance):
    """None when the agent is safe to serve, else a plain reason."""
    name = getattr(instance, "name", None)
    if not isinstance(name, str) or not name:
        return "name is missing or not a non-empty string"
    if not NAME_RE.match(name):
        return f"name {name!r} is not tool-safe (must match ^[a-zA-Z0-9_-]+$)"
    metadata = getattr(instance, "metadata", None)
    if not isinstance(metadata, dict):
        return "metadata is not a dict"
    if "description" in metadata and not isinstance(metadata["description"], str):
        return "metadata['description'] must be a string"
    if "parameters" in metadata:
        params = metadata["parameters"]
        if not isinstance(params, dict):
            return "metadata['parameters'] is not a dict"
        if params.get("type") != "object":
            return "metadata['parameters'].type must be 'object'"
        return _schema_problem(params, "metadata['parameters']")
    return None


def load_file(path, quarantined, errors):
    """Return {name: instance} for one file."""
    path = pathlib.Path(path)
    kernel.install(path.parent)
    found, dupes = {}, set()
    try:
        spec = importlib.util.spec_from_file_location(f"rappmcp_agent_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:
        errors.append({"file": path.name, "error": f"{type(error).__name__}: {error}"})
        return found
    for attr in dir(module):
        cls = getattr(module, attr)
        if not (
            isinstance(cls, type)
            and cls.__module__ == module.__name__
            and hasattr(cls, "perform")
            and attr not in ("BasicAgent", "object")
            and not attr.startswith("_")
        ):
            continue
        try:
            instance = cls()
        except Exception as error:
            # As the kernel does: a constructor that throws ends loading this file.
            # Classes already loaded from it stay; later ones are not tried.
            errors.append({"file": path.name, "error": f"{attr}(): {type(error).__name__}: {error}"})
            break
        reason = problem_with(instance)
        if reason:
            quarantined.append({"file": path.name, "class": attr, "reason": reason})
            continue
        if instance.name in found or instance.name in dupes:
            dupes.add(instance.name)
            found.pop(instance.name, None)
            quarantined.append(
                {"file": path.name, "class": attr, "reason": f"duplicate agent name {instance.name!r} within one file"}
            )
            continue
        found[instance.name] = instance
    return found


def load(target):
    """Load a file or folder. Returns (agents, files_by_name, quarantined, errors)."""
    agents, where, quarantined, errors = {}, {}, [], []
    for path in agent_files(target):
        for name, instance in load_file(path, quarantined, errors).items():
            if name in agents:
                quarantined.append(
                    {
                        "file": path.name,
                        "class": type(instance).__name__,
                        "reason": f"duplicate agent name {name!r}; already registered by an earlier file",
                    }
                )
                continue
            agents[name] = instance
            where[name] = str(path)
    return agents, where, quarantined, errors


def tool_of(instance):
    """MCP tool for one agent, built from the kernel's own to_tool()."""
    fn = instance.to_tool()["function"]
    return {
        "name": fn["name"],
        "description": fn.get("description", ""),
        "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
    }
