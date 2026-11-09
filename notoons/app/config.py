"""Load and normalize runtime directory configuration for Notoons.

    Configuration values are read from ``config/config.txt`` relative to the
    application base directory, with fallbacks to ``config.txt`` under the
    application directory and repository root.  Environment variables can
    override values from the file.
The resulting paths are absolute, and the required directories are created
when this module is imported.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
"""Absolute path to the directory containing the application configuration."""


def _parse_output_dirs(value):
    """Return configured output directories as named path records."""
    if isinstance(value, list):
        entries = value
    else:
        text = str(value).strip()
        try:
            entries = json.loads(text) if text.startswith("[") else [text]
        except json.JSONDecodeError as exc:
            raise ValueError("outputs_dir must be a JSON array") from exc

    if not entries:
        raise ValueError("outputs_dir must contain at least one directory")

    output_dirs = []
    for entry in entries:
        if isinstance(entry, str):
            nickname, path = entry, entry
        elif isinstance(entry, dict):
            nickname = entry.get("nickname")
            path = entry.get("path")
        else:
            raise ValueError("Each outputs_dir entry must contain nickname and path")
        if not isinstance(nickname, str) or not nickname.strip() or not isinstance(path, str) or not path.strip():
            raise ValueError("Each outputs_dir entry must contain nickname and path")
        if any(item["nickname"] == nickname.strip() for item in output_dirs):
            raise ValueError(f"Duplicate outputs_dir nickname: {nickname.strip()}")
        output_dirs.append({"nickname": nickname.strip(), "path": path.strip()})
    return output_dirs


def load_config() -> dict:
    """Load runtime directories from a configuration file and the environment.

    The supported configuration keys are ``outputs_dir`` (a JSON array of
    ``{"nickname": "...", "path": "..."}`` objects), ``logs_dir``,
    ``temp_dir``, ``server_log_file``, ``server_log_level``,
    ``oidc_issuer_url``, ``oidc_client_id``,
    ``oidc_client_secret``, ``oidc_redirect_uri``, ``oidc_session_secret``,
    ``oidc_scopes``, and ``oidc_cookie_secure``.  Each key may be written as
    either ``key=value`` or ``key:value``.  Blank lines and lines beginning
    with ``#``, ``;``, or ``//`` are ignored.  Unknown keys do not affect the
    returned mapping.

    Configuration is applied in the following order:

    1. Built-in relative directory names are used as defaults.
    2. ``config/config.txt`` is read from :data:`BASE_DIR` when available.
    3. ``config.txt`` directly under :data:`BASE_DIR` is used as a fallback.
     4. Environment variables override file values.  ``NOTOONS_*`` names are
         preferred for all settings; legacy short names remain supported for
         the directory settings.

    Relative paths are resolved against :data:`BASE_DIR`.      Configured directories and the parent directory for the server log file
    are created with :func:`os.makedirs` before the mapping is returned.

    Returns:
        A mapping containing normalized output, log, and temporary paths and
        the configured server log file and level.

    Raises:
        OSError: If a configured directory cannot be created.  Errors while
            reading an existing configuration file are handled locally and
            result in the available defaults or overrides being returned.
    """
    cfg = {
        "outputs_dir": [{"nickname": "Default", "path": "outputs"}],
        "logs_dir": "logs",
        "temp_dir": "temp",
        "server_log_file": "server.log",
        "server_log_level": "INFO",
        "oidc_issuer_url": "",
        "oidc_client_id": "",
        "oidc_client_secret": "",
        "oidc_redirect_uri": "",
        "oidc_post_logout_redirect_uri": "",
        "oidc_session_secret": "",
        "oidc_scopes": "openid profile email",
        "oidc_cookie_secure": "true",
    }
    config_file = os.path.join(BASE_DIR, "config", "config.txt")
    if not os.path.isfile(config_file):
        config_file = os.path.join(BASE_DIR, "config.txt")
    if not os.path.isfile(config_file):
        config_file = os.path.join(os.path.dirname(BASE_DIR), "config.txt")

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
                        try:
                            cfg[k] = _parse_output_dirs(v) if k == "outputs_dir" else v
                        except ValueError as exc:
                            logger.warning("Invalid outputs_dir configuration: %s", exc)
        except (OSError, UnicodeError, ValueError) as e:
            logger.warning("Unable to read config.txt: %s", e)

    outputs_override = os.getenv("NOTOONS_OUTPUTS_DIR", os.getenv("OUTPUTS_DIR"))
    if outputs_override is not None:
        try:
            cfg["outputs_dir"] = _parse_output_dirs(outputs_override)
        except ValueError as exc:
            logger.warning("Invalid outputs directory override: %s", exc)
    cfg["logs_dir"] = os.getenv("NOTOONS_LOGS_DIR", os.getenv("LOGS_DIR", cfg["logs_dir"]))
    cfg["temp_dir"] = os.getenv("NOTOONS_TEMP_DIR", os.getenv("TEMP_DIR", cfg["temp_dir"]))
    cfg["server_log_file"] = os.getenv(
        "NOTOONS_SERVER_LOG_FILE", cfg["server_log_file"]
    ).strip()
    cfg["server_log_level"] = os.getenv("NOTOONS_SERVER_LOG_LEVEL", cfg["server_log_level"])
    level_name = str(cfg["server_log_level"]).strip().upper()
    level = logging.getLevelNamesMapping().get(level_name)
    if not isinstance(level, int):
        raise ValueError(f"Invalid server_log_level: {cfg['server_log_level']}")
    cfg["server_log_level"] = logging.getLevelName(level)
    if not str(cfg["server_log_file"]).strip():
        raise ValueError("server_log_file must not be empty")
    for key in (
        "oidc_issuer_url",
        "oidc_client_id",
        "oidc_client_secret",
        "oidc_redirect_uri",
        "oidc_post_logout_redirect_uri",
        "oidc_session_secret",
        "oidc_scopes",
        "oidc_cookie_secure",
    ):
        cfg[key] = os.getenv(f"NOTOONS_{key.upper()}", cfg[key])

    for output_dir in cfg["outputs_dir"]:
        path = output_dir["path"]
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(BASE_DIR, path))
        output_dir["path"] = path
        os.makedirs(path, exist_ok=True)
    for k in ["logs_dir", "temp_dir"]:
        if not os.path.isabs(cfg[k]):
            cfg[k] = os.path.abspath(os.path.join(BASE_DIR, cfg[k]))
        os.makedirs(cfg[k], exist_ok=True)

    if not os.path.isabs(cfg["server_log_file"]):
        cfg["server_log_file"] = os.path.join(cfg["logs_dir"], cfg["server_log_file"])
    cfg["server_log_file"] = os.path.abspath(cfg["server_log_file"])
    os.makedirs(os.path.dirname(cfg["server_log_file"]), exist_ok=True)

    return cfg


CONFIG = load_config()
"""Runtime configuration loaded once when the application package starts."""
