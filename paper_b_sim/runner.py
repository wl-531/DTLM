import json
import os
import time

from config import OUTPUT_DIR, SEED, M_RATIOS
from data_loader import load_memory, forced_tail_sample, load_invocations_for_apps, load_triggers
from data_loader import build_request_stream, compute_working_set, compute_global_request_counts, build_hotness_labels
from cold_start import calibrate_cold_start
from engine import simulate
from metrics import summary

from policies.lru import LRU
from policies.lfu import LFU
from policies.fixed_ttl_lru import FixedTTL_LRU
from policies.gdsf import GDSF
from policies.adaptive_ttl_only import AdaptiveTTLOnly
from policies.adaptive_ttl_lru import AdaptiveTTL_LRU
from policies.ttlmin_extnd import TTLminExtnd
from policies.dtlm import DTLM
from policies.dtlm_ei import DTLMExpInf
from policies.c2rd import C2RD_SR

DAY_MS = 24 * 60 * 60 * 1000
DEFAULT_WARMUP_DAYS = 2
DEFAULT_WARN_THRESHOLD_SECONDS = 300.0

POLICY_MAP = {
    "lru": LRU,
    "lfu": LFU,
    "fixed_ttl_lru": FixedTTL_LRU,
    "gdsf": GDSF,
    "iat_adaptive_ttl": AdaptiveTTLOnly,
    "adaptive_ttl_lru": AdaptiveTTL_LRU,
    "ttlmin_extnd": TTLminExtnd,
    "dtlm": DTLM,
    "ei_dtlm": DTLMExpInf,
    "c2rd_sr": C2RD_SR,
}

UNCONSTRAINED_POLICIES = set()


def prepare_data(seed=SEED, days=(1, 12), working_set_days=None):
    """Load sampled data once and build both the simulation stream and working-set window."""
    if working_set_days is None:
        working_set_days = (3, 3)

    app_mem = load_memory()
    sampled = forced_tail_sample(app_mem, seed=seed)
    sampled_apps = set(sampled["HashApp"])
    inv = load_invocations_for_apps(sampled_apps)
    triggers = load_triggers()

    func_info_df = inv[["HashApp", "HashFunction"]].drop_duplicates()
    func_info_df = func_info_df.merge(sampled[["HashApp", "m_mb"]], on="HashApp", how="left")
    func_info_df = func_info_df.merge(triggers, on="HashFunction", how="left")

    c_map = calibrate_cold_start(func_info_df)
    functions_info = {}
    for _, row in func_info_df.iterrows():
        fid = row["HashFunction"]
        functions_info[fid] = {"m_i": row["m_mb"], "c_i": c_map.get(fid, 350)}

    print(f"Building request stream day {days[0]}-{days[1]}...")
    stream = build_request_stream(inv, sampled, day_range=days, seed=seed)
    global_request_counts = compute_global_request_counts(stream)
    hotness_labels = build_hotness_labels(global_request_counts)

    print(f"Computing working set (day {working_set_days[0]}-{working_set_days[1]})...")
    ws_stream = build_request_stream(inv, sampled, day_range=working_set_days, seed=seed)
    ws_mean = compute_working_set(ws_stream, sampled)
    print(f"Working set mean: {ws_mean:.1f} MB")

    return {
        "functions_info": functions_info,
        "stream": stream,
        "ws_mean": ws_mean,
        "seed": seed,
        "days": days,
        "working_set_days": working_set_days,
        "day_offset_ms": (days[0] - 1) * DAY_MS,
        "global_request_counts": global_request_counts,
        "hotness_labels": hotness_labels,
    }


def _scale_functions_info(functions_info, cold_start_scale):
    if cold_start_scale == 1.0:
        return functions_info
    scaled = {}
    for fid, info in functions_info.items():
        scaled[fid] = {"m_i": info["m_i"], "c_i": info["c_i"] * cold_start_scale}
    return scaled


def _default_output_path(policy_name, M_ratio, cold_start_scale):
    fname = f"{policy_name}_{M_ratio}_{cold_start_scale}.json"
    return os.path.join(OUTPUT_DIR, fname)


def _resolve_pair_policy(data, config, default_policy_name):
    """Build one policy instance for paired divergence analysis."""
    config = dict(config or {})
    policy_name = config.get("policy_name", default_policy_name)
    cold_start_scale = config.get("cold_start_scale", 1.0)
    policy_kwargs = dict(config.get("policy_kwargs") or {})
    warmup_days = config.get("warmup_days", DEFAULT_WARMUP_DAYS)

    functions_info = _scale_functions_info(data["functions_info"], cold_start_scale)
    if "M_MB" in config:
        M = float(config["M_MB"])
        M_ratio = config.get("M_ratio")
    else:
        M_ratio = config["M_ratio"]
        M = data["ws_mean"] * M_ratio

    policy_cls = POLICY_MAP[policy_name]
    if policy_name == "dtlm" and "sim_start_ms" not in policy_kwargs:
        policy_kwargs["sim_start_ms"] = data["day_offset_ms"]
    policy = policy_cls(M, functions_info, **policy_kwargs)
    warmup_end_ms = data["day_offset_ms"] + warmup_days * DAY_MS
    policy.warmup_end_ms = warmup_end_ms

    return {
        "policy_name": policy_name,
        "policy": policy,
        "functions_info": functions_info,
        "M": M,
        "M_ratio": M_ratio,
        "cold_start_scale": cold_start_scale,
        "policy_kwargs": policy_kwargs,
        "warmup_days": warmup_days,
        "warmup_end_ms": warmup_end_ms,
    }


def _policy_warm_set(policy):
    container = policy._get_cache_container()
    return set(container.keys())


def _next_snapshot_time(start_ms, interval_ms):
    aligned = ((start_ms + interval_ms - 1) // interval_ms) * interval_ms
    return max(interval_ms, aligned)


def _mean(values):
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _pressure_band(utilization):
    if utilization < 0.5:
        return "low (<0.5)"
    if utilization < 0.85:
        return "medium (0.5-0.85)"
    if utilization <= 0.95:
        return "high (0.85-0.95)"
    return "critical (>0.95)"


def _summarize_pressure_band(rows):
    if not rows:
        return {
            "mean_jaccard": 0.0,
            "mean_delta_cost": 0.0,
            "snapshot_count": 0,
        }
    return {
        "mean_jaccard": _mean([row["jaccard_similarity"] for row in rows]),
        "mean_delta_cost": _mean([row["interval_delta_cost"] for row in rows]),
        "snapshot_count": len(rows),
    }


def _summarize_hotness(func_ids, hotness_labels):
    counts = {"hot": 0, "warm": 0, "cold": 0}
    for func_id in func_ids:
        hotness = hotness_labels.get(func_id, "cold")
        if hotness not in counts:
            hotness = "cold"
        counts[hotness] += 1
    return counts


def run_divergence_pair(data, dtlm_config, gdsf_config, snapshot_interval_sec=60) -> dict:
    """Run DTLM and GDSF independently on one stream and snapshot cache divergence."""
    if snapshot_interval_sec <= 0:
        raise ValueError("snapshot_interval_sec must be positive")

    stream = data["stream"]
    if not stream:
        empty_bands = {
            "low (<0.5)": {"mean_jaccard": 0.0, "mean_delta_cost": 0.0, "snapshot_count": 0},
            "medium (0.5-0.85)": {"mean_jaccard": 0.0, "mean_delta_cost": 0.0, "snapshot_count": 0},
            "high (0.85-0.95)": {"mean_jaccard": 0.0, "mean_delta_cost": 0.0, "snapshot_count": 0},
            "critical (>0.95)": {"mean_jaccard": 0.0, "mean_delta_cost": 0.0, "snapshot_count": 0},
        }
        return {
            "snapshots": [],
            "summary": {
                "mean_jaccard": 0.0,
                "divergent_snapshot_ratio": 0.0,
                "mean_interval_delta_cost": 0.0,
                "total_delta_cost": 0.0,
                "by_pressure_band": empty_bands,
                "divergent_func_hotness": {
                    "dtlm_only": {"hot": 0, "warm": 0, "cold": 0},
                    "gdsf_only": {"hot": 0, "warm": 0, "cold": 0},
                },
            },
        }

    dtlm_run = _resolve_pair_policy(data, dtlm_config, "dtlm")
    gdsf_run = _resolve_pair_policy(data, gdsf_config, "gdsf")

    dtlm_policy = dtlm_run["policy"]
    gdsf_policy = gdsf_run["policy"]
    interval_ms = int(snapshot_interval_sec * 1000)
    analysis_start_ms = max(
        stream[0][0],
        dtlm_run["warmup_end_ms"],
        gdsf_run["warmup_end_ms"],
    )

    next_ttl_check_time = 60000
    next_snapshot_time = _next_snapshot_time(analysis_start_ms, interval_ms)
    dtlm_interval_cost = 0.0
    gdsf_interval_cost = 0.0
    snapshots = []
    dtlm_only_funcs = set()
    gdsf_only_funcs = set()

    def advance_ttl_to(target_ms):
        nonlocal next_ttl_check_time
        while target_ms >= next_ttl_check_time:
            dtlm_policy.check_ttl(next_ttl_check_time)
            gdsf_policy.check_ttl(next_ttl_check_time)
            next_ttl_check_time += 60000

    def record_snapshot(snapshot_ts):
        nonlocal dtlm_interval_cost, gdsf_interval_cost
        dtlm_warm_set = _policy_warm_set(dtlm_policy)
        gdsf_warm_set = _policy_warm_set(gdsf_policy)
        dtlm_only = dtlm_warm_set - gdsf_warm_set
        gdsf_only = gdsf_warm_set - dtlm_warm_set
        union = dtlm_warm_set | gdsf_warm_set
        intersection = dtlm_warm_set & gdsf_warm_set
        jaccard_similarity = 1.0 if not union else len(intersection) / len(union)
        dtlm_utilization = dtlm_policy.memory_used() / dtlm_run["M"] if dtlm_run["M"] > 0 else 0.0
        gdsf_utilization = gdsf_policy.memory_used() / gdsf_run["M"] if gdsf_run["M"] > 0 else 0.0

        snapshots.append({
            "timestamp_ms": snapshot_ts,
            "dtlm_warm_set": dtlm_warm_set,
            "gdsf_warm_set": gdsf_warm_set,
            "dtlm_only": dtlm_only,
            "gdsf_only": gdsf_only,
            "jaccard_similarity": jaccard_similarity,
            "dtlm_utilization": dtlm_utilization,
            "gdsf_utilization": gdsf_utilization,
            "dtlm_interval_cost": dtlm_interval_cost,
            "gdsf_interval_cost": gdsf_interval_cost,
            "interval_delta_cost": dtlm_interval_cost - gdsf_interval_cost,
        })

        dtlm_only_funcs.update(dtlm_only)
        gdsf_only_funcs.update(gdsf_only)
        dtlm_interval_cost = 0.0
        gdsf_interval_cost = 0.0

    total = len(stream)
    for index, (ts, func_id, app_id, m_mb) in enumerate(stream):
        while ts >= next_snapshot_time:
            advance_ttl_to(next_snapshot_time)
            record_snapshot(next_snapshot_time)
            next_snapshot_time += interval_ms

        advance_ttl_to(ts)

        dtlm_is_cold = dtlm_policy.on_request(ts, func_id)
        gdsf_is_cold = gdsf_policy.on_request(ts, func_id)
        if ts >= analysis_start_ms:
            if dtlm_is_cold:
                dtlm_interval_cost += dtlm_run["functions_info"].get(func_id, {}).get("c_i", 0.0)
            if gdsf_is_cold:
                gdsf_interval_cost += gdsf_run["functions_info"].get(func_id, {}).get("c_i", 0.0)

        if (index + 1) % 100000 == 0:
            print(f"Pair progress: {index + 1} / {total}")

    pressure_bands = {
        "low (<0.5)": [],
        "medium (0.5-0.85)": [],
        "high (0.85-0.95)": [],
        "critical (>0.95)": [],
    }
    for snapshot in snapshots:
        pressure_bands[_pressure_band(snapshot["dtlm_utilization"])].append(snapshot)

    summary = {
        "mean_jaccard": _mean([row["jaccard_similarity"] for row in snapshots]),
        "divergent_snapshot_ratio": (
            sum(1 for row in snapshots if row["jaccard_similarity"] < 1.0) / len(snapshots)
            if snapshots else 0.0
        ),
        "mean_interval_delta_cost": _mean([row["interval_delta_cost"] for row in snapshots]),
        "total_delta_cost": float(sum(row["interval_delta_cost"] for row in snapshots)),
        "by_pressure_band": {
            label: _summarize_pressure_band(rows)
            for label, rows in pressure_bands.items()
        },
        "divergent_func_hotness": {
            "dtlm_only": _summarize_hotness(dtlm_only_funcs, data.get("hotness_labels", {})),
            "gdsf_only": _summarize_hotness(gdsf_only_funcs, data.get("hotness_labels", {})),
        },
    }

    return {
        "snapshots": snapshots,
        "summary": summary,
    }


def run_single(data, policy_name, M_ratio, cold_start_scale=1.0, policy_kwargs=None,
               output_path=None, warmup_days=DEFAULT_WARMUP_DAYS,
               warn_threshold_seconds=DEFAULT_WARN_THRESHOLD_SECONDS):
    """Run a single experiment from preloaded data."""
    functions_info = _scale_functions_info(data["functions_info"], cold_start_scale)
    stream = data["stream"]
    ws_mean = data["ws_mean"]
    seed = data["seed"]
    days = data["days"]
    policy_kwargs = dict(policy_kwargs or {})

    M = ws_mean * M_ratio
    warmup_end_ms = data["day_offset_ms"] + warmup_days * DAY_MS

    policy_cls = POLICY_MAP[policy_name]
    if policy_name == "dtlm" and "sim_start_ms" not in policy_kwargs:
        policy_kwargs["sim_start_ms"] = data["day_offset_ms"]
    policy = policy_cls(M, functions_info, **policy_kwargs)

    print(f"Running {policy_name} (M={M:.0f}MB, ratio={M_ratio}, scale={cold_start_scale})...")
    t0 = time.time()
    results = simulate(policy, stream, warmup_end_ms=warmup_end_ms)
    elapsed = time.time() - t0
    if warn_threshold_seconds and elapsed > warn_threshold_seconds:
        print(f"WARNING: {policy_name} M_ratio={M_ratio} scale={cold_start_scale} took {elapsed:.1f}s")

    is_unconstrained = policy_name in UNCONSTRAINED_POLICIES
    metrics = summary(results, functions_info, M,
                      policy=policy,
                      memory_unconstrained=is_unconstrained, skip_warmup=True)
    metrics["runtime_seconds"] = round(elapsed, 1)

    result = {
        "policy": policy_name,
        "M_ratio": M_ratio,
        "M_MB": round(M, 1),
        "cold_start_scale": cold_start_scale,
        "seed": seed,
        "days": list(days),
        "working_set_days": list(data["working_set_days"]),
        "warmup_days": warmup_days,
        "working_set_mean_MB": round(ws_mean, 1),
        "metrics": metrics,
    }
    if policy_kwargs:
        result["policy_kwargs"] = policy_kwargs

    path = output_path or _default_output_path(policy_name, M_ratio, cold_start_scale)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"SAVED: {path}")
    return result


def run_all(m_ratios=None, cold_start_scale=1.0, seed=SEED, days=(1, 12),
            working_set_days=None, warmup_days=DEFAULT_WARMUP_DAYS,
            policy_kwargs_map=None, output_dir=None,
            warn_threshold_seconds=DEFAULT_WARN_THRESHOLD_SECONDS):
    """Run all policies x M-ratios using one preloaded dataset."""
    if m_ratios is None:
        m_ratios = M_RATIOS
    policies = list(POLICY_MAP.keys())
    total = len(policies) * len(m_ratios)

    data = prepare_data(seed=seed, days=days, working_set_days=working_set_days)
    policy_kwargs_map = policy_kwargs_map or {}

    count = 0
    for m_ratio in m_ratios:
        for pname in policies:
            count += 1
            print(f"\n{'=' * 60}")
            print(f"[{count}/{total}] {pname}, M_ratio={m_ratio}")
            print(f"{'=' * 60}")
            try:
                output_path = None
                if output_dir:
                    output_path = os.path.join(output_dir, f"{pname}_{m_ratio}_{cold_start_scale}.json")
                result = run_single(
                    data,
                    pname,
                    m_ratio,
                    cold_start_scale=cold_start_scale,
                    policy_kwargs=policy_kwargs_map.get(pname),
                    output_path=output_path,
                    warmup_days=warmup_days,
                    warn_threshold_seconds=warn_threshold_seconds,
                )
                metrics = result["metrics"]
                print(f"  cold_start_rate: {metrics['cold_start_rate']:.4f}")
                print(f"  total_cost: {metrics['total_cold_start_cost']:.0f}")
                print(f"  avg_mem_util: {metrics['avg_memory_utilization']:.4f}")
                print(f"  runtime: {metrics['runtime_seconds']}s")
            except Exception as exc:
                print(f"  ERROR: {exc}")
                import traceback
                traceback.print_exc()

    print(f"\nAll {total} experiments done.")


def run_experiment(policy_name, M_ratio, cold_start_scale=1.0, seed=SEED, days=(1, 12),
                   working_set_days=None, warmup_days=DEFAULT_WARMUP_DAYS,
                   policy_kwargs=None, output_path=None,
                   warn_threshold_seconds=DEFAULT_WARN_THRESHOLD_SECONDS):
    """Run a single experiment with its own data load."""
    data = prepare_data(seed=seed, days=days, working_set_days=working_set_days)
    return run_single(
        data,
        policy_name,
        M_ratio,
        cold_start_scale=cold_start_scale,
        policy_kwargs=policy_kwargs,
        output_path=output_path,
        warmup_days=warmup_days,
        warn_threshold_seconds=warn_threshold_seconds,
    )


if __name__ == "__main__":
    result = run_experiment("lru", 0.3, days=(1, 3), working_set_days=(3, 3))
    metrics = result["metrics"]
    print("\n=== Phase 8 single test ===")
    print(f"Policy: {result['policy']}, M: {result['M_MB']} MB")
    print(f"cold_start_rate: {metrics['cold_start_rate']:.4f}")
    print(f"total_cold_start_cost: {metrics['total_cold_start_cost']:.0f}")
    print(f"total_requests: {metrics['total_requests']}")
    print(f"runtime: {metrics['runtime_seconds']}s")
