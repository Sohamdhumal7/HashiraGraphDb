"""Configuration regression tests use temporary files and never touch project secrets."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import ModuleType
from pathlib import Path
from unittest.mock import patch

import graph_store
from graph_store import (
    GraphConfigurationError,
    configuration_report,
    load_configuration,
    validate_graph_configuration,
)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.env_file = self.root / ".env"

    def write_env(self, text: str, *, bom: bool = False) -> None:
        encoding = "utf-8-sig" if bom else "utf-8"
        self.env_file.write_text(text, encoding=encoding)

    def test_valid_env_loads_host_and_quoted_values(self):
        self.write_env('FALKORDB_HOST="hashira-graph-9281.cloud.example.net"\nFALKORDB_PORT=6380\nFALKORDB_PASSWORD="p#@!$%= :;[]"\n')
        config = load_configuration(self.env_file, environ={})
        self.assertEqual(config["FALKORDB_HOST"], "hashira-graph-9281.cloud.example.net")
        self.assertEqual(config["FALKORDB_PASSWORD"], "p#@!$%= :;[]")

    def test_loading_is_independent_of_process_working_directory(self):
        self.write_env("FALKORDB_HOST=long.host-91.example.net\nFALKORDB_PORT=6380\n")
        other_dir = self.root / "different-cwd"
        other_dir.mkdir()
        with patch.object(graph_store, "DEFAULT_ENV_FILE", self.env_file), patch.dict(os.environ, {}, clear=True):
            old_cwd = Path.cwd()
            try:
                os.chdir(other_dir)
                config = load_configuration(environ=os.environ)
            finally:
                os.chdir(old_cwd)
        self.assertEqual(config["FALKORDB_HOST"], "long.host-91.example.net")

    def test_missing_env_file_is_explicit(self):
        with self.assertRaisesRegex(GraphConfigurationError, "Configuration file not found") as ctx:
            load_configuration(self.env_file, environ={}, require_file=True)
        self.assertIn("cp .env.example .env", str(ctx.exception))

    def test_missing_host_and_whitespace_host_are_rejected(self):
        self.write_env("FALKORDB_PORT=6380\n")
        with self.assertRaisesRegex(GraphConfigurationError, "FALKORDB_HOST"):
            validate_graph_configuration(load_configuration(self.env_file, environ={}))
        self.write_env("FALKORDB_HOST=   \nFALKORDB_PORT=6380\n")
        with self.assertRaisesRegex(GraphConfigurationError, "FALKORDB_HOST"):
            validate_graph_configuration(load_configuration(self.env_file, environ={}))

    def test_long_opaque_hostname_is_not_truncated_or_rewritten(self):
        host = ("graph1234567890-cloud987654321." * 5) + "region-1.example.net"
        self.write_env(f"FALKORDB_HOST={host}\nFALKORDB_PORT=6380\n")
        config = load_configuration(self.env_file, environ={})
        validate_graph_configuration(config)
        self.assertEqual(config["FALKORDB_HOST"], host)

    def test_empty_external_value_does_not_mask_file_but_nonempty_wins(self):
        self.write_env("FALKORDB_HOST=from-file.example.net\nFALKORDB_PORT=6380\n")
        empty = load_configuration(self.env_file, environ={"FALKORDB_HOST":"   "})
        nonempty = load_configuration(self.env_file, environ={"FALKORDB_HOST":"from-shell.example.net"})
        self.assertEqual(empty["FALKORDB_HOST"], "from-file.example.net")
        self.assertEqual(nonempty["FALKORDB_HOST"], "from-shell.example.net")

    def test_ssl_true_false_and_invalid_values(self):
        for raw, expected in (("true", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("no", False)):
            self.write_env(f"FALKORDB_HOST=db.example.net\nFALKORDB_PORT=6380\nFALKORDB_SSL={raw}\n")
            config = load_configuration(self.env_file, environ={})
            self.assertEqual(graph_store._env_bool("FALKORDB_SSL", config=config), expected)
        self.write_env("FALKORDB_HOST=db.example.net\nFALKORDB_PORT=6380\nFALKORDB_SSL=sometimes\n")
        with self.assertRaisesRegex(GraphConfigurationError, "FALKORDB_SSL"):
            validate_graph_configuration(load_configuration(self.env_file, environ={}))

    def test_invalid_ports_are_rejected(self):
        for port in ("abc", "0", "65536", "-1"):
            self.write_env(f"FALKORDB_HOST=db.example.net\nFALKORDB_PORT={port}\n")
            with self.assertRaises(GraphConfigurationError):
                validate_graph_configuration(load_configuration(self.env_file, environ={}))

    def test_utf8_bom_is_supported(self):
        self.write_env("FALKORDB_HOST=bom.example.net\nFALKORDB_PORT=6380\n", bom=True)
        config = load_configuration(self.env_file, environ={})
        self.assertEqual(config["FALKORDB_HOST"], "bom.example.net")

    def test_safe_report_never_contains_configuration_values(self):
        host = "secret-host-937.example.net"
        password = "secret-password-punctuation!#%"
        self.write_env(f"FALKORDB_HOST={host}\nFALKORDB_PORT=6380\nFALKORDB_PASSWORD='{password}'\n")
        report = configuration_report(self.env_file, environ={})
        self.assertTrue(report["configuration_file_found"])
        self.assertTrue(report["FALKORDB_HOST_configured"])
        self.assertTrue(report["FALKORDB_PASSWORD_configured"])
        self.assertNotIn(host, repr(report))
        self.assertNotIn(password, repr(report))

    def test_valid_config_passes_validation_and_constructs_sdk_without_network(self):
        host = "long-cloud-graph-948271.eu-west.example.net"
        password = "punctuation!#%=:;[]"
        self.write_env(
            f'FALKORDB_HOST="{host}"\nFALKORDB_PORT=6380\n'
            f'FALKORDB_PASSWORD="{password}"\nFALKORDB_SSL=true\n'
            "FALKORDB_GRAPH=tenant_hashira_demo\n"
        )
        constructed = {}
        class FakeClient:
            def __init__(self, **kwargs):
                constructed.update(kwargs)
            def select_graph(self, name):
                constructed["graph_name"] = name
                return object()
        sdk = ModuleType("falkordb")
        sdk.FalkorDB = FakeClient
        with patch.object(graph_store, "DEFAULT_ENV_FILE", self.env_file), \
             patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"falkordb": sdk}):
            client, graph = graph_store.connect_graph()
        self.assertIsInstance(client, FakeClient)
        self.assertIsNotNone(graph)
        self.assertEqual(constructed["host"], host)
        self.assertEqual(constructed["password"], password)
        self.assertIs(constructed["ssl"], True)
        self.assertEqual(constructed["graph_name"], "tenant_hashira_demo")


if __name__ == "__main__":
    unittest.main()
