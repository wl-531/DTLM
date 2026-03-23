import csv
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from engine import simulate
from metrics import summary
from runner import POLICY_MAP, prepare_data

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "results" / "dtlm_v3_1_continuous_tau"
FINAL_DIR = OUT_DIR / "final"
BASELINE_DIR = ROOT / "results" / "baseline"
V31_FINAL_DIR = ROOT / "results" / "dtlm_v3_1" / "final"
SELECTED_PARAMS_PATH = ROOT / "results" / "dtlm_v3" / "selected_params.json"

SEED = 42
DAY_MS = 24 * 60 * 60 * 1000
WARMUP_DAYS = 2
M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
BASELINE_POLICIES = [
    "lru",
    "lfu",
    "fixed_ttl_lru",
    "gdsf",
    "iat_adaptive_ttl",
    "adaptive_ttl_lru",
    "ttlmin_extnd",
]
DISPLAY_NAMES = {
    "lru": "LRU",
    "lfu": "LFU",
    "fixed_ttl_lru": "Fixed-TTL+LRU",
    "gdsf": "GDSF",
    "iat_adaptive_ttl": "IAT-Adaptive TTL",
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "ttlmin_extnd": "TTLmin_extnd",
    "dtlm_v3_1_continuous_tau": "DTLM v3.1 continuous tau",
}


def ensure_dirs():
    FINAL_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"SAVED: {path}")


def write_text(path, text):
    path.write_text(text, encoding="utf-8")
    print(f"SAVED: {path}")


def baseline_path(policy_name, m_ratio):
    return BASELINE_DIR / f"{policy_name}_{m_ratio:.1f}.json"


def v31_path(m_ratio):
    return V31_FINAL_DIR / f"dtlm_v3_1_{m_ratio:.1f}.json"


def continuous_path(m_ratio):
    return FINAL_DIR / f"dtlm_v3_1_continuous_tau_{m_ratio:.1f}.json"


def write_results_table(rows):
    path = FINAL_DIR / "results_table.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"SAVED: {path}")


def write_ranking_table(rows):
    path = FINAL_DIR / "ranking_table.txt"
    grouped = {}
    for row in rows:
        grouped.setdefault(row["M_ratio"], []).append(row)

    probe_ranks = {}
    lines = []
    for m_ratio in M_RATIOS:
        lines.append(f"M_ratio={m_ratio:.1f}")
        ordered = sorted(grouped[m_ratio], key=lambda row: row["total_cost"])
        for rank, row in enumerate(ordered, start=1):
            lines.append(f"  {rank}. {row['display_name']}: cost={row['total_cost']:.0f}, CSR={row['csr'] * 100:.2f}%")
            if row["policy"] == "dtlm_v3_1_continuous_tau":
                probe_ranks[m_ratio] = rank
        lines.append("")
    avg_rank = sum(probe_ranks[m_ratio] for m_ratio in M_RATIOS) / len(M_RATIOS)
    lines.append("DTLM continuous tau rank summary")
    for m_ratio in M_RATIOS:
        lines.append(f"- M_ratio={m_ratio:.1f}: rank {probe_ranks[m_ratio]}")
    lines.append(f"- average rank: {avg_rank:.2f}")
    write_text(path, "\n".join(lines))
    return probe_ranks, avg_rank


def plot_cost_vs_m(rows):
    path = FINAL_DIR / "cost_vs_M.png"
    grouped = {}
    for row in rows:
        grouped.setdefault(row["policy"], []).append(row)

    plt.figure(figsize=(9, 5))
    for policy_name in BASELINE_POLICIES + ["dtlm_v3_1_continuous_tau"]:
        policy_rows = sorted(grouped[policy_name], key=lambda row: row["M_ratio"])
        x = [row["M_ratio"] for row in policy_rows]
        y = [row["total_cost"] for row in policy_rows]
        linestyle = "--" if policy_name == "iat_adaptive_ttl" else "-"
        linewidth = 2.8 if policy_name == "dtlm_v3_1_continuous_tau" else 1.8
        marker = "o" if policy_name == "dtlm_v3_1_continuous_tau" else None
        plt.plot(x, y, linestyle=linestyle, linewidth=linewidth, marker=marker, label=DISPLAY_NAMES[policy_name])
    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()
    print(f"SAVED: {path}")


def run_probe_policy(data, m_ratio, policy_kwargs, result_label, output_path):
    if output_path.exists():
        return read_json(output_path)

    M = data["ws_mean"] * m_ratio
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS
    policy = POLICY_MAP["dtlm"](M, data["functions_info"], **policy_kwargs)
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    metrics = summary(results, data["functions_info"], M, policy=policy, skip_warmup=True)
    payload = {
        "policy": result_label,
        "source_policy": "dtlm",
        "M_ratio": m_ratio,
        "M_MB": round(M, 1),
        "seed": data["seed"],
        "days": list(data["days"]),
        "working_set_days": list(data["working_set_days"]),
        "warmup_days": WARMUP_DAYS,
        "working_set_mean_MB": round(data["ws_mean"], 1),
        "policy_kwargs": policy_kwargs,
        "metrics": metrics,
        "diagnostics": policy.get_state(),
    }
    write_json(output_path, payload)
    return payload


def simulate_with_tau_tracking(policy, stream, warmup_end_ms):
    next_ttl_check_time = 60000
    request_counts = defaultdict(int)
    recent_calls = defaultdict(deque)
    mem_after_request = []

    for index, (timestamp_ms, func_id, app_id, m_mb) in enumerate(stream):
        while timestamp_ms >= next_ttl_check_time:
            policy.check_ttl(next_ttl_check_time)
            next_ttl_check_time += 60000

        is_cold = policy.on_request(timestamp_ms, func_id)
        mem_used = policy.memory_used()
        if timestamp_ms < warmup_end_ms:
            if (index + 1) % 100000 == 0:
                print(f"Progress: {index + 1} / {len(stream)}")
            continue

        request_counts[func_id] += 1
        recent_calls[func_id].append(timestamp_ms)
        one_hour_ago = timestamp_ms - 3600000
        while recent_calls[func_id] and recent_calls[func_id][0] < one_hour_ago:
            recent_calls[func_id].popleft()
        mem_after_request.append(mem_used)

        if (index + 1) % 100000 == 0:
            print(f"Progress: {index + 1} / {len(stream)}")

    return request_counts, recent_calls, mem_after_request, stream[-1][0]


def summarize_discrete_tau(request_counts, recent_calls, final_timestamp, params):
    rows = []
    counts = Counter()
    for func_id, req_count in request_counts.items():
        calls = deque(recent_calls[func_id])
        one_hour_ago = final_timestamp - 3600000
        while calls and calls[0] < one_hour_ago:
            calls.popleft()
        recent_count = len(calls)
        if recent_count >= params["hot_threshold"]:
            tau_ms = params["tau_hot_ms"]
            tau_bucket = "hot"
        elif recent_count >= params["warm_threshold"]:
            tau_ms = params["tau_warm_ms"]
            tau_bucket = "warm"
        else:
            tau_ms = params["tau_cold_ms"]
            tau_bucket = "cold"
        counts[tau_bucket] += 1
        rows.append({
            "func_id": func_id,
            "request_count": req_count,
            "tau_ms": tau_ms,
            "tau_bucket": tau_bucket,
        })
    total = len(rows) if rows else 1
    return {
        "rows": rows,
        "bucket_ratio": {key: counts[key] / total for key in ["cold", "warm", "hot"]},
        "cold_floor_ratio": counts["cold"] / total,
        "largest_bucket_ratio": max((count / total for count in counts.values()), default=0.0),
    }


def summarize_continuous_tau(policy, request_counts):
    rows = []
    tau_values = []
    hit_tau_min = 0
    hit_tau_max = 0
    for func_id, req_count in request_counts.items():
        details = policy.estimate_tau_for_func(func_id)
        tau_ms = details["tau_ms"]
        tau_values.append(tau_ms)
        hit_tau_min += int(details["hit_tau_min"])
        hit_tau_max += int(details["hit_tau_max"])
        rows.append({
            "func_id": func_id,
            "request_count": req_count,
            "tau_ms": round(tau_ms, 2),
            "raw_tau_ms": round(details["raw_tau_ms"], 2),
            "iat_quantile_ms": round(details["iat_quantile_ms"], 2),
            "tau_min_ms": round(details["tau_min_ms"], 2),
            "tau_max_ms": round(details["tau_max_ms"], 2),
            "hit_tau_min": int(details["hit_tau_min"]),
            "hit_tau_max": int(details["hit_tau_max"]),
        })
    total = len(rows) if rows else 1
    tau_min = min((row["tau_min_ms"] for row in rows), default=0.0)
    tau_max = max((row["tau_max_ms"] for row in rows), default=0.0)
    if tau_values and tau_max > tau_min:
        bins = np.linspace(tau_min, tau_max, 6)
        hist, _ = np.histogram(tau_values, bins=bins)
        largest_bin_ratio = float(max(hist) / len(tau_values))
    else:
        largest_bin_ratio = 1.0 if tau_values else 0.0
    return {
        "rows": rows,
        "summary": {
            "p50": float(np.quantile(tau_values, 0.50)) if tau_values else 0.0,
            "p90": float(np.quantile(tau_values, 0.90)) if tau_values else 0.0,
            "p95": float(np.quantile(tau_values, 0.95)) if tau_values else 0.0,
            "hit_tau_min_ratio": hit_tau_min / total,
            "hit_tau_max_ratio": hit_tau_max / total,
            "largest_bin_ratio": largest_bin_ratio,
        },
    }


def main():
    ensure_dirs()
    selected = read_json(SELECTED_PARAMS_PATH)
    data = prepare_data(seed=SEED, days=(3, 12), working_set_days=(5, 12))

    discrete_kwargs = {
        "p_deactivate": selected["p_deactivate"],
        "hot_threshold": 10,
        "warm_threshold": 1,
        "tau_hot_ms": selected["tau_hot_ms"],
        "tau_warm_ms": selected["tau_warm_ms"],
        "tau_cold_ms": selected["tau_cold_ms"],
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
        "physical_delete_requires_pressure": True,
    }
    continuous_kwargs = dict(discrete_kwargs)
    continuous_kwargs.update({
        "dtlm_mode": "v3_1_continuous_tau",
        "tau_quantile_p": 0.8,
        "tau_cost_alpha": 0.5,
    })

    rows = []
    probe_results = {}
    v31_results = {}
    gdsf_results = {}
    for m_ratio in M_RATIOS:
        for policy_name in BASELINE_POLICIES:
            result = read_json(baseline_path(policy_name, m_ratio))
            rows.append({
                "policy": policy_name,
                "display_name": DISPLAY_NAMES[policy_name],
                "M_ratio": m_ratio,
                "csr": result["metrics"]["cold_start_rate"],
                "total_cost": result["metrics"]["total_cold_start_cost"],
                "avg_mem_util": result["metrics"]["avg_memory_utilization"],
                "result_path": str(baseline_path(policy_name, m_ratio)),
            })
            if policy_name == "gdsf":
                gdsf_results[m_ratio] = result

        v31_results[m_ratio] = read_json(v31_path(m_ratio))
        probe_results[m_ratio] = run_probe_policy(
            data,
            m_ratio,
            continuous_kwargs,
            "dtlm_v3_1_continuous_tau",
            continuous_path(m_ratio),
        )
        result = probe_results[m_ratio]
        rows.append({
            "policy": "dtlm_v3_1_continuous_tau",
            "display_name": DISPLAY_NAMES["dtlm_v3_1_continuous_tau"],
            "M_ratio": m_ratio,
            "csr": result["metrics"]["cold_start_rate"],
            "total_cost": result["metrics"]["total_cold_start_cost"],
            "avg_mem_util": result["metrics"]["avg_memory_utilization"],
            "result_path": str(continuous_path(m_ratio)),
        })

    rows.sort(key=lambda row: (row["M_ratio"], row["total_cost"]))
    write_results_table(rows)
    plot_cost_vs_m(rows)
    probe_ranks, avg_rank = write_ranking_table(rows)

    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS
    M = data["ws_mean"]
    discrete_policy = POLICY_MAP["dtlm"](M, data["functions_info"], **discrete_kwargs)
    discrete_request_counts, discrete_recent_calls, _, final_timestamp = simulate_with_tau_tracking(discrete_policy, data["stream"], warmup_end_ms)
    discrete_tau = summarize_discrete_tau(discrete_request_counts, discrete_recent_calls, final_timestamp, discrete_kwargs)

    continuous_policy = POLICY_MAP["dtlm"](M, data["functions_info"], **continuous_kwargs)
    continuous_request_counts, _, _, _ = simulate_with_tau_tracking(continuous_policy, data["stream"], warmup_end_ms)
    continuous_tau = summarize_continuous_tau(continuous_policy, continuous_request_counts)

    write_csv_path = OUT_DIR / "tau_base_distribution.csv"
    with write_csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "func_id",
            "request_count",
            "tau_ms",
            "raw_tau_ms",
            "iat_quantile_ms",
            "tau_min_ms",
            "tau_max_ms",
            "hit_tau_min",
            "hit_tau_max",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(continuous_tau["rows"])
    print(f"SAVED: {write_csv_path}")

    cold_floor_comparison = {
        "v3_1_discrete": {
            "bucket_ratio": discrete_tau["bucket_ratio"],
            "cold_floor_ratio": discrete_tau["cold_floor_ratio"],
            "largest_bucket_ratio": discrete_tau["largest_bucket_ratio"],
        },
        "continuous_tau": continuous_tau["summary"],
    }
    write_json(OUT_DIR / "cold_floor_comparison.json", cold_floor_comparison)

    guardrail_ok = probe_results[1.0]["metrics"]["total_cold_start_cost"] <= 1.05 * gdsf_results[1.0]["metrics"]["total_cold_start_cost"]
    diversity_ok = continuous_tau["summary"]["hit_tau_min_ratio"] < 0.7 and continuous_tau["summary"]["largest_bin_ratio"] < 0.7
    cold_floor_reduced = discrete_tau["cold_floor_ratio"] - continuous_tau["summary"]["hit_tau_min_ratio"] >= 0.10
    improvement_points = []
    for m_ratio in [0.3, 0.5, 0.7]:
        v31_cost = v31_results[m_ratio]["metrics"]["total_cold_start_cost"]
        probe_cost = probe_results[m_ratio]["metrics"]["total_cold_start_cost"]
        if probe_cost <= 0.98 * v31_cost:
            improvement_points.append(m_ratio)
    performance_ok = len(improvement_points) >= 2

    if not guardrail_ok:
        verdict = "FAIL"
    elif diversity_ok and cold_floor_reduced and performance_ok:
        verdict = "PASS"
    elif len(improvement_points) >= 1 or cold_floor_reduced:
        verdict = "WEAK PASS"
    else:
        verdict = "FAIL"

    summary_lines = [
        "# Continuous tau probe summary",
        "",
        "1. guardrail 检查（硬条件）",
        f"- M=1.0 是否 <= 1.05x GDSF：{'是' if guardrail_ok else '否'}",
        f"- M=1.0 probe cost={probe_results[1.0]['metrics']['total_cold_start_cost']:.0f}, GDSF={gdsf_results[1.0]['metrics']['total_cold_start_cost']:.0f}",
        "",
        "2. 区分度检查",
        f"- tau_base p50 / p90 / p95: {continuous_tau['summary']['p50']:.1f} / {continuous_tau['summary']['p90']:.1f} / {continuous_tau['summary']['p95']:.1f}",
        f"- hitting tau_max ratio: {continuous_tau['summary']['hit_tau_max_ratio'] * 100:.2f}%",
        f"- hitting tau_min ratio: {continuous_tau['summary']['hit_tau_min_ratio'] * 100:.2f}%",
        f"- v3.1 cold-floor ratio: {discrete_tau['cold_floor_ratio'] * 100:.2f}%",
        f"- continuous hitting tau_min ratio: {continuous_tau['summary']['hit_tau_min_ratio'] * 100:.2f}%",
        f"- 是否仍有 >70% 集中在同一区间：{'是' if continuous_tau['summary']['largest_bin_ratio'] > 0.70 else '否'}",
        "",
        "3. 性能检查（核心）",
        f"- M=0.3~0.7 是否持续优于 v3.1：{'是' if performance_ok else '否'}",
        f"- 达标点：{', '.join(str(m) for m in improvement_points) if improvement_points else 'none'}",
        "",
        "4. 最终判定",
        f"- {verdict}",
    ]
    write_text(OUT_DIR / "probe_summary.md", "\n".join(summary_lines))

    probe_metrics = {
        "guardrail_ok": guardrail_ok,
        "diversity_ok": diversity_ok,
        "cold_floor_reduced": cold_floor_reduced,
        "performance_ok": performance_ok,
        "improvement_points": improvement_points,
        "verdict": verdict,
        "continuous_tau_summary": continuous_tau["summary"],
        "discrete_tau_summary": {
            "bucket_ratio": discrete_tau["bucket_ratio"],
            "cold_floor_ratio": discrete_tau["cold_floor_ratio"],
            "largest_bucket_ratio": discrete_tau["largest_bucket_ratio"],
        },
        "avg_rank": avg_rank,
    }
    write_json(OUT_DIR / "tau_probe_metrics.json", probe_metrics)


if __name__ == "__main__":
    main()
