"""Offline unit tests: no database, no network."""
import os, sys, tempfile, types, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import graph_store as gs
import action_agent as aa


class ConfigTests(unittest.TestCase):
    def _env_file(self, text):
        d = tempfile.mkdtemp(); p = Path(d) / ".env"; p.write_text(text, encoding="utf-8"); return p

    def test_env_file_values_loaded(self):
        p = self._env_file("FALKORDB_HOST=h.example\nFALKORDB_PORT=6379\nFALKORDB_SSL=true\n")
        cfg = gs.load_configuration(p, environ={})
        self.assertEqual(cfg["FALKORDB_HOST"], "h.example"); self.assertEqual(cfg["FALKORDB_PORT"], "6379")

    def test_nonempty_process_env_wins_and_empty_does_not_mask(self):
        p = self._env_file("FALKORDB_HOST=file.example\nFALKORDB_PORT=1\n")
        cfg = gs.load_configuration(p, environ={"FALKORDB_HOST": "env.example", "FALKORDB_PORT": "   "})
        self.assertEqual(cfg["FALKORDB_HOST"], "env.example"); self.assertEqual(cfg["FALKORDB_PORT"], "1")

    def test_password_not_stripped(self):
        p = self._env_file('FALKORDB_PASSWORD=" pa ss "\n')
        self.assertEqual(gs.load_configuration(p, environ={})["FALKORDB_PASSWORD"], " pa ss ")

    def test_utf8_bom_and_quotes(self):
        d = tempfile.mkdtemp(); p = Path(d) / ".env"; p.write_bytes(b"\xef\xbb\xbfFALKORDB_HOST='quoted.example'\n")
        self.assertEqual(gs.load_configuration(p, environ={})["FALKORDB_HOST"], "quoted.example")

    def test_fallback_parser_matches_dotenv_on_common_inputs(self):
        p = self._env_file("export FALKORDB_HOST=a.example # c\nFALKORDB_PORT=\"123\"\n# note\nFALKORDB_SSL=false\n")
        self.assertEqual(gs._fallback_dotenv_values(p), {"FALKORDB_HOST": "a.example", "FALKORDB_PORT": "123", "FALKORDB_SSL": "false"})

    def test_missing_config_has_actionable_diagnostic_isolated(self):
        """The isolation fix: point DEFAULT_ENV_FILE at nothing so the real .env cannot leak in."""
        nowhere = Path(tempfile.mkdtemp()) / "missing.env"
        clean = {k: v for k, v in os.environ.items() if not k.startswith("FALKORDB_")}
        with mock.patch.object(gs, "DEFAULT_ENV_FILE", nowhere), mock.patch.dict(os.environ, clean, clear=True):
            with self.assertRaises(gs.GraphConfigurationError) as ctx:
                gs.connect_graph("hashira_demo")
        msg = str(ctx.exception)
        self.assertIn("FALKORDB_HOST", msg); self.assertIn("cp .env.example .env", msg)

    def test_validate_rejects_bad_port_and_bool(self):
        base = {"FALKORDB_HOST": "h", "FALKORDB_PORT": "6379", "FALKORDB_SSL": "true"}
        gs.validate_graph_configuration(base)
        for bad in ({"FALKORDB_PORT": "abc"}, {"FALKORDB_PORT": "0"}, {"FALKORDB_PORT": "70000"}, {"FALKORDB_SSL": "maybe"}, {"FALKORDB_HOST": " "}):
            with self.assertRaises(gs.GraphConfigurationError, msg=str(bad)):
                gs.validate_graph_configuration({**base, **bad})

    def test_configuration_report_never_leaks_values(self):
        p = self._env_file("FALKORDB_HOST=secret-host\nFALKORDB_PASSWORD=hunter2hunter2\n")
        rep = gs.configuration_report(p, environ={})
        self.assertNotIn("hunter2", repr(rep)); self.assertNotIn("secret-host", repr(rep))
        self.assertTrue(rep["FALKORDB_PASSWORD_configured"])

    def test_tenant_graph_name(self):
        self.assertEqual(gs.tenant_graph_name("Hashira-Demo"), "tenant_hashira_demo")
        for bad in ("", "../x", "a b", "x" * 49, "a;b", "-lead"):
            with self.assertRaises(ValueError, msg=bad): gs.tenant_graph_name(bad)

    def test_safe_error_detail_redacts(self):
        out = gs._safe_error_detail("bad password=abc123 and redis://u:p@host:1 token: ZZZ " + "A" * 40)
        for leaked in ("abc123", "u:p@host", "ZZZ", "A" * 40): self.assertNotIn(leaked, out)

    def test_query_rows_error_keeps_query_detail_and_empty_key_is_empty_read(self):
        class G:
            def ro_query(self, *a, **k): raise RuntimeError("Invalid input near 'RETRUN'")
            def query(self, *a, **k): raise RuntimeError("Invalid input near 'RETRUN'")
        with self.assertRaises(RuntimeError) as ctx: gs.query_rows(G(), "x")
        self.assertIn("RETRUN", str(ctx.exception))
        class Empty:
            def ro_query(self, *a, **k): raise RuntimeError("Invalid graph operation on empty key")
        self.assertEqual(gs.query_rows(Empty(), "x", read_only=True), [])


class TenantAuthTests(unittest.TestCase):
    def test_unauthorized_tenant_fails_closed(self):
        with self.assertRaises(PermissionError): aa.ActionAgent(object(), "evil_corp", "s")
        with self.assertRaises(PermissionError): aa.authorize_tenant("hashira_demo", set())
        self.assertEqual(aa.ActionAgent(object(), "demo_acme", "s").tenant_id, "demo_acme")


class IntentTests(unittest.TestCase):
    def test_parse_tool_plan_accepts_fenced_json(self):
        self.assertEqual(aa.parse_tool_plan('```json\n{"tool":"incidents","service":"Payment API"}\n```')["tool"], "incidents")

    def test_parse_tool_plan_rejects_bad_input(self):
        for bad in ('{"tool":"cypher"}', '{"tool":"memories","cypher":"MATCH (n) DETACH DELETE n"}',
                    '[]', '{"tool":"ownership","service":%s}' % ('"' + "x" * 121 + '"'), '{"tool":"ownership","service":5}', 'not json'):
            with self.assertRaises((ValueError, TypeError)): aa.parse_tool_plan(bad)

    def test_parse_answer_response_validation(self):
        ok = '{"answer":" Payments Team owns it. ","evidence_indices":[0]}'
        self.assertEqual(aa.parse_answer_response(ok, 1), "Payments Team owns it.")
        for bad in ('{"answer":"x","evidence_indices":[]}', '{"answer":"x","evidence_indices":[1]}',
                    '{"answer":"x","evidence_indices":[-1]}', '{"answer":"x","evidence_indices":[true]}',
                    '{"answer":"","evidence_indices":[0]}', '{"answer":"x","evidence_indices":[0],"extra":1}', '{"answer":"x"}'):
            with self.assertRaises((ValueError, TypeError), msg=bad): aa.parse_answer_response(bad, 1)

    def test_remember_detection_and_extraction(self):
        cases = {"Remember this project note: HashiraGraph is our hackathon project": "HashiraGraph is our hackathon project",
                 "remember that Payments rotates on-call Fridays": "Payments rotates on-call Fridays",
                 "Please remember: demo is Sunday": "demo is Sunday", "Note: ship it": "ship it"}
        for text, note in cases.items():
            self.assertTrue(aa.is_remember_statement(text), text); self.assertEqual(aa.extract_note(text), note)
        for q in ("Do you remember what I said?", "What is the owner of Payment API?", "note the blast radius?"):
            self.assertFalse(aa.is_remember_statement(q), q)

    def test_heuristic_routing_table(self):
        table = [("If the Payment API fails, who is affected?", "blast_radius", "Payment API"),
                 ("Who owns Ledger Service?", "ownership", "Ledger Service"),
                 ("Which incidents affected Payment API?", "incidents", "Payment API"),
                 ("Which decisions govern Ledger Service?", "decisions", "Ledger Service"),
                 ("What did I tell you about the hackathon?", "memories", ""),
                 ("What do we know about Payment API?", "memories", "Payment API"),
                 ("What did I just ask in this session?", "memories", "")]
        for q, tool, svc in table:
            plan = aa.heuristic_plan(q)
            self.assertEqual((plan["tool"], plan["service"]), (tool, svc), q)
        self.assertEqual(aa.heuristic_plan("what did i just ask")["session_id"], "current")

    def test_gemini_failure_falls_back_and_cannot_change_memory_scope(self):
        pkg = types.ModuleType("google"); genai = types.ModuleType("google.genai")
        class Boom:
            def __init__(self, *a, **k): raise RuntimeError("quota")
        genai.Client = Boom; pkg.genai = genai
        with mock.patch.dict(sys.modules, {"google": pkg, "google.genai": genai}), mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            plan, note = aa.interpret_intent("Who owns Payment API?")
            self.assertEqual(plan["tool"], "ownership"); self.assertIn("RuntimeError", note)
            plan, note = aa.interpret_intent("What did I tell you about the hackathon?")
            self.assertEqual(plan["tool"], "memories"); self.assertIn("deterministic", note)

    def test_compose_answer_empty_evidence_never_invents(self):
        text, _ = aa.compose_answer("anything", [])
        self.assertIn("no matching records", text)


if __name__ == "__main__":
    unittest.main()
