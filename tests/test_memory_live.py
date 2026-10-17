"""Live integration test for persistent memory writes and recall against FalkorDB Cloud.

Run with:
    $env:HASHIRAGRAPH_LIVE_TEST = "1"
    python -m unittest tests.test_memory_live -v
"""
from __future__ import annotations

import os
import unittest
import uuid

from action_agent import ActionAgent, persist_episode, run_tool
from graph_store import close_client, connect_graph, query_rows


@unittest.skipUnless(
    os.getenv("HASHIRAGRAPH_LIVE_TEST") == "1",
    "set HASHIRAGRAPH_LIVE_TEST=1 to test persistent memory against the configured Cloud graph",
)
class LivePersistentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.client, self.graph = connect_graph("hashira_demo")
        self.test_ids: list[str] = []
        self.session_id = f"test-sess-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        try:
            if self.test_ids:
                query_rows(
                    self.graph,
                    "MATCH (m:MemoryEpisode) WHERE m.id IN $ids DETACH DELETE m",
                    {"ids": self.test_ids},
                )
        finally:
            close_client(self.client)

    def test_live_memory_write_and_recall_with_service_link(self):
        ep_id = f"test-ep-{uuid.uuid4().hex[:12]}"
        self.test_ids.append(ep_id)

        # 1. Write a user note mentioning a canonical service ("Payment API")
        user_msg = "Remember this note: Payment API is verified for cloud persistence."
        saved_id = persist_episode(
            self.graph,
            tenant_id="hashira_demo",
            session_id=self.session_id,
            user_message=user_msg,
            answer="Saved live test note to persistent memory.",
            evidence=[],
            source="Live memory test suite",
            kind="user_note",
            episode_id=ep_id,
        )
        self.assertEqual(saved_id, ep_id)

        # 2. Verify directly from Cloud graph storage
        direct_rows = query_rows(
            self.graph,
            "MATCH (m:MemoryEpisode {id:$id}) "
            "OPTIONAL MATCH (m)-[:MENTIONS]->(s:Service) "
            "RETURN m.id, m.tenant_id, m.session_id, m.kind, m.user_message, s.name",
            {"id": ep_id},
            read_only=True,
        )
        self.assertEqual(len(direct_rows), 1)
        row = direct_rows[0]
        self.assertEqual(row[0], ep_id)
        self.assertEqual(row[1], "hashira_demo")
        self.assertEqual(row[2], self.session_id)
        self.assertEqual(row[3], "user_note")
        self.assertEqual(row[4], user_msg)
        self.assertEqual(row[5], "Payment API", "Should link unambiguous canonical Payment API service")

        # 3. Verify parameterized recall query via run_tool
        plan = {
            "tool": "memories",
            "tenant_id": "hashira_demo",
            "session_id": self.session_id,
            "service": "Payment API",
        }
        evidence, cypher, tool_name = run_tool(self.graph, plan)
        self.assertEqual(tool_name, "memories")
        self.assertTrue(any(rec.get("episode_id") == ep_id for rec in evidence))

        target = next(rec for rec in evidence if rec.get("episode_id") == ep_id)
        self.assertEqual(target["kind"], "user_note")
        self.assertIn("Payment API", target.get("services", []))

    def test_live_memory_idempotency_preserves_original(self):
        ep_id = f"test-ep-{uuid.uuid4().hex[:12]}"
        self.test_ids.append(ep_id)

        # Initial write
        persist_episode(
            self.graph,
            tenant_id="hashira_demo",
            session_id=self.session_id,
            user_message="Initial prompt text",
            answer="Initial summary text",
            evidence=[],
            source="Test Source A",
            kind="user_note",
            episode_id=ep_id,
        )

        # Read back original creation time and message
        first_read = query_rows(
            self.graph,
            "MATCH (m:MemoryEpisode {id:$id}) RETURN m.created_at, m.user_message, m.source",
            {"id": ep_id},
            read_only=True,
        )
        self.assertEqual(len(first_read), 1)
        created_at, msg, src = first_read[0]
        self.assertEqual(msg, "Initial prompt text")
        self.assertEqual(src, "Test Source A")

        # Retry with the same episode_id but different parameters (should not overwrite ON CREATE SET)
        persist_episode(
            self.graph,
            tenant_id="hashira_demo",
            session_id=self.session_id,
            user_message="Overwritten prompt text",
            answer="Overwritten summary text",
            evidence=[],
            source="Test Source B",
            kind="user_note",
            episode_id=ep_id,
        )

        second_read = query_rows(
            self.graph,
            "MATCH (m:MemoryEpisode {id:$id}) RETURN count(m), m.created_at, m.user_message, m.source",
            {"id": ep_id},
            read_only=True,
        )
        self.assertEqual(second_read[0][0], 1, "Should maintain exactly one node (no duplicate)")
        self.assertEqual(second_read[0][1], created_at, "created_at must be preserved")
        self.assertEqual(second_read[0][2], "Initial prompt text", "Original user_message must be preserved")
        self.assertEqual(second_read[0][3], "Test Source A", "Original source must be preserved")

    def test_live_memory_tenant_isolation(self):
        ep_id = f"test-ep-{uuid.uuid4().hex[:12]}"
        self.test_ids.append(ep_id)

        persist_episode(
            self.graph,
            tenant_id="hashira_demo",
            session_id=self.session_id,
            user_message="Strictly confidential note for hashira_demo",
            answer="Secret note saved.",
            evidence=[],
            kind="user_note",
            episode_id=ep_id,
        )

        # Query under other tenant ("demo_acme")
        acme_plan = {
            "tool": "memories",
            "tenant_id": "demo_acme",
            "session_id": "",
            "service": "",
        }
        evidence, _, _ = run_tool(self.graph, acme_plan)
        matching = [rec for rec in evidence if rec.get("episode_id") == ep_id]
        self.assertEqual(matching, [], "Tenant demo_acme must never see hashira_demo episodes")

    def test_live_action_agent_end_to_end_flow(self):
        agent = ActionAgent(self.graph, tenant_id="hashira_demo", session_id=self.session_id)

        # 1. Ask agent to remember a note
        note_prompt = "Remember this note: Verification on FalkorDB Cloud passed all memory checks."
        remember_resp = agent.ask(note_prompt)
        self.assertIn("Saved your note to persistent memory", remember_resp["answer"])
        written_ids = remember_resp["trace"].get("episode_ids", [])
        self.assertTrue(written_ids)
        self.test_ids.extend(written_ids)

        # 2. Ask agent to recall the note in this session
        recall_resp = agent.ask("What did I ask in this session?")
        self.assertEqual(recall_resp["trace"]["tool"], "memories")
        self.assertGreater(recall_resp["trace"]["rows"], 0)
        # Check written episode from the exchange
        if recall_resp["trace"].get("written_episode_ids"):
            self.test_ids.extend(recall_resp["trace"]["written_episode_ids"])


if __name__ == "__main__":
    unittest.main()
