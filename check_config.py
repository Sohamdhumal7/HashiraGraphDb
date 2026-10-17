"""Safely report whether FalkorDB configuration is present and valid."""
from __future__ import annotations

import sys

from graph_store import GraphConfigurationError, configuration_report, validate_graph_configuration


def main() -> int:
    try:
        report = configuration_report()
        print(f"Configuration file found: {report['configuration_file_found']}")
        print(f"Configuration file path: {report['configuration_file_path']}")
        for key in (
            "FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME",
            "FALKORDB_PASSWORD", "FALKORDB_SSL",
        ):
            print(f"{key} configured: {report[f'{key}_configured']}")
        validate_graph_configuration()
        print("Required connection settings are valid. No network connection was attempted.")
        return 0
    except GraphConfigurationError as exc:
        print(f"Configuration check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
