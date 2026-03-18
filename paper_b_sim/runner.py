import json
import os
import time

from config import OUTPUT_DIR, SEED, M_RATIOS
from data_loader import load_memory, forced_tail_sample, load_invocations_for_apps, load_triggers
from data_loader import build_request_stream, compute_working_set
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
}

UNCONSTRAINED_POLICIES = {"iat_adaptive_ttl"}


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
