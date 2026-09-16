"""Load and normalize runtime directory configuration for Notoons.

Configuration values are read from ``config/config.txt`` relative to the
application base directory, with a fallback to ``config.txt`` directly under
that directory.  Environment variables can override values from the file.
The resulting paths are absolute, and the required directories are created
when this module is imported.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
"""Absolute path to the directory containing the application configuration."""


def load_config() -> dict:
    """Load runtime directories from a configuration file and the environment.

    The supported configuration keys are ``outputs_dir``, ``logs_dir``, and
    ``temp_dir``.  Each key may be written as either ``key=value`` or
    ``key:value``.  Blank lines and lines beginning with ``#``, ``;``, or
    ``//`` are ignored.  Unknown keys do not affect the returned mapping.

    Configuration is applied in the following order:

    1. Built-in relative directory names are used as defaults.
    2. ``config/config.txt`` is read from :data:`BASE_DIR` when available.
    3. ``config.txt`` directly under :data:`BASE_DIR` is used as a fallback.
    4. Environment variables override file values.  The preferred names are
       ``NOTOONS_OUTPUTS_DIR``, ``NOTOONS_LOGS_DIR``, and
       ``NOTOONS_TEMP_DIR``; the shorter ``OUTPUTS_DIR``, ``LOGS_DIR``, and
       ``TEMP_DIR`` names are accepted as secondary fallbacks.

    Relative paths are resolved against :data:`BASE_DIR`.  All three
    directories are created with :func:`os.makedirs` before the mapping is
    returned.

    Returns:
        A mapping containing absolute paths under the keys ``outputs_dir``,
        ``logs_dir``, and ``temp_dir``.

    Raises:
        OSError: If a configured directory cannot be created.  Errors while
            reading an existing configuration file are handled locally and
            result in the available defaults or overrides being returned.
    """
    cfg = {
        "outputs_dir": "outputs",
        "logs_dir": "logs",
        "temp_dir": "temp",
    }
    config_file = os.path.join(BASE_DIR, "config", "config.txt")
    if not os.path.isfile(config_file):
        config_file = os.path.join(BASE_DIR, "config.txt")

    if os.path.isfile(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(("#", ";", "//")):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                    elif ":" in line:
                        k, v = line.split(":", 1)
                    else:
                        continue
                    k = k.strip().lower()
                    v = v.strip().strip("\"'")
                    if k in cfg:
                        cfg[k] = v
        except (OSError, UnicodeError, ValueError) as e:
            print(f"[WARN] Impossibile leggere config.txt: {e}")

    cfg["outputs_dir"] = os.getenv("NOTOONS_OUTPUTS_DIR", os.getenv("OUTPUTS_DIR", cfg["outputs_dir"]))
    cfg["logs_dir"] = os.getenv("NOTOONS_LOGS_DIR", os.getenv("LOGS_DIR", cfg["logs_dir"]))
    cfg["temp_dir"] = os.getenv("NOTOONS_TEMP_DIR", os.getenv("TEMP_DIR", cfg["temp_dir"]))

    for k in ["outputs_dir", "logs_dir", "temp_dir"]:
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.abspath(os.path.join(BASE_DIR, cfg[k]))
        os.makedirs(cfg[k], exist_ok=True)

    return cfg


CONFIG = load_config()
"""Runtime configuration loaded once when the application package starts."""
