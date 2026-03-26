import numpy as np


def _warmup_cutoff(policy, skip_warmup):
    if not skip_warmup or policy is None:
        return None
    return getattr(policy, "warmup_end_ms", None)


def _after_cutoff(timestamp, cutoff):
    return cutoff is None or timestamp >= cutoff


def _filtered_results(results, skip_warmup=True):
    return [
        (ts, fid, cold, mem, wu)
        for ts, fid, cold, mem, wu in results
        if not (skip_warmup and wu)
    ]


def _quantile(values, q):
    if not values:
        return 0.0
    return float(np.quantile(values, q))


def cold_start_rate(results, skip_warmup=True):
    filtered = _filtered_results(results, skip_warmup)
    if not filtered:
        return 0.0
    cold_count = sum(1 for _, _, cold, _, _ in filtered if cold)
    return cold_count / len(filtered)


def total_cold_start_cost(results, functions_info, skip_warmup=True):
    filtered = _filtered_results(results, skip_warmup)
    cost = 0.0
    for _, fid, cold, _, _ in filtered:
        if cold:
            cost += functions_info.get(fid, {}).get("c_i", 0.0)
    return cost


def _request_weighted_avg_utilization(results, M, skip_warmup=True):
    filtered = [mem for _, _, _, mem, wu in results if not (skip_warmup and wu)]
    if not filtered or M <= 0:
        return 0.0
    return float(np.mean(filtered) / M)


def avg_memory_utilization(results, M, policy=None, skip_warmup=True):
    if policy is not None:
        stats = utilization_stats(results, M, policy=policy, skip_warmup=skip_warmup)
        sample_count = stats["time_weighted"]["sample_count"]
        if sample_count > 0:
            return stats["time_weighted"]["mean"]
    return _request_weighted_avg_utilization(results, M, skip_warmup)


def peak_memory(results, skip_warmup=True):
    filtered = [mem for _, _, _, mem, wu in results if not (skip_warmup and wu)]
    if not filtered:
        return 0.0
    return max(filtered)


def cold_start_breakdown(policy, functions_info, skip_warmup=True):
    breakdown = {
        "initial_cold_starts": 0,
        "initial_cold_cost": 0.0,
        "expiry_induced_cold_starts": 0,
        "expiry_induced_cold_cost": 0.0,
        "eviction_induced_cold_starts": 0,
        "eviction_induced_cold_cost": 0.0,
        "admission_failure_cold_starts": 0,
        "admission_failure_cold_cost": 0.0,
    }
    if policy is None:
        return breakdown

    cutoff = _warmup_cutoff(policy, skip_warmup)
    for event in policy.cold_start_log:
        if not _after_cutoff(event["timestamp"], cutoff):
            continue
        func_id = event["func_id"]
        cost = functions_info.get(func_id, {}).get("c_i", 0.0)
        cause = event["cause"]
        if cause == "initial":
            breakdown["initial_cold_starts"] += 1
            breakdown["initial_cold_cost"] += cost
        elif cause == "expiry_induced":
            breakdown["expiry_induced_cold_starts"] += 1
            breakdown["expiry_induced_cold_cost"] += cost
        elif cause == "eviction_induced":
            breakdown["eviction_induced_cold_starts"] += 1
            breakdown["eviction_induced_cold_cost"] += cost
        elif cause == "admission_failure":
            breakdown["admission_failure_cold_starts"] += 1
            breakdown["admission_failure_cold_cost"] += cost
    return breakdown


def utilization_stats(results, M, policy=None, skip_warmup=True):
    cutoff = _warmup_cutoff(policy, skip_warmup)
    utilization_values = []
    if policy is not None:
        utilization_values = [
            row["memory_utilization"]
            for row in policy.utilization_samples
            if _after_cutoff(row["timestamp"], cutoff)
        ]
    if not utilization_values:
        filtered = [mem / M for _, _, _, mem, wu in results if M > 0 and not (skip_warmup and wu)]
        utilization_values = filtered

    time_weighted = {
        "mean": float(np.mean(utilization_values)) if utilization_values else 0.0,
        "p5": _quantile(utilization_values, 0.05),
        "p50": _quantile(utilization_values, 0.50),
        "p95": _quantile(utilization_values, 0.95),
        "sample_count": len(utilization_values),
    }

    deletion_time = {}
    for reason in ("expiry", "eviction"):
        values = []
        if policy is not None:
            values = [
                row["memory_utilization"]
                for row in policy.deletion_log
                if row["reason"] == reason and _after_cutoff(row["timestamp"], cutoff)
            ]
        deletion_time[reason] = {
            "count": len(values),
            "mean": float(np.mean(values)) if values else 0.0,
            "median": _quantile(values, 0.50),
            "p95": _quantile(values, 0.95),
        }

    return {
        "time_weighted": time_weighted,
        "deletion_time": deletion_time,
    }


def summary(results, functions_info, M, policy=None, memory_unconstrained=False, skip_warmup=True):
    filtered = _filtered_results(results, skip_warmup)
    total_reqs = len(filtered)
    total_colds = sum(1 for _, _, cold, _, _ in filtered if cold)
    breakdown = cold_start_breakdown(policy, functions_info, skip_warmup=skip_warmup)
    util_stats = utilization_stats(results, M, policy=policy, skip_warmup=skip_warmup)
    per_function_stats = {}
    if policy is not None:
        per_function_stats = {
            func_id: {
                "cold_start_cost": float(stats["cold_start_cost"]),
                "cold_start_count": int(stats["cold_start_count"]),
                "request_count": int(stats["request_count"]),
            }
            for func_id, stats in policy.per_function_stats.items()
        }

    return {
        "cold_start_rate": cold_start_rate(results, skip_warmup),
        "total_cold_start_cost": total_cold_start_cost(results, functions_info, skip_warmup),
        "avg_memory_utilization": avg_memory_utilization(results, M, policy=policy, skip_warmup=skip_warmup),
        "peak_memory_mb": peak_memory(results, skip_warmup),
        "total_requests": total_reqs,
        "total_cold_starts": total_colds,
        "memory_unconstrained": memory_unconstrained,
        "cold_start_breakdown": breakdown,
        "utilization_stats": util_stats,
        "per_function_stats": per_function_stats,
    }
