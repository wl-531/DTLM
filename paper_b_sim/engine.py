def simulate(policy, request_stream, warmup_end_ms=None):
    """Event-driven simulation with minute-boundary TTL scans."""
    results = []
    total = len(request_stream)
    next_ttl_check_time = 60000
    policy.warmup_end_ms = warmup_end_ms

    for i, (ts, func_id, app_id, m_mb) in enumerate(request_stream):
        while ts >= next_ttl_check_time:
            policy.check_ttl(next_ttl_check_time)
            policy.record_utilization_sample(next_ttl_check_time)
            next_ttl_check_time += 60000

        is_cold = policy.on_request(ts, func_id)
        if is_cold:
            if not policy.ever_seen.get(func_id, False):
                cause = "initial"
            else:
                last_reason = policy.last_removal_reason.get(func_id)
                if last_reason == "expiry":
                    cause = "expiry_induced"
                elif last_reason == "eviction":
                    cause = "eviction_induced"
                else:
                    cause = "initial"
            policy.current_cold_start_cause = cause
            policy.cold_start_log.append({
                "func_id": func_id,
                "cause": cause,
                "timestamp": ts,
            })
            policy.mark_cache_inserted(func_id)
        else:
            policy.current_cold_start_cause = None

        mem_used = policy.memory_used()
        is_warmup = warmup_end_ms is not None and ts < warmup_end_ms
        results.append((ts, func_id, is_cold, mem_used, is_warmup))

        if (i + 1) % 100000 == 0:
            print(f"Progress: {i + 1} / {total}")

    print(f"Simulation done: {total} requests processed")
    return results
