import json
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from runner import prepare_data, run_single
from policies.dtlm import DTLM

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "dtlm_v2"
QUICK_DIR = RESULTS_DIR / "quick"
TUNING_DIR = RESULTS_DIR / "tuning"
FULL_DIR = RESULTS_DIR / "full"

POLICIES = [
    "lru",
    "lfu",
    "fixed_ttl_lru",
    "gdsf",
    "iat_adaptive_ttl",
    "adaptive_ttl_lru",
    "ttlmin_extnd",
    "dtlm",
]
DISPLAY_NAMES = {
    "lru": "LRU",
    "lfu": "LFU",
    "fixed_ttl_lru": "Fixed-TTL+LRU",
    "gdsf": "GDSF",
    "iat_adaptive_ttl": "IAT-Adaptive TTL (Adm.)",
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "ttlmin_extnd": "TTLmin_extnd",
    "dtlm": "DTLM v2",
}
QUICK_M_RATIOS = [0.2, 0.3, 0.5]
SCAN_K_TAU = [2.0, 3.0, 5.0]
SCAN_P_HIGH = [0.75, 0.85, 0.95]
FULL_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SEED = 42
WARN_THRESHOLD_SECONDS = 300.0


def format_float(value):
    return f"{value:.2f}" if value < 10 else f"{value:.1f}"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def metric(result, key):
    return result["metrics"][key]


def cost(result):
    return metric(result, "total_cold_start_cost")


def csr(result):
    return metric(result, "cold_start_rate")


def mem_util(result):
    return metric(result, "avg_memory_utilization")


def rank_map(results_by_policy):
    ordered = sorted(results_by_policy.items(), key=lambda item: cost(item[1]))
    return {policy: idx + 1 for idx, (policy, _) in enumerate(ordered)}


def tau_stats(policy):
    values = [policy._tau_base(fid) for fid in policy.ema_iat]
    if not values:
        return {"min_ms": 0.0, "median_ms": 0.0, "max_ms": 0.0}
    arr = np.array(values, dtype=float)
    return {
        "min_ms": float(arr.min()),
        "median_ms": float(np.median(arr)),
        "max_ms": float(arr.max()),
    }


def run_dtlm(data, m_ratio, output_path, policy_kwargs=None, warmup_days=2):
    result = run_single(
        data,
        "dtlm",
        m_ratio,
        policy_kwargs=policy_kwargs,
        output_path=str(output_path),
        warmup_days=warmup_days,
        warn_threshold_seconds=WARN_THRESHOLD_SECONDS,
    )

    policy = DTLM(
        result["M_MB"],
        {fid: info.copy() for fid, info in data["functions_info"].items()},
        sim_start_ms=data["day_offset_ms"],
        **(policy_kwargs or {}),
    )
    from engine import simulate
    simulate(policy, data["stream"], warmup_end_ms=data["day_offset_ms"] + warmup_days * 24 * 60 * 60 * 1000)
    extra = {
        "ttl_expire_count": policy.ttl_expire_count,
        "eviction_count": policy.eviction_count,
        "ttl_skip_high_pressure_count": policy.ttl_skip_high_pressure_count,
        "tau_base_stats": tau_stats(policy),
    }
    result["dtlm_v2_debug"] = extra
    save_json(output_path, result)
    return result


def run_step1():
    start = time.time()
    QUICK_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=(1, 3), working_set_days=(3, 3))
    rows = []
    gdsf_refs = {}
    for m_ratio in QUICK_M_RATIOS:
        gdsf_path = QUICK_DIR / f"gdsf_{m_ratio:.1f}.json"
        if gdsf_path.exists():
            gdsf_refs[m_ratio] = load_json(gdsf_path)
        else:
            gdsf_refs[m_ratio] = run_single(data, "gdsf", m_ratio, output_path=str(gdsf_path), warmup_days=2)

        path = QUICK_DIR / f"dtlm_default_{m_ratio:.1f}.json"
        result = run_dtlm(data, m_ratio, path, policy_kwargs=None, warmup_days=2)
        debug = result["dtlm_v2_debug"]
        rows.append([
            f"{m_ratio:.1f}",
            f"{csr(result) * 100:.2f}%",
            f"{cost(result):.0f}",
            f"{debug['ttl_expire_count']}",
            f"{debug['eviction_count']}",
            f"{debug['tau_base_stats']['min_ms']:.0f}",
            f"{debug['tau_base_stats']['median_ms']:.0f}",
            f"{debug['tau_base_stats']['max_ms']:.0f}",
        ])
        print(
            f"M_ratio={m_ratio:.1f}: CSR={csr(result) * 100:.2f}% Cost={cost(result):.0f} "
            f"TTL={debug['ttl_expire_count']} Evict={debug['eviction_count']} "
            f"tau=[{debug['tau_base_stats']['min_ms']:.0f}, {debug['tau_base_stats']['median_ms']:.0f}, {debug['tau_base_stats']['max_ms']:.0f}]"
        )

    report = "\n".join([
        "=== Step 2 快速验证 ===",
        f"总运行时间：{time.time() - start:.1f}s",
        "| M_ratio | CSR | Total Cost | TTL Reclaims | Evictions | tau_min | tau_median | tau_max |",
        "|---|---|---|---|---|---|---|---|",
        *["| " + " | ".join(row) + " |" for row in rows],
        "",
        f"M_ratio=0.2 对比 GDSF：DTLM CSR={csr(load_json(QUICK_DIR / 'dtlm_default_0.2.json')) * 100:.2f}% vs GDSF CSR={csr(gdsf_refs[0.2]) * 100:.2f}%",
    ])
    print(report)
    return report


def run_step3():
    start = time.time()
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=(1, 3), working_set_days=(3, 3))
    combo_rank_sums = {}
    combo_cost_sums = {}
    best_by_ratio = {}

    total = len(QUICK_M_RATIOS) * len(SCAN_K_TAU) * len(SCAN_P_HIGH)
    index = 0
    for m_ratio in QUICK_M_RATIOS:
        per_ratio = {}
        for k_tau in SCAN_K_TAU:
            for p_high in SCAN_P_HIGH:
                path = TUNING_DIR / f"dtlm_k{k_tau:.1f}_p{p_high:.2f}_m{m_ratio:.1f}.json"
                if path.exists():
                    result = load_json(path)
                else:
                    result = run_dtlm(data, m_ratio, path, policy_kwargs={"k_tau": k_tau, "p_high": p_high}, warmup_days=2)
                index += 1
                print(f"[{index}/{total}] DTLM v2 k_tau={k_tau:.1f} p_high={p_high:.2f} M={m_ratio:.1f} done: CSR={csr(result) * 100:.2f}% Cost={cost(result):.0f}")
                per_ratio[(k_tau, p_high)] = result
        ordered = sorted(per_ratio.items(), key=lambda item: cost(item[1]))
        best_by_ratio[m_ratio] = ordered[:3]
        for rank, ((k_tau, p_high), result) in enumerate(ordered, start=1):
            combo_rank_sums[(k_tau, p_high)] = combo_rank_sums.get((k_tau, p_high), 0) + rank
            combo_cost_sums[(k_tau, p_high)] = combo_cost_sums.get((k_tau, p_high), 0.0) + cost(result)

    best_combo = min(combo_rank_sums, key=lambda key: (combo_rank_sums[key], combo_cost_sums[key]))
    report_lines = [
        "=== Step 3 完成 ===",
        f"实验数：{total}",
        f"总运行时间：{time.time() - start:.1f}s",
        f"最优参数：k_tau={best_combo[0]:.1f}, p_high={best_combo[1]:.2f}",
        "",
        "每个 M_ratio 的 top 3：",
    ]
    for m_ratio in QUICK_M_RATIOS:
        report_lines.append(f"M_ratio={m_ratio:.1f}:")
        report_lines.append("| k_tau | p_high | CSR | Total Cost |")
        report_lines.append("|---|---|---|---|")
        for (k_tau, p_high), result in best_by_ratio[m_ratio]:
            report_lines.append(f"| {k_tau:.1f} | {p_high:.2f} | {csr(result) * 100:.2f}% | {cost(result):.0f} |")
    report = "\n".join(report_lines)
    print(report)
    return best_combo, report


def run_step4(best_combo):
    start = time.time()
    FULL_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=(3, 12), working_set_days=(5, 12))
    dtlm_kwargs = {"k_tau": best_combo[0], "p_high": best_combo[1]}
    results = {}
    total = len(FULL_M_RATIOS) * len(POLICIES)
    index = 0
    for m_ratio in FULL_M_RATIOS:
        for policy in POLICIES:
            path = FULL_DIR / f"{policy}_{m_ratio:.1f}.json"
            if path.exists():
                result = load_json(path)
            else:
                kwargs = dtlm_kwargs if policy == "dtlm" else None
                result = run_single(data, policy, m_ratio, policy_kwargs=kwargs, output_path=str(path), warmup_days=2)
            results[(policy, m_ratio)] = result
            index += 1
            print(f"[{index}/{total}] {DISPLAY_NAMES[policy]} M={m_ratio:.1f} done: CSR={csr(result) * 100:.2f}% Cost={cost(result):.0f}")

    rows = []
    dtlm_ranks = []
    for m_ratio in FULL_M_RATIOS:
        rank = rank_map({policy: results[(policy, m_ratio)] for policy in POLICIES})
        dtlm_ranks.append((m_ratio, rank["dtlm"]))
        for policy in POLICIES:
            result = results[(policy, m_ratio)]
            rows.append({
                "M_ratio": m_ratio,
                "policy": DISPLAY_NAMES[policy],
                "CSR": csr(result),
                "Total Cost": cost(result),
                "Avg Mem Util": mem_util(result),
            })

    df = pd.DataFrame(rows)
    df.to_csv(FULL_DIR / "dtlm_v2_results_table.csv", index=False)

    plt.figure(figsize=(9, 5))
    for policy in POLICIES:
        subset = df[df["policy"] == DISPLAY_NAMES[policy]].sort_values("M_ratio")
        linestyle = "--" if policy == "iat_adaptive_ttl" else "-"
        plt.plot(subset["M_ratio"], subset["Total Cost"], marker="o", linestyle=linestyle, label=DISPLAY_NAMES[policy])
    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(FULL_DIR / "dtlm_v2_cost_vs_M.png", dpi=200)
    plt.close()

    avg_rank = float(np.mean([rank for _, rank in dtlm_ranks]))
    gdsf_ok = all(csr(results[("dtlm", m)]) <= 1.5 * csr(results[("gdsf", m)]) for m in [0.1, 0.2])
    mid_ok = all(cost(results[("dtlm", m)]) <= cost(results[("gdsf", m)]) for m in [0.3, 0.5])
    high_ok = all(cost(results[("dtlm", m)]) < cost(results[("adaptive_ttl_lru", m)]) for m in [0.7, 1.0])

    report_lines = [
        "=== Step 4 完成 ===",
        f"总运行时间：{time.time() - start:.1f}s",
        "结果表：results/dtlm_v2/full/dtlm_v2_results_table.csv",
        "Cost vs M 图：results/dtlm_v2/full/dtlm_v2_cost_vs_M.png",
        "",
        "DTLM v2 每个 M_ratio 的排名：",
        *[f"- M_ratio={m:.1f}: rank {rank}" for m, rank in dtlm_ranks],
        f"- 平均 rank: {avg_rank:.2f}",
        "",
        "成功标准检查：",
        f"- M_ratio=0.1–0.2 时 CSR 不超过 GDSF 的 1.5 倍：{'通过' if gdsf_ok else '不通过'}",
        f"- M_ratio=0.3–0.5 时 total cost <= GDSF：{'通过' if mid_ok else '不通过'}",
        f"- M_ratio=0.7–1.0 时 total cost < Adaptive-TTL+LRU：{'通过' if high_ok else '不通过'}",
        f"- 6 个 M_ratio 平均 rank <= 3：{'通过' if avg_rank <= 3.0 else '不通过'}",
    ]
    report = "\n".join(report_lines)
    print(report)
    return report


def main():
    quick_report = run_step1()
    best_combo, tuning_report = run_step3()
    full_report = run_step4(best_combo)
    summary = RESULTS_DIR / "summary.txt"
    summary.write_text("\n\n".join([quick_report, tuning_report, full_report]), encoding="utf-8")
    print(f"Saved summary: {summary}")


if __name__ == "__main__":
    main()
