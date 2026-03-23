def function_attribution(dtlm_stats: dict, baseline_stats: dict, functions_info: dict, hotness_labels: dict) -> dict:
    """Compare DTLM vs baseline per-function cold-start cost for attribution analysis."""
    all_func_ids = set(functions_info) | set(dtlm_stats) | set(baseline_stats)
    rows = []
    net_effect_by_hotness = {
        "hot": {"total_delta_cost": 0.0, "function_count": 0, "total_request_count": 0},
        "warm": {"total_delta_cost": 0.0, "function_count": 0, "total_request_count": 0},
        "cold": {"total_delta_cost": 0.0, "function_count": 0, "total_request_count": 0},
    }

    for func_id in all_func_ids:
        dtlm_entry = dtlm_stats.get(func_id, {})
        bl_entry = baseline_stats.get(func_id, {})
        info = functions_info.get(func_id, {})
        delta_cost = float(dtlm_entry.get("cold_start_cost", 0.0)) - float(bl_entry.get("cold_start_cost", 0.0))
        hotness = hotness_labels.get(func_id, "cold")
        request_count = int(dtlm_entry.get("request_count", bl_entry.get("request_count", 0)))
        row = {
            "func_id": func_id,
            "delta_cost": delta_cost,
            "hotness": hotness,
            "memory_mb": float(info.get("memory_mb", info.get("m_i", 0.0))),
            "cold_start_cost_ms": float(info.get("cold_start_cost_ms", info.get("c_i", 0.0))),
            "request_count": request_count,
            "dtlm_cold_count": int(dtlm_entry.get("cold_start_count", 0)),
            "baseline_cold_count": int(bl_entry.get("cold_start_count", 0)),
        }
        rows.append(row)
        if hotness not in net_effect_by_hotness:
            net_effect_by_hotness[hotness] = {"total_delta_cost": 0.0, "function_count": 0, "total_request_count": 0}
        net_effect_by_hotness[hotness]["total_delta_cost"] += delta_cost
        net_effect_by_hotness[hotness]["function_count"] += 1
        net_effect_by_hotness[hotness]["total_request_count"] += request_count

    harmful = [row for row in rows if row["delta_cost"] > 0]
    harmful.sort(key=lambda row: (-row["delta_cost"], -row["request_count"], row["func_id"]))

    beneficial = [row for row in rows if row["delta_cost"] < 0]
    beneficial.sort(key=lambda row: (row["delta_cost"], -row["request_count"], row["func_id"]))

    net_cost_delta = sum(row["delta_cost"] for row in rows)
    changed_functions = sum(1 for row in rows if row["delta_cost"] != 0)

    return {
        "top_10_harmful": harmful[:10],
        "top_10_beneficial": beneficial[:10],
        "net_effect_by_hotness": net_effect_by_hotness,
        "summary": {
            "total_functions": len(all_func_ids),
            "changed_functions": changed_functions,
            "net_delta_cost": net_cost_delta,
            "beneficial_count": len(beneficial),
            "harmful_count": len(harmful),
        },
    }
