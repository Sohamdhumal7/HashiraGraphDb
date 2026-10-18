"""OPT-IN live checks against YOUR FalkorDB Cloud. Uses a throwaway graph and deletes it afterwards.

PowerShell:  $env:HASHIRAGRAPH_LIVE_TEST="1"; python -m unittest tests.test_cloud_live -v
It never touches tenant_hashira_demo / tenant_demo_acme: it creates tenant_ci_scratch_<random>.
"""
import os, sys, unittest, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if os.getenv("HASHIRAGRAPH_LIVE_TEST") != "1":
    raise unittest.SkipTest("set HASHIRAGRAPH_LIVE_TEST=1 to run live Cloud checks")

import action_agent as aa, graph_store as gs, insights, seed_graph as sg, simulation as sim


class CloudChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client, cls.g = gs.connect_graph("ci_scratch_" + uuid.uuid4().hex[:8])  # scratch graph, deleted in tearDownClass
        sg.seed_graph(cls.g)

    @classmethod
    def tearDownClass(cls):
        try: cls.g.delete()
        finally: gs.close_client(cls.client)

    def _seed_counts(self):
        n = gs.query_rows(self.g, "MATCH (n) WHERE NOT n:MemoryEpisode RETURN count(n)", read_only=True)[0][0]
        r = gs.query_rows(self.g, "MATCH (a)-[r]->() WHERE NOT a:MemoryEpisode RETURN count(r)", read_only=True)[0][0]
        return n, r  # memory episodes are excluded so test order cannot matter

    def test_seed_is_idempotent_with_expected_counts(self):
        self.assertEqual(self._seed_counts(), (19, 20))
        sg.seed_graph(self.g); self.assertEqual(self._seed_counts(), (19, 20))

    def test_blast_radius(self):
        got = {r[0]: r[1] for r in sg.run_blast_radius_query(self.g, "Payment API")}
        self.assertEqual(got, {"Checkout API": "Checkout Team", "Web Checkout": "Web Team"})

    def test_three_documented_simulations_and_read_only(self):
        expect = {"Payment API": {"Checkout API": "DOWN", "Web Checkout": "DOWN"},
                  "Ledger Service": {"Payment API": "DOWN", "Checkout API": "DOWN", "Web Checkout": "DOWN"},
                  "Fraud Service": {"Checkout API": "DEGRADED", "Web Checkout": "DEGRADED"}}
        for target, want in expect.items():
            rep = sim.simulate_failure(self.g, [target])
            self.assertEqual({a["service"]: a["status"] for a in rep["affected"]}, want, target)
            self.assertTrue(rep["source_graph_unchanged"])

    def test_fragility_and_server_algorithm_support_is_reported(self):
        rep = insights.fragility_ranking(self.g)
        self.assertEqual(rep["ranking"][0]["service"], "Ledger Service")
        print("\n[live] betweenness status:", rep["algorithm_status"])

    def test_memory_write_recall_and_scratch_isolation(self):
        eid = aa.persist_episode(self.g, "hashira_demo", "live-s1", "Remember: Payment API is primary", "Payment API is primary", [], source="User project note", kind="user_note")
        rows = gs.query_rows(self.g, "MATCH (m:MemoryEpisode {id:$id})-[:MENTIONS]->(s:Service) RETURN s.name", {"id": eid}, read_only=True)
        self.assertEqual(rows, [["Payment API"]])
        other_client, other = gs.connect_graph("ci_scratch_" + uuid.uuid4().hex[:8])
        try:
            self.assertIn(gs.query_rows(other, "MATCH (m:MemoryEpisode) RETURN count(m)", read_only=True), ([], [[0]]))
        finally:
            try: other.delete()
            except Exception: pass
            gs.close_client(other_client)


if __name__ == "__main__":
    unittest.main()
