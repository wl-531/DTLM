import unittest

from analysis import function_attribution
from data_loader import classify_hotness


class AnalysisTests(unittest.TestCase):
    def test_function_attribution_output_format(self):
        dtlm_stats = {
            "f_hot": {"cold_start_cost": 300.0, "cold_start_count": 3, "request_count": 100},
            "f_warm": {"cold_start_cost": 40.0, "cold_start_count": 1, "request_count": 50},
            "f_cold": {"cold_start_cost": 10.0, "cold_start_count": 1, "request_count": 10},
        }
        gdsf_stats = {
            "f_hot": {"cold_start_cost": 250.0, "cold_start_count": 2, "request_count": 100},
            "f_warm": {"cold_start_cost": 60.0, "cold_start_count": 2, "request_count": 50},
            "f_cold": {"cold_start_cost": 10.0, "cold_start_count": 1, "request_count": 10},
        }
        functions_info = {
            "f_hot": {"m_i": 512, "c_i": 100},
            "f_warm": {"m_i": 256, "c_i": 50},
            "f_cold": {"m_i": 128, "c_i": 10},
        }
        global_request_counts = {
            "f_hot": 100,
            "f_warm": 50,
            "f_cold": 10,
        }
        hotness_labels = {
            func_id: classify_hotness(func_id, global_request_counts)
            for func_id in global_request_counts
        }

        result = function_attribution(dtlm_stats, gdsf_stats, functions_info, hotness_labels)

        self.assertEqual(set(result.keys()), {"top_10_harmful", "top_10_beneficial", "net_effect_by_hotness", "summary"})
        self.assertEqual(result["summary"]["total_functions"], 3)
        self.assertEqual(result["summary"]["functions_with_difference"], 2)
        self.assertEqual(result["summary"]["net_cost_delta"], 30.0)

        harmful = result["top_10_harmful"]
        beneficial = result["top_10_beneficial"]
        self.assertEqual(len(harmful), 1)
        self.assertEqual(harmful[0]["func_id"], "f_hot")
        self.assertEqual(harmful[0]["delta_cost"], 50.0)
        self.assertEqual(harmful[0]["hotness"], "hot")

        self.assertEqual(len(beneficial), 1)
        self.assertEqual(beneficial[0]["func_id"], "f_warm")
        self.assertEqual(beneficial[0]["delta_cost"], -20.0)
        self.assertEqual(beneficial[0]["hotness"], "warm")

        self.assertEqual(result["net_effect_by_hotness"]["hot"], 50.0)
        self.assertEqual(result["net_effect_by_hotness"]["warm"], -20.0)
        self.assertEqual(result["net_effect_by_hotness"]["cold"], 0.0)


if __name__ == "__main__":
    unittest.main()
