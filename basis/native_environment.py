"""Limit launcher Python overrides to BaSIS, not explicitly selected Conda tools.

This module prepares a child environment. It never changes ``os.environ`` or
activates an environment in the parent process.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys


_REMOVE_VARIABLES = (
    "VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "_OLD_VIRTUAL_PATH",
    "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__",
)
_TARGET_OPTIONS = {"-p", "--prefix", "-n", "--name"}
_FLAG_OPTIONS = {
    "--no-capture-output", "--live-stream", "--debug-wrapper-scripts",
    "--dev", "-v", "--verbose",
}


def is_explicit_conda_run_prefix(command_prefix):
    """Recognize a shell-free Conda prefix with exactly one explicit target.

    Arbitrary wrappers and a complete command embedded in the prefix are not
    interpreted. This is routing detection, not general Conda CLI validation.
    """
    prefix = list(command_prefix)
    if (len(prefix) < 3
            or not all(isinstance(value, str) and "\0" not in value for value in prefix)
            or Path(prefix[0]).name.lower() not in {"conda", "conda.exe"}
            or prefix[1] != "run"):
        return False
    target_count = 0
    index = 2
    while index < len(prefix):
        option = prefix[index]
        if option == "--":
            # A boundary is permitted only at the end of a routing prefix.
            # Embedded commands remain unsupported and must not be interpreted.
            return index == len(prefix) - 1 and target_count == 1
        if option in _TARGET_OPTIONS or option == "--cwd":
            index += 1
            if index >= len(prefix) or not prefix[index] or prefix[index].startswith("-"):
                return False
            if option in _TARGET_OPTIONS:
                target_count += 1
        elif option.startswith(("--prefix=", "--name=", "--cwd=")):
            if not option.split("=", 1)[1]:
                return False
            if not option.startswith("--cwd="):
                target_count += 1
        elif option in _FLAG_OPTIONS or (option.startswith("-v") and set(option[1:]) == {"v"}):
            pass
        else:
            return False
        index += 1
    return target_count == 1


def native_command(command_prefix, arguments):
    """Separate explicit Conda options from the executable and its options.

    Conda's global pre-parser can otherwise consume native flags such as
    ``-version`` as its own ``-v`` option. Older Conda releases do not remove a
    leading ``--`` from their executable vector. An isolated controller-Python
    exec bridge consumes the boundary instead, working with both old and new
    Conda while requiring no Python in the target environment. It immediately
    replaces itself with the native process; no shell command is constructed.
    Unknown wrappers are preserved exactly.
    """
    prefix = list(command_prefix)
    if is_explicit_conda_run_prefix(prefix):
        if prefix[-1] == "--":
            prefix.pop()
        prefix.extend([sys.executable, "-I", "-c",
                       "import os,sys; os.execvpe(sys.argv[2],sys.argv[2:],os.environ)", "--"])
    return prefix + list(arguments)


def _normalized(path):
    return os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))


def native_child_environment(env, command_prefix, *, launcher_prefix=None, base_prefix=None):
    """Return a copied child environment and a limited routing audit.

    The cleanup policy matches the tested native-recovery helper: remove only
    launcher virtualenv bin directories and Python overrides, retain Conda
    activation, resource and offline settings, disable automatic default-env
    activation during the Conda child's shell initialization, and disable Python
    user-site packages for explicitly targeted Conda children. Audit values contain no
    environment override contents; removed PATH directories are recorded.
    """
    clean = dict(env)
    audit = {
        "schema": "basis-native-environment-isolation-v1",
        "applied": False,
        "removed_variable_names": [],
        "removed_path_directories": [],
        "set_variable_names": [],
    }
    if not is_explicit_conda_run_prefix(command_prefix):
        audit["reason"] = "no_explicit_conda_run_target"
        return clean, audit
    launcher_prefix = sys.prefix if launcher_prefix is None else launcher_prefix
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    roots = set()
    if env.get("VIRTUAL_ENV"):
        roots.add(_normalized(env["VIRTUAL_ENV"]))
    if _normalized(launcher_prefix) != _normalized(base_prefix):
        roots.add(_normalized(launcher_prefix))
    blocked = {_normalized(Path(root) / name)
               for root in roots for name in ("bin", "Scripts")}
    if "PATH" in clean:
        kept = []
        for entry in clean["PATH"].split(os.pathsep):
            if entry and _normalized(entry) in blocked:
                audit["removed_path_directories"].append(entry)
            else:
                kept.append(entry)
        clean["PATH"] = os.pathsep.join(kept)
    for name in _REMOVE_VARIABLES:
        if name in clean:
            audit["removed_variable_names"].append(name)
            del clean[name]
    # Conda 26.1.1 computes its run activation before evaluating the shell hook.
    # If this target is already active, the computed reactivation omits
    # CONDA_PREFIX; the hook's automatic base activation then leaves base's
    # identity alongside the target's PATH. Suppress only that automatic step,
    # retaining the inherited activation state and the target's activation hooks.
    # Use the legacy spelling supported by both old Conda and current aliases.
    # Defining both spellings causes Conda's MultipleKeysError, even if equal.
    if "CONDA_AUTO_ACTIVATE" in clean:
        audit["removed_variable_names"].append("CONDA_AUTO_ACTIVATE")
        del clean["CONDA_AUTO_ACTIVATE"]
    clean["CONDA_AUTO_ACTIVATE_BASE"] = "false"
    clean["PYTHONNOUSERSITE"] = "1"
    audit["set_variable_names"] = ["PYTHONNOUSERSITE", "CONDA_AUTO_ACTIVATE_BASE"]
    audit["applied"] = True
    audit["policy"] = ("Remove launcher virtualenv routing and suppress child shell default-env "
                       "auto-activation; retain Conda activation and unrelated settings.")
    return clean, audit
