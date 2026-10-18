"""Runs the real Streamlit script headlessly (AppTest) against the embedded FalkorDB engine."""
import os, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.embedded import AVAILABLE
if not AVAILABLE:
    raise unittest.SkipTest("FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
from tests.embedded import seeded_graph
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.g = seeded_graph()
        self.p = [mock.patch("graph_store.connect_graph", lambda tenant=None: (object(), self.g)),
                  mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})]
        for p in self.p: p.start()

    def tearDown(self):
        for p in self.p: p.stop()

    def _app(self):
        at = AppTest.from_file(APP, default_timeout=60); at.run(); self.assertFalse(at.exception, at.exception); return at

    def test_loads_with_live_counts(self):
        at = self._app(); vals = {m.label: m.value for m in at.metric}
        self.assertEqual((vals["Graph Nodes"], vals["Relationships"]), ("19", "20"))

    def test_chat_blast_radius_then_memory_roundtrip(self):
        at = self._app()
        at.chat_input[0].set_value("If the Payment API fails, which upstream services may be affected, and which teams own them?").run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["last_result"]["trace"]["tool"], "blast_radius")
        at.chat_input[0].set_value("Remember this project note: HashiraGraph is our hackathon project").run()
        at.chat_input[0].set_value("What did I tell you about the hackathon?").run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("hackathon project", at.session_state["last_result"]["answer"])
        n = self.g.ro_query("MATCH (m:MemoryEpisode) RETURN count(m)").result_set[0][0]
        self.assertEqual(n, 3)  # blast question + note + recall question: each persisted exactly once

    def test_simulator_buttons_fragility_and_save(self):
        at = self._app()
        at.multiselect[0].set_value(["Ledger Service"]).run()
        next(b for b in at.button if b.label == "Simulate Impact").click().run()
        self.assertFalse(at.exception, at.exception)
        rep = at.session_state["simulation_report"]
        self.assertEqual({a["service"] for a in rep["affected"]}, {"Payment API", "Checkout API", "Web Checkout"}); self.assertTrue(rep["source_graph_unchanged"])
        next(b for b in at.button if b.label == "Save this simulation to persistent memory").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(self.g.ro_query("MATCH (m:MemoryEpisode {kind:'simulation'}) RETURN count(m)").result_set[0][0], 1)
        next(b for b in at.button if b.label == "Compute fragility ranking").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["fragility_report"]["ranking"][0]["service"], "Ledger Service")

    def test_curator_button(self):
        at = self._app()
        next(b for b in at.button if b.label == "Run Curator Maintenance").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state["curator_report"]["generic_relationships_promoted"], 0)

    def test_unavailable_database_is_reported_not_faked(self):
        with mock.patch("graph_store.connect_graph", side_effect=RuntimeError("boom")):
            at = AppTest.from_file(APP, default_timeout=60).run()
        self.assertFalse(at.exception); self.assertTrue(any("Unavailable" in e.value for e in at.error))
        self.assertEqual(len(at.metric), 0)


if __name__ == "__main__":
    unittest.main()
