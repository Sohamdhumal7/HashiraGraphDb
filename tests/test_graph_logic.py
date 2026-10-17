"""Offline checks: these tests use a recording fake, never pretend to be Cloud tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from action_agent import ActionAgent, authorize_tenant, compose_answer, parse_answer_response, parse_tool_plan, persist_episode, run_tool
from curator_agent import run_curator
from graph_store import GraphConfigurationError, connect_graph
from seed_graph import BLAST_RADIUS_QUERY, SERVICES, seed_graph


class FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class FakeGraph:
    name = "test_graph"
    def __init__(self):
        self.queries = []
        self.read_rows = []
    def query(self, query, params=None):
        self.queries.append((query, params or {}))
        if "RETURN m.id" in query:
            return FakeResult([[params["id"]]])
        return FakeResult([])
    def ro_query(self, query, params=None):
        self.queries.append((query, params or {}))
        if "DEPENDS_ON*1..4" in query or "MemoryEpisode" in query:
            return FakeResult(self.read_rows)
        return FakeResult([])


class GraphLogicTests(unittest.TestCase):
    def test_blast_radius_validates_live_rows_and_paths(self):
        graph = FakeGraph()
        graph.read_rows = [
            ["Checkout API", "Checkout Team", ["Checkout API", "Payment API"], ["DEPENDS_ON"]],
            ["Web Checkout", "Web Team", ["Web Checkout", "Checkout API", "Payment API"], ["DEPENDS_ON", "DEPENDS_ON"]],
        ]
        evidence, query, _ = run_tool(graph, {"tool":"blast_radius", "service":"Payment API", "tenant_id":"hashira_demo"})
        self.assertEqual({row["service"]:row["owner"] for row in evidence}, {"Checkout API":"Checkout Team", "Web Checkout":"Web Team"})
        self.assertEqual(evidence[1]["nodes"], ["Web Checkout", "Checkout API", "Payment API"])
        self.assertEqual(evidence[1]["edges"], ["DEPENDS_ON", "DEPENDS_ON"])
        self.assertIn("DEPENDS_ON*1..4", query)
        self.assertEqual(graph.queries[0][1], {"failed_service":"Payment API"})

    def test_query_parameterized_and_seed_uses_idempotent_merge(self):
        graph = FakeGraph()
        seed_graph(graph)
        self.assertEqual(len(SERVICES), 7)
        self.assertTrue(graph.queries)
        self.assertTrue(all("MERGE" in query for query, _ in graph.queries))
        self.assertTrue(any("AFFECTED_BY" in query for query, _ in graph.queries))
        self.assertIn("Notification Service", {row[0] for row in SERVICES})

    def test_no_results_are_reported_as_no_evidence(self):
        answer, note = compose_answer("what happened?", [])
        self.assertIn("no matching records", answer)
        self.assertIsNone(note)

    def test_connection_missing_config_has_actionable_diagnostic(self):
        # Root cause: load_configuration() re-reads the real .env file from disk
        # every time it is called, so removing keys from os.environ is not enough
        # to simulate a missing configuration — the values come back from the file.
        # Fix: patch load_configuration inside graph_store so connect_graph sees a
        # genuinely empty config, isolating this test from any real credentials.
        empty_config = {key: "" for key in (
            "FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME",
            "FALKORDB_PASSWORD", "FALKORDB_SSL", "FALKORDB_GRAPH",
            "GEMINI_API_KEY", "GEMINI_MODEL",
        )}
        import graph_store as gs
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp_dir, \
             patch.object(gs, "DEFAULT_ENV_FILE", Path(temp_dir) / "does-not-exist.env"), \
             patch.dict(os.environ, {key: "" for key in gs.CONFIG_KEYS}, clear=False):
            with self.assertRaisesRegex(GraphConfigurationError, "FALKORDB_HOST"):
                connect_graph()

    def test_unconfigured_gemini_does_not_block_deterministic_graph_operations(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY":""}):
            rows, _, _ = run_tool(FakeGraph(), {"tool":"ownership", "service":"Payment API", "tenant_id":"hashira_demo"})
            self.assertEqual(rows, [])

    def test_memory_retrieval_is_tenant_scoped_in_query_and_parameters(self):
        graph = FakeGraph()
        run_tool(graph, {"tool":"memories", "tenant_id":"hashira_demo", "session_id":"session-1"})
        query, params = graph.queries[-1]
        self.assertIn("m.tenant_id=$tenant", query)
        self.assertEqual(params["tenant"], "hashira_demo")
        self.assertEqual(params["session"], "session-1")

    def test_generated_text_cannot_execute_arbitrary_query(self):
        with self.assertRaises(ValueError):
            parse_tool_plan('{"tool":"MATCH (n) DETACH DELETE n"}')
        with self.assertRaises(ValueError):
            parse_tool_plan('{"tool":"ownership","service":"x","cypher":"MATCH (n) RETURN n"}')

    def test_malformed_or_uncited_model_answers_are_rejected(self):
        self.assertEqual(parse_answer_response('{"answer":"Payment API is owned by Payments Team.","evidence_indices":[0]}', 1), "Payment API is owned by Payments Team.")
        with self.assertRaises(ValueError):
            parse_answer_response('{"answer":"Unsupported","evidence_indices":[3]}', 1)
        with self.assertRaises(ValueError):
            parse_answer_response('{"answer":"Unsupported","evidence_indices":[]}', 1)

    def test_tenant_authorization_fails_closed(self):
        self.assertEqual(authorize_tenant("hashira_demo", {"hashira_demo"}), "hashira_demo")
        with self.assertRaises(PermissionError):
            authorize_tenant("other", {"hashira_demo"})

    def test_curator_does_not_delete_historical_facts(self):
        graph = FakeGraph()
        # Distinguish curator count reads and metrics. Empty graph means no writes expected.
        report = run_curator(graph, "hashira_demo")
        self.assertEqual(report["ownership_facts_closed_with_explicit_ended_at"], 0)
        self.assertTrue(all("DELETE" not in query.upper() for query, _ in graph.queries))
        self.assertTrue(any("ended_at IS NOT NULL" in query for query, _ in graph.queries))

    def test_curator_promotion_count_requires_created_marker(self):
        class CandidateGraph(FakeGraph):
            def __init__(self, did_create):
                super().__init__()
                self.did_create = did_create
            def ro_query(self, query, params=None):
                self.queries.append((query, params or {}))
                if "RELATED_TO" in query:
                    return FakeResult([[1, ["Service"], 2, ["Service"], "DEPENDS_ON", "audit-source", 1]])
                return FakeResult([])
            def query(self, query, params=None):
                self.queries.append((query, params or {}))
                if "_curator_run_id" in query:
                    return FakeResult([[self.did_create]])
                return FakeResult([])
        self.assertEqual(run_curator(CandidateGraph(False), "hashira_demo")["generic_relationships_promoted"], 0)
        created_graph = CandidateGraph(True)
        report = run_curator(created_graph, "hashira_demo")
        self.assertEqual(report["generic_relationships_promoted"], 1)
        self.assertTrue(any("MERGE (a)-[c:DEPENDS_ON" in q for q, _ in created_graph.queries))

    def test_blast_query_returns_typed_provenance(self):
        self.assertIn("type(r)", BLAST_RADIUS_QUERY)
        self.assertIn("nodes(path)", BLAST_RADIUS_QUERY)
        self.assertIn("relationships(path)", BLAST_RADIUS_QUERY)

    def test_persist_episode_creates_expected_cypher_properties(self):
        graph = FakeGraph()
        persist_episode(graph, "hashira_demo", "sess-1", "My prompt", "My answer", [{"k": "v"}], source="Test Source")
        self.assertTrue(graph.queries)
        query, params = graph.queries[-1]
        self.assertIn("MERGE (m:MemoryEpisode {id:$id})", query)
        self.assertIn("ON CREATE SET", query)
        self.assertEqual(params["tenant"], "hashira_demo")
        self.assertEqual(params["session"], "sess-1")
        self.assertEqual(params["message"], "My prompt")
        self.assertEqual(params["summary"], "My answer")
        self.assertEqual(params["source"], "Test Source")
        self.assertEqual(params["evidence_count"], 1)
        self.assertEqual(params["kind"], "exchange")
        first_id = params["id"]
        persist_episode(graph, "hashira_demo", "sess-1", "My prompt", "My answer", [{"k": "v"}], source="Other Source")
        self.assertNotEqual(first_id, graph.queries[-1][1]["id"])
        persist_episode(graph, "hashira_demo", "sess-1", "My prompt", "My answer", [], source="retry source", episode_id="retry-event")
        persist_episode(graph, "hashira_demo", "sess-1", "Changed prompt", "Changed answer", [], source="changed source", episode_id="retry-event")
        retry_query, retry_params = graph.queries[-1]
        self.assertEqual(retry_params["id"], "retry-event")
        self.assertIn("ON CREATE SET", retry_query)

    def test_remember_statement_handling(self):
        graph = FakeGraph()
        agent = ActionAgent(graph, "hashira_demo", "sess-100")
        prompt = "Remember this project note: HashiraGraph is our hackathon project, and Payment API is the primary service in our demo."
        res = agent.ask(prompt)
        self.assertEqual(res["answer"], "Saved your note to persistent memory.")
        self.assertEqual(res["trace"]["tool"], "memories")
        self.assertEqual(res["trace"]["rows"], 1)
        self.assertEqual(len(res["evidence"]), 0)
        # Verify it was persisted to graph
        self.assertTrue(graph.queries)
        _, params = graph.queries[-1]
        self.assertEqual(params["tenant"], "hashira_demo")
        self.assertEqual(params["session"], "sess-100")
        self.assertIn("HashiraGraph is our hackathon project", params["message"])
        self.assertEqual(params["kind"], "user_note")

    def test_remember_write_failure_is_never_reported_as_saved(self):
        class FailedWriteGraph(FakeGraph):
            def query(self, query, params=None):
                self.queries.append((query, params or {}))
                return FakeResult([])
        result = ActionAgent(FailedWriteGraph(), "hashira_demo", "s1").ask("Remember this note: important fact")
        self.assertIn("couldn't save", result["answer"])
        self.assertIn("error", result["trace"])

    def test_memory_recall_cross_session_default(self):
        graph = FakeGraph()
        graph.read_rows = [
            ["episode-1", "sess-old", "2026-10-17T10:00:00Z", "user_note", "Project note", "Remember this project note: ...", "User note", []]
        ]
        agent = ActionAgent(graph, "hashira_demo", "sess-new")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            res = agent.ask("What do you remember about our project?")
        query, params = next((q, p) for q, p in graph.queries if "WHERE m.tenant_id=$tenant" in q)
        self.assertIn("m.tenant_id=$tenant", query)
        self.assertEqual(params["tenant"], "hashira_demo")
        self.assertEqual(params["session"], "")
        self.assertEqual(res["trace"]["rows"], 1)
        self.assertIn("Project note", res["answer"])

    def test_memory_routing_overrides_untrusted_model_scope(self):
        graph = FakeGraph()
        agent = ActionAgent(graph, "hashira_demo", "current-session")
        with patch("action_agent.interpret_intent", return_value=({"tool":"ownership", "service":"Payment API", "session_id":"untrusted"}, None)), \
             patch.dict(os.environ, {"GEMINI_API_KEY":""}):
            agent.ask("What did I tell you about the hackathon?")
        query, params = next(pair for pair in graph.queries if "WHERE m.tenant_id=$tenant" in pair[0])
        self.assertIn("m.tenant_id=$tenant", query)
        self.assertEqual(params["tenant"], "hashira_demo")
        self.assertEqual(params["session"], "")

    def test_memory_recall_current_session_filter(self):
        graph = FakeGraph()
        agent = ActionAgent(graph, "hashira_demo", "sess-active")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            agent.ask("What did I ask in this session?")
        query, params = next((q, p) for q, p in graph.queries if "WHERE m.tenant_id=$tenant" in q)
        self.assertEqual(params["session"], "sess-active")

    def test_memory_retrieval_distinguishes_empty_and_error(self):
        # 1. Empty results: legitimate empty state, no error
        graph_empty = FakeGraph()
        agent_empty = ActionAgent(graph_empty, "hashira_demo", "sess-1")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            res_empty = agent_empty.ask("What do you remember?")
        self.assertIn("no matching records", res_empty["answer"])
        self.assertNotIn("error", res_empty["trace"])

        # 2. Query error: graph raises exception
        class ErrorGraph:
            name = "err_graph"
            def ro_query(self, query, params=None):
                raise RuntimeError("FalkorDB timeout failure")
        agent_err = ActionAgent(ErrorGraph(), "hashira_demo", "sess-1")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            res_err = agent_err.ask("What do you remember?")
        self.assertIn("The graph query did not complete: RuntimeError: FalkorDB timeout failure", res_err["answer"])
        self.assertIn("FalkorDB timeout failure", res_err["trace"]["error"])

    def test_memory_retrieval_tenant_isolation(self):
        graph = FakeGraph()
        # Unauthorized tenant fails closed
        with self.assertRaises(PermissionError):
            ActionAgent(graph, "unauthorized_tenant", "s1")

        # Different authorized tenant uses separate tenant parameter
        agent_acme = ActionAgent(graph, "demo_acme", "s1")
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            agent_acme.ask("What do you remember?")
        query, params = next((q, p) for q, p in graph.queries if "WHERE m.tenant_id=$tenant" in q)
        self.assertEqual(params["tenant"], "demo_acme")


if __name__ == "__main__":
    unittest.main()

