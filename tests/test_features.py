"""Tests for the new features: chat simulation, grounding verification, fragility ranking, memory kinds."""
import os, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.embedded import AVAILABLE, counts, fresh_graph, seeded_graph
import action_agent as aa
import insights, simulation as sim, verification as ver

NO_LLM = mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})


class VerificationTests(unittest.TestCase):
    EV = [{"service": "Checkout API", "owner": "Checkout Team", "nodes": ["Checkout API", "Payment API"], "edges": ["DEPENDS_ON"]}]

    def test_supported_answer_verified(self):
        r = ver.verify_answer("Checkout API, owned by Checkout Team, depends on Payment API.", self.EV)
        self.assertEqual(r["status"], "verified")

    def test_invented_team_and_incident_are_flagged(self):
        r = ver.verify_answer("Checkout API is owned by Risk Team after INC-9999.", self.EV)
        self.assertEqual(r["status"], "unsupported_claims"); self.assertEqual(set(r["unsupported"]), {"Risk Team", "INC-9999"})

    def test_no_evidence(self):
        self.assertEqual(ver.verify_answer("Payment API is down", [])["status"], "no_evidence")

    def test_name_boundaries(self):
        # "Payments Team" must not be confused with the "Payment API" service
        r = ver.verify_answer("Payments Team", [{"team": "Payments Team"}]); self.assertEqual(r["status"], "verified")

    @unittest.skipUnless(AVAILABLE, "FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
    def test_hallucinating_llm_answer_is_replaced_by_evidence(self):
        g = seeded_graph(); agent = aa.ActionAgent(g, "hashira_demo", "s")
        with mock.patch.object(aa, "compose_answer", return_value=("Risk Team owns Payment API.", None)):
            r = agent.ask("Who owns Payment API?")
        self.assertEqual(r["trace"]["verification"]["status"], "unsupported_claims")
        self.assertNotIn("Risk Team", r["answer"]); self.assertIn("Payments Team", r["answer"])


@unittest.skipUnless(AVAILABLE, "FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
@NO_LLM
class ChatSimulationTests(unittest.TestCase):
    def setUp(self): self.g = seeded_graph(); self.a = aa.ActionAgent(self.g, "hashira_demo", "s1")

    def test_routing(self):
        self.assertEqual(aa.heuristic_plan("Simulate failure of Ledger Service")["tool"], "simulate")
        plan = aa.heuristic_plan("What if Payment API and Fraud Service go down?")
        self.assertEqual((plan["tool"], set(plan["service"].split("|"))), ("simulate", {"Payment API", "Fraud Service"}))
        # the headline blast-radius question must keep its original routing
        self.assertEqual(aa.heuristic_plan("If the Payment API fails, which upstream services may be affected, and which teams own them?")["tool"], "blast_radius")
        self.assertEqual(aa.heuristic_plan("What did we simulate earlier?")["kind"], "simulation")

    def test_ledger_simulation_via_chat(self):
        r = self.a.ask("Simulate failure of Ledger Service")
        self.assertEqual(r["trace"]["tool"], "simulate")
        got = {e["service"]: e["status"] for e in r["evidence"]}
        self.assertEqual(got, {"Payment API": "DOWN", "Checkout API": "DOWN", "Web Checkout": "DOWN"})
        self.assertIn("nothing was changed", r["answer"]); self.assertEqual(r["trace"]["verification"]["status"], "verified")

    def test_fraud_simulation_is_degraded_not_down(self):
        r = self.a.ask("Simulate failure of Fraud Service")
        self.assertEqual({e["status"] for e in r["evidence"]}, {"DEGRADED"})
        self.assertEqual(r["evidence"][1]["edges"], ["DEPENDS_ON (hard)", "DEPENDS_ON (soft)"])

    def test_simulation_does_not_mutate_facts_and_is_remembered_as_simulation(self):
        before = self.g.ro_query("MATCH (n) WHERE NOT n:MemoryEpisode RETURN count(n)").result_set[0][0]
        rels = self.g.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        self.a.ask("What if Payment API goes down?")
        self.assertEqual(self.g.ro_query("MATCH (n) WHERE NOT n:MemoryEpisode RETURN count(n)").result_set[0][0], before)
        self.assertEqual(self.g.ro_query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0], rels + 1)  # only the MENTIONS link
        kinds = self.g.ro_query("MATCH (m:MemoryEpisode) RETURN m.kind").result_set
        self.assertEqual(kinds, [["simulation"]])

    def test_recall_of_earlier_simulation_in_new_session(self):
        self.a.ask("Simulate failure of Ledger Service"); self.a.ask("Remember: unrelated note")
        r = aa.ActionAgent(self.g, "hashira_demo", "s2").ask("What did we simulate earlier?")
        self.assertEqual({e["kind"] for e in r["evidence"]}, {"simulation"}); self.assertIn("Ledger Service", r["answer"])

    def test_note_recall_ignores_simulation_noise(self):
        self.a.ask("Simulate failure of Ledger Service"); self.a.ask("Remember: HashiraGraph is our hackathon project")
        r = aa.ActionAgent(self.g, "hashira_demo", "s2").ask("What did I tell you about the hackathon?")
        self.assertEqual({e["kind"] for e in r["evidence"]}, {"user_note"})

    def test_simulate_without_known_service_reports_error_honestly(self):
        r = self.a.ask("Simulate a failure")
        self.assertIn("error", r["trace"]); self.assertEqual(r["evidence"], [])

    def test_failed_simulation_is_not_labelled_simulation(self):
        self.a.ask("Simulate a failure")
        self.assertEqual({k[0] for k in self.g.ro_query("MATCH (m:MemoryEpisode) RETURN m.kind").result_set}, {"exchange"})


@unittest.skipUnless(AVAILABLE, "FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
@NO_LLM
class RecallHygieneTests(unittest.TestCase):
    def test_recall_answers_are_not_recursively_recalled(self):
        g = seeded_graph(); a = aa.ActionAgent(g, "hashira_demo", "s")
        a.ask("Remember: Payment API is the primary service")
        a.ask("What did I tell you about Payment API?")           # logged as kind=recall
        r = aa.ActionAgent(g, "hashira_demo", "s2").ask("What do we know about Payment API?")
        self.assertFalse(any(e["kind"] == "recall" for e in r["evidence"]))
        self.assertNotIn("Retrieved persistent memory records", r["answer"].split("\n", 1)[1])
        kinds = {k[0] for k in g.ro_query("MATCH (m:MemoryEpisode) RETURN m.kind").result_set}
        self.assertIn("recall", kinds)  # still auditable

    def test_legacy_episodes_without_kind_are_still_recalled(self):
        g = seeded_graph()
        g.query("CREATE (:MemoryEpisode {id:'old',tenant_id:'hashira_demo',session_id:'x',created_at:'2026-01-01',summary:'legacy hackathon note',user_message:'legacy',source:'t'})")
        r = aa.ActionAgent(g, "hashira_demo", "s").ask("What do we know about the hackathon history?")
        self.assertTrue(any(e["episode_id"] == "old" for e in r["evidence"]))


@unittest.skipUnless(AVAILABLE, "FalkorDBLite not installed (pip install falkordblite; Linux/macOS/WSL)")
class FragilityTests(unittest.TestCase):
    def setUp(self): self.g = seeded_graph(); self.rep = insights.fragility_ranking(self.g)

    def test_matches_simulator_for_every_service(self):
        for row in self.rep["ranking"]:
            if row["hard_dependents_down"] or True:
                affected = sim.simulate_failure(self.g, [row["service"]])["affected"]
                self.assertEqual(sorted(a["service"] for a in affected if a["status"] == "DOWN"), row["down_if_lost"], row["service"])
                self.assertEqual(sorted(a["service"] for a in affected if a["status"] == "DEGRADED"), row["degraded_if_lost"], row["service"])

    def test_known_ordering_on_seed(self):
        names = [r["service"] for r in self.rep["ranking"]]
        self.assertEqual(names[0], "Ledger Service"); self.assertEqual(self.rep["ranking"][0]["hard_dependents_down"], 3)
        self.assertEqual(names[-1], "Web Checkout")

    def test_every_service_ranked_once(self):
        self.assertEqual(len(self.rep["ranking"]), 7); self.assertEqual(len({r["service"] for r in self.rep["ranking"]}), 7)

    def test_betweenness_present_when_supported_and_degrades_gracefully(self):
        self.assertEqual(self.rep["algorithm_status"], "ok")
        by = {r["service"]: r["betweenness"] for r in self.rep["ranking"]}
        self.assertTrue(all(isinstance(v, float) for v in by.values()))
        self.assertEqual(by["Web Checkout"], 0.0)  # nothing depends on it, so nothing routes through it
        self.assertGreater(max(by.values()), 0)    # sampled by the server: assert presence, not an exact winner
        class NoAlgo:
            name = "x"
            def __init__(s, g): s.g = g
            def ro_query(s, q, p=None):
                if "algo." in q: raise RuntimeError("Unknown function 'algo.betweenness'")
                return s.g.ro_query(q, p or {})
        class NoSeed(NoAlgo):
            def ro_query(s, q, p=None):
                if "samplingSeed" in q: raise RuntimeError("Unknown configuration key samplingSeed")
                return s.g.ro_query(q, p or {})
        self.assertIn("unseeded", insights.fragility_ranking(NoSeed(self.g))["algorithm_status"])
        rep = insights.fragility_ranking(NoAlgo(self.g))
        self.assertIn("unavailable", rep["algorithm_status"]); self.assertIsNone(rep["ranking"][0]["betweenness"])
        self.assertEqual([r["service"] for r in rep["ranking"]], [r["service"] for r in self.rep["ranking"]])

    def test_read_only(self):
        before = counts(self.g); insights.fragility_ranking(self.g); self.assertEqual(counts(self.g), before)


if __name__ == "__main__":
    unittest.main()
