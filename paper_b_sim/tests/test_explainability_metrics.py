import unittest

from engine import simulate
from metrics import summary
from policies.dtlm import DTLM
from policies.lru import LRU


class ExplainabilityMetricsTests(unittest.TestCase):
    def test_eviction_only_policy_has_zero_expiry_induced_cold_starts(self):
        functions_info = {
            "a": {"m_i": 1, "c_i": 10},
            "b": {"m_i": 1, "c_i": 10},
            "c": {"m_i": 1, "c_i": 10},
        }
        policy = LRU(M=2, functions_info=functions_info)
        stream = [
            (0, "a", "app", 1),
            (30000, "b", "app", 1),
            (70000, "c", "app", 1),
            (130000, "a", "app", 1),
        ]

        results = simulate(policy, stream, warmup_end_ms=0)
        metrics = summary(results, functions_info, 2, policy=policy, skip_warmup=True)
        breakdown = metrics["cold_start_breakdown"]

        self.assertEqual(breakdown["expiry_induced_cold_starts"], 0)
        self.assertEqual(breakdown["expiry_induced_cold_cost"], 0.0)
        self.assertGreater(breakdown["eviction_induced_cold_starts"], 0)
        self.assertIn("per_function_stats", metrics)
        self.assertEqual(metrics["per_function_stats"]["a"]["request_count"], 2)
        self.assertEqual(metrics["per_function_stats"]["a"]["cold_start_count"], 2)
        self.assertEqual(metrics["per_function_stats"]["b"]["request_count"], 1)
        self.assertEqual(metrics["per_function_stats"]["c"]["request_count"], 1)

    def test_dtlm_collects_both_deletion_reasons_and_utilization_samples(self):
        functions_info = {
            "a": {"m_i": 1, "c_i": 10},
            "b": {"m_i": 1, "c_i": 10},
            "c": {"m_i": 1, "c_i": 10},
            "d": {"m_i": 1, "c_i": 10},
            "e": {"m_i": 1, "c_i": 10},
        }
        policy = DTLM(
            M=3,
            functions_info=functions_info,
            p_deactivate=0.95,
            t_protect_ms=0,
            tau_cold_ms=60000,
            tau_warm_ms=60000,
            tau_hot_ms=60000,
            physical_delete_requires_pressure=False,
        )
        stream = [
            (0, "a", "app", 1),
            (30000, "b", "app", 1),
            (70000, "c", "app", 1),
            (80000, "d", "app", 1),
            (90000, "e", "app", 1),
            (130000, "a", "app", 1),
        ]

        results = simulate(policy, stream, warmup_end_ms=0)
        metrics = summary(results, functions_info, 3, policy=policy, skip_warmup=True)

        reasons = {row["reason"] for row in policy.deletion_log}
        self.assertIn("expiry", reasons)
        self.assertIn("eviction", reasons)

        time_weighted = metrics["utilization_stats"]["time_weighted"]
        self.assertGreater(time_weighted["sample_count"], 0)
        for key in ("mean", "p5", "p50", "p95"):
            self.assertGreaterEqual(time_weighted[key], 0.0)
            self.assertLessEqual(time_weighted[key], 1.0)


if __name__ == "__main__":
    unittest.main()
