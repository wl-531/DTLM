import unittest

from runner import run_divergence_pair


def build_data(stream):
    return {
        "functions_info": {
            "a": {"m_i": 1, "c_i": 10},
            "b": {"m_i": 1, "c_i": 10},
            "c": {"m_i": 1, "c_i": 10},
        },
        "stream": stream,
        "ws_mean": 1.0,
        "seed": 42,
        "days": (1, 1),
        "working_set_days": (1, 1),
        "day_offset_ms": 0,
        "hotness_labels": {
            "a": "hot",
            "b": "warm",
            "c": "cold",
        },
    }


class DivergencePairTests(unittest.TestCase):
    def test_large_memory_pair_stays_aligned(self):
        data = build_data([
            (0, "a", "app", 1),
            (30000, "b", "app", 1),
            (90000, "c", "app", 1),
            (180000, "a", "app", 1),
        ])

        result = run_divergence_pair(
            data,
            dtlm_config={
                "M_MB": 10,
                "warmup_days": 0,
                "policy_kwargs": {
                    "p_deactivate": 0.95,
                    "t_protect_ms": 0,
                    "tau_cold_ms": 60000,
                    "tau_warm_ms": 60000,
                    "tau_hot_ms": 60000,
                    "physical_delete_requires_pressure": True,
                },
            },
            gdsf_config={
                "M_MB": 10,
                "warmup_days": 0,
            },
            snapshot_interval_sec=60,
        )

        snapshots = result["snapshots"]
        self.assertEqual([row["timestamp_ms"] for row in snapshots], [60000, 120000, 180000])
        self.assertTrue(all(row["jaccard_similarity"] == 1.0 for row in snapshots))
        self.assertTrue(all(row["interval_delta_cost"] == 0.0 for row in snapshots))

        summary = result["summary"]
        self.assertEqual(summary["mean_jaccard"], 1.0)
        self.assertEqual(summary["divergent_snapshot_ratio"], 0.0)
        self.assertEqual(summary["total_delta_cost"], 0.0)

    def test_time_jump_backfills_snapshots_and_keeps_instances_independent(self):
        data = build_data([
            (0, "a", "app", 1),
            (180000, "b", "app", 1),
            (210000, "a", "app", 1),
            (240000, "c", "app", 1),
        ])

        result = run_divergence_pair(
            data,
            dtlm_config={
                "M_MB": 10,
                "warmup_days": 0,
                "policy_kwargs": {
                    "p_deactivate": 0.95,
                    "t_protect_ms": 0,
                    "tau_cold_ms": 60000,
                    "tau_warm_ms": 60000,
                    "tau_hot_ms": 60000,
                    "physical_delete_requires_pressure": False,
                },
            },
            gdsf_config={
                "M_MB": 10,
                "warmup_days": 0,
            },
            snapshot_interval_sec=60,
        )

        snapshots = result["snapshots"]
        self.assertEqual([row["timestamp_ms"] for row in snapshots], [60000, 120000, 180000, 240000])
        self.assertEqual(snapshots[0]["dtlm_warm_set"], set())
        self.assertEqual(snapshots[0]["gdsf_only"], {"a"})
        self.assertEqual(snapshots[1]["gdsf_only"], {"a"})
        self.assertGreater(snapshots[3]["interval_delta_cost"], 0.0)

        summary = result["summary"]
        self.assertGreater(summary["divergent_snapshot_ratio"], 0.0)
        self.assertGreater(summary["total_delta_cost"], 0.0)
        self.assertGreaterEqual(
            summary["divergent_func_hotness"]["gdsf_only"]["hot"]
            + summary["divergent_func_hotness"]["gdsf_only"]["warm"]
            + summary["divergent_func_hotness"]["gdsf_only"]["cold"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
