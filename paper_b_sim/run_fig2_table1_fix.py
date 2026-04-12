"""One-off run for consistent Fig. 2 and Table 1 gate ablation data."""

import json
import time
from pathlib import Path

from engine import simulate
from metrics import summary
from policies.dtlm import DTLM
from runner import DAY_MS, prepare_data


ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "results" / "gate_ablation"

SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2
M_RATIO = 1.0
COLD_START_SCALE = 1.0
PCT_THRESHOLD = 0.85

BASE_KWARGS = {
    "p_deactivate": 0.95,
    "hot_threshold": 10,
    "warm_threshold": 1,
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
    "t_protect_ms": 60000,
    "ttl_scan_interval_ms": 60000,
}


def pct_le(values, threshold):
    """Percent of values <= threshold."""
    if not values:
        return 0.0
    return sum(1 for value in values if value <= threshold) / len(values) * 100.0


def mean(values):
    """Mean with an empty-list fallback."""
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def run_variant(data, label, gate_on):
    functions_info = data["functions_info"]
    memory_budget = data["ws_mean"] * M_RATIO
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS

    policy_kwargs = dict(BASE_KWARGS)
    policy_kwargs["physical_delete_requires_pressure"] = bool(gate_on)

    policy = DTLM(memory_budget, functions_info, **policy_kwargs)
    print(f"\nRunning {label}: M_ratio={M_RATIO}, cold_start_scale={COLD_START_SCALE}")
    start = time.time()
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    metrics = summary(results, functions_info, memory_budget, policy=policy, skip_warmup=True)
    metrics["runtime_seconds"] = round(time.time() - start, 1)

    pressure_samples = [
        row["memory_utilization"]
        for row in policy.deletion_log
        if row["reason"] == "expiry" and row["timestamp"] >= warmup_end_ms
    ]
    expiry_stats = {
        "count": len(pressure_samples),
        "mean_pressure": mean(pressure_samples),
        "pct_le_085": round(pct_le(pressure_samples, PCT_THRESHOLD), 2),
        "pressure_samples": [round(value, 6) for value in pressure_samples],
    }

    payload = {
        "purpose": "Fig. 2 and Table 1 controlled gate ablation with vanilla GDSF",
        "policy": "dtlm",
        "eviction_policy": "vanilla_gdsf",
        "ei_gdsf": False,
        "variant": label,
        "gate_on": bool(gate_on),
        "M_ratio": M_RATIO,
        "M_MB": round(memory_budget, 1),
        "cold_start_scale": COLD_START_SCALE,
        "seed": data["seed"],
        "days": list(data["days"]),
        "working_set_days": list(data["working_set_days"]),
        "warmup_days": WARMUP_DAYS,
        "working_set_mean_MB": round(data["ws_mean"], 1),
        "policy_kwargs": policy_kwargs,
        "metrics": metrics,
        "final_state": policy.get_state(),
        "expiry_physical_deletion": expiry_stats,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{label}_1.0_vanilla_gdsf.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"SAVED: {output_path}")
    print_summary(output_path, payload)
    return payload


def print_summary(output_path, payload):
    metrics = payload["metrics"]
    breakdown = metrics["cold_start_breakdown"]
    expiry = payload["expiry_physical_deletion"]
    print(f"Summary for {output_path.name}")
    print(f"  total_cold_start_cost: {metrics['total_cold_start_cost']:.1f}")
    print(f"  expiry_induced_cold_start_count: {breakdown['expiry_induced_cold_starts']}")
    print(f"  expiry_deletion_mean_p: {expiry['mean_pressure']:.6f}")
    print(f"  expiry_deletion_count: {expiry['count']}")
    print(f"  expiry_deletion_pct_p_le_0.85: {expiry['pct_le_085']:.2f}%")


def main():
    print("Experiment setup")
    print(f"  seed: {SEED}")
    print(f"  data days: {DAYS}, working-set days: {WORKING_SET_DAYS}")
    print(f"  M_ratio: {M_RATIO}, cold_start_scale: {COLD_START_SCALE}")
    print("  algorithms: DTLM gate-off vs DTLM gate-on, both with vanilla GDSF eviction")
    print("  TTL: cold=120s, warm=360s, hot=1200s")
    print("  metrics: total cold-start cost, expiry-induced cold starts, expiry-deletion pressure")
    print("  output: each variant is saved immediately after it finishes")

    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
    run_variant(data, "gate_off", gate_on=False)
    run_variant(data, "gate_on", gate_on=True)


if __name__ == "__main__":
    main()
