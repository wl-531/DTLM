import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from engine import simulate
from metrics import summary
from runner import POLICY_MAP, prepare_data

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "results" / "dtlm_v3_1"
FINAL_DIR = OUT_DIR / "final"
BASELINE_DIR = ROOT / "results" / "baseline"
V3_FINAL_DIR = ROOT / "results" / "dtlm_v3" / "final"
V3_DIAG_PATH = ROOT / "results" / "dtlm_v3_diagnosis_m1" / "diagnosis_metrics.json"
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
    "iat_adaptive_ttl": "IAT-Adaptive TTL (Adm.)",
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "ttlmin_extnd": "TTLmin_extnd",
    "dtlm_v3_1": "DTLM v3.1",
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


def v3_result_path(m_ratio):
    return V3_FINAL_DIR / f"dtlm_v3_{m_ratio:.1f}.json"


def v31_result_path(m_ratio):
    return FINAL_DIR / f"dtlm_v3_1_{m_ratio:.1f}.json"


def run_dtlm_v31(data, m_ratio, policy_kwargs):
    output_path = v31_result_path(m_ratio)
    if output_path.exists():
        return read_json(output_path)

    M = data["ws_mean"] * m_ratio
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS
    policy = POLICY_MAP["dtlm"](M, data["functions_info"], **policy_kwargs)
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    metrics = summary(results, data["functions_info"], M, policy=policy, skip_warmup=True)
    payload = {
        "policy": "dtlm_v3_1",
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

    dtlm_ranks = {}
    lines = []
    for m_ratio in M_RATIOS:
        lines.append(f"M_ratio={m_ratio:.1f}")
        ordered = sorted(grouped[m_ratio], key=lambda row: row["total_cost"])
        for rank, row in enumerate(ordered, start=1):
            lines.append(f"  {rank}. {row['display_name']}: cost={row['total_cost']:.0f}, CSR={row['csr'] * 100:.2f}%")
            if row["policy"] == "dtlm_v3_1":
                dtlm_ranks[m_ratio] = rank
        lines.append("")
    avg_rank = sum(dtlm_ranks[m_ratio] for m_ratio in M_RATIOS) / len(M_RATIOS)
    lines.append("DTLM v3.1 rank summary")
    for m_ratio in M_RATIOS:
        lines.append(f"- M_ratio={m_ratio:.1f}: rank {dtlm_ranks[m_ratio]}")
    lines.append(f"- average rank: {avg_rank:.2f}")
    write_text(path, "\n".join(lines))
    return dtlm_ranks, avg_rank


def plot_cost_vs_m(rows):
    path = FINAL_DIR / "cost_vs_M.png"
    grouped = {}
    for row in rows:
        grouped.setdefault(row["policy"], []).append(row)

    plt.figure(figsize=(9, 5))
    for policy_name in BASELINE_POLICIES + ["dtlm_v3_1"]:
        policy_rows = sorted(grouped[policy_name], key=lambda row: row["M_ratio"])
        x = [row["M_ratio"] for row in policy_rows]
        y = [row["total_cost"] for row in policy_rows]
        linestyle = "--" if policy_name == "iat_adaptive_ttl" else "-"
        linewidth = 2.8 if policy_name == "dtlm_v3_1" else 1.8
        marker = "o" if policy_name == "dtlm_v3_1" else None
        plt.plot(x, y, linestyle=linestyle, linewidth=linewidth, marker=marker, label=DISPLAY_NAMES[policy_name])
    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()
    print(f"SAVED: {path}")


def main():
    ensure_dirs()
    selected = read_json(SELECTED_PARAMS_PATH)
    v3_diag = read_json(V3_DIAG_PATH)
    data = prepare_data(seed=SEED, days=(3, 12), working_set_days=(5, 12))

    policy_kwargs = {
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

    rows = []
    v31_results = {}
    v3_results = {}
    gdsf_results = {}
    adaptive_results = {}

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
            if policy_name == "adaptive_ttl_lru":
                adaptive_results[m_ratio] = result

        v3_results[m_ratio] = read_json(v3_result_path(m_ratio))
        v31_results[m_ratio] = run_dtlm_v31(data, m_ratio, policy_kwargs)
        result = v31_results[m_ratio]
        rows.append({
            "policy": "dtlm_v3_1",
            "display_name": DISPLAY_NAMES["dtlm_v3_1"],
            "M_ratio": m_ratio,
            "csr": result["metrics"]["cold_start_rate"],
            "total_cost": result["metrics"]["total_cold_start_cost"],
            "avg_mem_util": result["metrics"]["avg_memory_utilization"],
            "result_path": str(v31_result_path(m_ratio)),
        })

    rows.sort(key=lambda row: (row["M_ratio"], row["total_cost"]))
    write_results_table(rows)
    plot_cost_vs_m(rows)
    dtlm_ranks, avg_rank = write_ranking_table(rows)

    guardrail_fixed = all(
        v31_results[m]["metrics"]["total_cold_start_cost"] <= 1.1 * gdsf_results[m]["metrics"]["total_cold_start_cost"]
        for m in M_RATIOS
    )
    low_m_regression = any(
        v31_results[m]["metrics"]["total_cold_start_cost"] > 1.1 * v3_results[m]["metrics"]["total_cold_start_cost"]
        for m in [0.1, 0.2, 0.3]
    )
    v3_avg_rank = 2.50
    avg_rank_direction = "下降" if avg_rank > v3_avg_rank else "上升"

    m1_v3_cost = v3_results[1.0]["metrics"]["total_cold_start_cost"]
    m1_v31_cost = v31_results[1.0]["metrics"]["total_cold_start_cost"]
    m1_gdsf_cost = gdsf_results[1.0]["metrics"]["total_cold_start_cost"]
    m1_v3_reclaims = v3_results[1.0].get("diagnostics", {}).get("ttl_reclaim_count", None)
    m1_v31_reclaims = v31_results[1.0].get("diagnostics", {}).get("ttl_reclaim_count", None)

    patch_summary_lines = [
        "# DTLM v3.1 patch summary",
        "",
        f"- M=1.0 guardrail fixed: {'yes' if guardrail_fixed else 'no'}.",
        f"- Low-M regression on 0.1~0.3: {'yes' if low_m_regression else 'no'}.",
        f"- Average rank moved from {v3_avg_rank:.2f} to {avg_rank:.2f} ({avg_rank_direction}).",
        f"- M=1.0 cost: v3={m1_v3_cost:.0f}, v3.1={m1_v31_cost:.0f}, GDSF={m1_gdsf_cost:.0f}.",
        f"- M=1.0 TTL physical reclaims: v3={m1_v3_reclaims}, v3.1={m1_v31_reclaims}.",
        "- v3.1 gain source: the patch only changed low-pressure physical deletion, so any high-M improvement comes from suppressing low-pressure expiry removals rather than changing GDSF eviction.",
        f"- tau saturation issue: still present / unchanged. v3 diagnosis showed no tau_base exists and 80.0% of functions ended at the cold-floor TTL; v3.1 did not change that logic.",
        "",
        "## Per-M comparison",
    ]
    for m_ratio in M_RATIOS:
        patch_summary_lines.append(
            f"- M={m_ratio:.1f}: v3.1 cost={v31_results[m_ratio]['metrics']['total_cold_start_cost']:.0f}, "
            f"v3 cost={v3_results[m_ratio]['metrics']['total_cold_start_cost']:.0f}, "
            f"GDSF cost={gdsf_results[m_ratio]['metrics']['total_cold_start_cost']:.0f}, rank={dtlm_ranks[m_ratio]}"
        )
    write_text(OUT_DIR / "patch_summary.md", "\n".join(patch_summary_lines))


if __name__ == "__main__":
    main()
