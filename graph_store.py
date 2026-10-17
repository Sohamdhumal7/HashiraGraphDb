"""FalkorDB connection, query, tenant and diagnostics helpers."""
from __future__ import annotations

import os
import json
import re
from pathlib import Path
from typing import Mapping, MutableMapping
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = PROJECT_DIR / ".env"
CONFIG_KEYS = (
    "FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME", "FALKORDB_PASSWORD",
    "FALKORDB_SSL", "FALKORDB_GRAPH", "GEMINI_API_KEY", "GEMINI_MODEL",
)
DEMO_TENANTS = {"hashira_demo", "demo_acme"}


class GraphConfigurationError(RuntimeError):
    """A required connection setting is missing or invalid."""


class GraphConnectionError(RuntimeError):
    """The FalkorDB client could not select/connect to a graph."""


def _fallback_dotenv_values(path: Path) -> dict[str, str]:
    """Parse common dotenv assignments if python-dotenv is not installed yet."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise GraphConfigurationError(f"Invalid .env key on line {line_number} of {path.name}.")
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = -1
            escaped = False
            for index in range(1, len(value)):
                char = value[index]
                if char == quote and not escaped:
                    end = index
                    break
                if char == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
            if end < 0 or value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#"):
                raise GraphConfigurationError(f"Malformed quoted value for {key} in {path.name} on line {line_number}.")
            if quote == '"':
                try:
                    content = json.loads(value[:end + 1])
                except json.JSONDecodeError as exc:
                    raise GraphConfigurationError(f"Malformed quoted value for {key} in {path.name} on line {line_number}.") from exc
            else:
                content = value[1:end].replace("\\'", "'").replace("\\\\", "\\")
            values[key] = content
        else:
            # dotenv comments start at an unquoted # preceded by whitespace.
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            values[key] = value
    return values


def load_configuration(env_file: str | Path | None = None, *, environ: MutableMapping[str, str] | Mapping[str, str] | None = None,
                       apply_to_environment: bool = False, require_file: bool = False) -> dict[str, str]:
    """Load project configuration independently of the process working directory.

    Nonempty process environment variables win. Empty/whitespace-only process
    values are treated as unset, so they cannot mask a valid .env assignment.
    Password values are returned exactly as parsed; callers must not strip them.
    """
    path = Path(env_file).expanduser().resolve() if env_file is not None else DEFAULT_ENV_FILE
    environment = os.environ if environ is None else environ
    if not path.is_file():
        if require_file:
            raise GraphConfigurationError(
                f"Configuration file not found at expected path: {path}. "
                "Create it from the template with `cp .env.example .env` (Git Bash) "
                "or `Copy-Item .env.example .env` (PowerShell)."
            )
        file_values: dict[str, str] = {}
    else:
        try:
            try:
                from dotenv import dotenv_values
                parsed = dotenv_values(dotenv_path=path, encoding="utf-8-sig")
                file_values = {str(key): value for key, value in parsed.items() if value is not None}
            except ImportError:
                file_values = _fallback_dotenv_values(path)
        except GraphConfigurationError:
            raise
        except Exception as exc:
            raise GraphConfigurationError(f"Could not read configuration file {path}: {type(exc).__name__}.") from exc

    merged: dict[str, str] = {}
    for key in CONFIG_KEYS:
        external = environment.get(key)
        if external is not None and external.strip():
            merged[key] = external
        else:
            merged[key] = file_values.get(key, external or "")
    if apply_to_environment and isinstance(environment, MutableMapping):
        for key, value in merged.items():
            if not environment.get(key, "").strip() and value:
                environment[key] = value
    return merged


# Keep existing application modules that read optional Gemini settings through
# os.getenv compatible, while centralizing discovery and precedence here.
load_configuration(apply_to_environment=True)


def configuration_report(env_file: str | Path | None = None, *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return only safe presence diagnostics; never return any configured value."""
    path = Path(env_file).expanduser().resolve() if env_file is not None else DEFAULT_ENV_FILE
    config = load_configuration(path, environ=environ)
    return {
        "configuration_file_found": path.is_file(),
        "configuration_file_path": str(path),
        "FALKORDB_HOST_configured": bool(config["FALKORDB_HOST"].strip()),
        "FALKORDB_PORT_configured": bool(config["FALKORDB_PORT"].strip()),
        "FALKORDB_USERNAME_configured": bool(config["FALKORDB_USERNAME"].strip()),
        "FALKORDB_PASSWORD_configured": bool(config["FALKORDB_PASSWORD"].strip()),
        "FALKORDB_SSL_configured": bool(config["FALKORDB_SSL"].strip()),
    }


def validate_graph_configuration(config: Mapping[str, str] | None = None) -> None:
    """Validate required connection settings without making a network call."""
    settings = config if config is not None else load_configuration()
    _required_env("FALKORDB_HOST", settings)
    try:
        port = int(_required_env("FALKORDB_PORT", settings))
    except ValueError as exc:
        raise GraphConfigurationError("FALKORDB_PORT must be a numeric TCP port.") from exc
    if not 1 <= port <= 65535:
        raise GraphConfigurationError("FALKORDB_PORT must be between 1 and 65535.")
    _env_bool("FALKORDB_SSL", config=settings)


def _required_env(name: str, config: Mapping[str, str] | None = None, *, strip: bool = True) -> str:
    settings = config if config is not None else load_configuration()
    value = settings.get(name, "")
    if not value.strip():
        path = DEFAULT_ENV_FILE
        if not path.is_file():
            detail = (f"No .env file found at expected path {path}. "
                      "Create it with `cp .env.example .env` in Git Bash or `Copy-Item .env.example .env` in PowerShell.")
        else:
            detail = f"{name} is missing or empty in {path.name} and the process environment."
        raise GraphConfigurationError(f"Missing {name}. {detail}")
    return value.strip() if strip else value


def _env_bool(name: str, default: bool = False, config: Mapping[str, str] | None = None) -> bool:
    settings = config if config is not None else load_configuration()
    raw = settings.get(name, "").strip().lower()
    if not raw:
        return default
    if raw not in {"1", "true", "yes", "y", "on", "0", "false", "no", "n", "off"}:
        raise GraphConfigurationError(f"{name} must be a boolean: true/false, 1/0, yes/no, or on/off.")
    return raw in {"1", "true", "yes", "y", "on"}


def tenant_graph_name(tenant_id: str) -> str:
    """Create a strict graph name from an application-authorized tenant ID."""
    tenant_id = tenant_id.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", tenant_id):
        raise ValueError("Tenant ID must be 1–48 lowercase letters, digits, underscores, or hyphens.")
    return "tenant_" + tenant_id.replace("-", "_")


def connect_graph(tenant_id: str | None = None) -> tuple[Any, Any]:
    """Connect using the official synchronous FalkorDB client.

    Tenant selection is deliberately opt-in: callers must authorize the ID before
    calling this function. Different graph names are logical separation, not an
    authentication boundary.
    """
    config = load_configuration()
    validate_graph_configuration(config)
    host = _required_env("FALKORDB_HOST", config)
    port = int(_required_env("FALKORDB_PORT", config))
    graph_name = tenant_graph_name(tenant_id) if tenant_id is not None else (config.get("FALKORDB_GRAPH", "").strip() or "tenant_hashira_demo")
    if not graph_name or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", graph_name):
        raise GraphConfigurationError("FALKORDB_GRAPH must contain 1–128 letters, digits, or underscores.")
    try:
        from falkordb import FalkorDB
    except ImportError as exc:
        raise GraphConnectionError("FalkorDB package is unavailable. Install dependencies with: python -m pip install -r requirements.txt") from exc
    try:
        password = config.get("FALKORDB_PASSWORD", "")
        client = FalkorDB(host=host, port=port, username=config.get("FALKORDB_USERNAME", "").strip() or None,
                          password=password if password.strip() else None, ssl=_env_bool("FALKORDB_SSL", config=config),
                          socket_connect_timeout=10, socket_timeout=20)
        graph = client.select_graph(graph_name)
        return client, graph
    except Exception as exc:
        message = str(exc).lower()
        if any(token in message for token in ("ssl", "tls", "certificate")):
            category, next_step = "TLS", "check FALKORDB_SSL and the Cloud certificate/connection instructions"
        elif any(token in message for token in ("auth", "password", "credential", "permission")):
            category, next_step = "authentication", "check the Cloud username/password without sharing them"
        elif any(token in message for token in ("resolve", "dns", "name or service", "getaddrinfo")):
            category, next_step = "DNS", "check the hostname in your Cloud dashboard and network access"
        elif any(token in message for token in ("timeout", "timed out")):
            category, next_step = "timeout", "check network access and whether the Cloud instance is ready"
        elif any(token in message for token in ("refused", "unreachable", "connection")):
            category, next_step = "network", "check the Cloud host, port, instance status, and outbound network access"
        else:
            category, next_step = "client setup", "check the FalkorDB SDK version and Cloud connection settings"
        raise GraphConnectionError(f"FalkorDB {category} failure ({type(exc).__name__}); {next_step}.") from exc


def query_rows(graph: Any, cypher: str, params: dict[str, Any] | None = None, *, read_only: bool = False) -> list:
    """Execute Cypher with value parameters and return rows with useful errors."""
    try:
        result = graph.ro_query(cypher, params or {}) if read_only else graph.query(cypher, params or {})
        return result.result_set
    except Exception as exc:
        message = str(exc).lower()
        if read_only and "empty key" in message:
            return []
        if any(token in message for token in ("ssl", "tls", "certificate")):
            category, next_step = "TLS", "check FALKORDB_SSL and Cloud TLS requirements"
        elif any(token in message for token in ("auth", "password", "credential", "permission")):
            category, next_step = "authentication", "check the Cloud credentials and account permissions"
        elif any(token in message for token in ("resolve", "dns", "name or service", "getaddrinfo")):
            category, next_step = "DNS", "check the Cloud hostname and network access"
        elif any(token in message for token in ("timeout", "timed out")):
            category, next_step = "timeout", "check network access and instance readiness"
        elif any(token in message for token in ("refused", "unreachable", "connection")):
            category, next_step = "network", "check the Cloud endpoint and outbound network access"
        else:
            category, next_step = "query", "check the graph query and the selected graph schema"
        detail = _safe_error_detail(str(exc))
        suffix = f" Detail: {detail}" if category == "query" and detail else ""
        raise RuntimeError(f"FalkorDB {category} failure ({type(exc).__name__}); {next_step}.{suffix}") from exc


def _safe_error_detail(message: str) -> str:
    """Keep useful query diagnostics while redacting credential-shaped data."""
    message = re.sub(r"(?i)(password|api[_ -]?key|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", message)
    message = re.sub(r"(?i)(redis|rediss|https?)://[^\s]+", "[REDACTED_URL]", message)
    message = re.sub(r"(?i)\b[A-Za-z0-9_-]{32,}\b", "[REDACTED_VALUE]", message)
    return " ".join(message.split())[:300]


def graph_health(graph: Any) -> dict[str, int]:
    """Return actual total node and relationship counts from the selected graph."""
    rows = query_rows(graph, "MATCH (n) OPTIONAL MATCH (n)-[r]-() RETURN count(DISTINCT n) AS nodes, count(DISTINCT r) AS relationships", read_only=True)
    if not rows:
        return {"nodes": 0, "relationships": 0}
    return {"nodes": int(rows[0][0] or 0), "relationships": int(rows[0][1] or 0)}


def close_client(client: Any | None) -> None:
    """Close the synchronous client's underlying connection when available."""
    if client is not None and callable(getattr(client, "close", None)):
        client.close()
