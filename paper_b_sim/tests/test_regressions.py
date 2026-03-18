import unittest

from engine import simulate
from policies.base import CachePolicy
from policies.dtlm import DTLM
from policies.fixed_ttl_lru import FixedTTL_LRU
from policies.ttlmin_extnd import TTLminExtnd


class RecordingPolicy(CachePolicy):
    def __init__(self):
        super().__init__(M=1, functions_info={"f": {"m_i": 1, "c_i": 1}})
        self.checks = []

    def on_request(self, timestamp_ms, func_id):
        return False

    def check_ttl(self, current_time_ms):
        self.checks.append(current_time_ms)

    def get_state(self):
        return {}

    def memory_used(self):
        return 0.0


class RegressionTests(unittest.TestCase):
    def test_engine_scans_ttl_on_minute_boundaries(self):
        policy = RecordingPolicy()
        stream = [
            (0, "f", "app", 1),
            (180000, "f", "app", 1),
        ]

        simulate(policy, stream)

        self.assertEqual(policy.checks, [60000, 120000, 180000])

    def test_fixed_ttl_lru_expires_instances_on_ttl_boundary(self):
        functions_info = {
            "f": {"m_i": 1, "c_i": 1},
            "g": {"m_i": 1, "c_i": 1},
        }
        policy = FixedTTL_LRU(M=10, functions_info=functions_info)
        stream = [
            (0, "f", "app", 1),
            (180000, "g", "app", 1),
            (660000, "f", "app", 1),
        ]

        results = simulate(policy, stream)

        self.assertTrue(results[0][2])
        self.assertTrue(results[1][2])
        self.assertTrue(results[2][2])
        self.assertEqual(policy.ttl_expire_count, 1)

    def test_ttlmin_extnd_evicts_coldest_live_function_under_iat_ttl(self):
        functions_info = {
            "hot": {"m_i": 1, "c_i": 1},
            "cold": {"m_i": 1, "c_i": 1},
            "new": {"m_i": 1, "c_i": 1},
        }
        policy = TTLminExtnd(M=2, functions_info=functions_info)
        policy.ema_iat = {"hot": 100, "cold": 1000}
        policy.warm = {
            "hot": {"m_i": 1, "last_access": 900, "load_time": 0},
            "cold": {"m_i": 1, "last_access": 0, "load_time": 0},
        }
        policy._mem_used = 2

        policy.on_request(1000, "new")

        self.assertIn("hot", policy.warm)
        self.assertNotIn("cold", policy.warm)
        self.assertIn("new", policy.warm)

    def test_dtlm_skips_ttl_under_high_pressure(self):
        functions_info = {
            "a": {"m_i": 1, "c_i": 10},
            "b": {"m_i": 1, "c_i": 10},
        }
        policy = DTLM(M=2, functions_info=functions_info, p_deactivate=0.9, t_protect_ms=0)
        policy.on_request(0, "a")
        policy.on_request(1, "b")

        policy.check_ttl(120000)

        self.assertIn("a", policy.warm_pool)
        self.assertIn("b", policy.warm_pool)
        self.assertEqual(policy.ttl_layer_skipped_scans, 1)
        self.assertEqual(policy.ttl_reclaim_count, 0)

    def test_dtlm_reuses_gdsf_priority_for_eviction(self):
        functions_info = {
            "keep": {"m_i": 1, "c_i": 100},
            "drop": {"m_i": 1, "c_i": 1},
            "new": {"m_i": 1, "c_i": 1},
        }
        policy = DTLM(M=2, functions_info=functions_info, t_protect_ms=0)
        policy.on_request(0, "keep")
        policy.on_request(1, "keep")
        policy.on_request(2, "drop")

        policy.on_request(3, "new")

        self.assertIn("keep", policy.warm_pool)
        self.assertIn("new", policy.warm_pool)
        self.assertNotIn("drop", policy.warm_pool)
        self.assertEqual(policy.eviction_count, 1)

    def test_dtlm_v31_marks_logical_expiry_without_low_pressure_delete(self):
        functions_info = {
            "f": {"m_i": 1, "c_i": 10},
        }
        policy = DTLM(
            M=10,
            functions_info=functions_info,
            p_deactivate=0.9,
            t_protect_ms=0,
            tau_cold_ms=60000,
            tau_warm_ms=60000,
            tau_hot_ms=60000,
            physical_delete_requires_pressure=True,
        )
        policy.on_request(0, "f")

        policy.check_ttl(120000)

        self.assertIn("f", policy.warm_pool)
        self.assertTrue(policy.warm_pool["f"]["logically_expired"])
        self.assertEqual(policy.ttl_reclaim_count, 0)
        self.assertEqual(policy.ttl_layer_skipped_scans, 1)
        self.assertEqual(policy.on_request(180000, "f"), False)

    def test_dtlm_v31_physically_deletes_only_in_pressure_window(self):
        functions_info = {
            "a": {"m_i": 1, "c_i": 10},
            "b": {"m_i": 1, "c_i": 10},
            "c": {"m_i": 1, "c_i": 10},
        }
        policy = DTLM(
            M=3,
            functions_info=functions_info,
            p_deactivate=0.9,
            t_protect_ms=0,
            tau_cold_ms=60000,
            tau_warm_ms=60000,
            tau_hot_ms=60000,
            physical_delete_requires_pressure=True,
        )
        policy.on_request(0, "a")
        policy.check_ttl(120000)
        policy.on_request(180000, "b")
        policy.on_request(180001, "c")

        policy.check_ttl(239000)

        self.assertNotIn("a", policy.warm_pool)
        self.assertIn("b", policy.warm_pool)
        self.assertIn("c", policy.warm_pool)
        self.assertEqual(policy.ttl_reclaim_count, 1)
        self.assertEqual(policy.ttl_layer_active_scans, 1)


if __name__ == "__main__":
    unittest.main()