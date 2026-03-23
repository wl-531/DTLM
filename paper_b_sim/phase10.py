import argparse
import json
import shutil
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from runner import prepare_data, run_single, run_divergence_pair

RESULTS_DIR = Path(__file__).resolve().parent / "results"
BASELINE_DIR = RESULTS_DIR / "baseline"
SENSITIVITY_DIR = RESULTS_DIR / "sensitivity"
DTLM_TUNING_DIR = RESULTS_DIR / "dtlm_tuning"
DIVERGENCE_DIR = RESULTS_DIR / "divergence"

POLICIES = [
    "lru",
    "lfu",
    "fixed_ttl_lru",
    "gdsf",
    "iat_adaptive_ttl",
    "adaptive_ttl_lru",
    "ttlmin_extnd",
    "c2rd_sr",
    "dtlm",
]
DISPLAY_NAMES = {
    "lru": "LRU",
    "lfu": "LFU",
    "fixed_ttl_lru": "Fixed-TTL+LRU",
    "gdsf": "GDSF",
    "iat_adaptive_ttl": "IAT-Adaptive TTL",
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "ttlmin_extnd": "TTLmin_extnd",
    "c2rd_sr": "C2RD-SR",
    "dtlm": "DTLM",
}
BASELINE_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SENSITIVITY_M_RATIOS = [0.2, 0.3, 0.5]
COLD_START_SCALES = [0.5, 1.0, 2.0]
DTLM_MULTIPLIERS = [1.0, 2.0, 3.0, 5.0]
DTLM_DECAY_TYPES = ["linear", "exponential", "step"]
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2
SEED = 42
WARN_THRESHOLD_SECONDS = 300.0

# --- DTLM v3.1 paper-frozen parameters ---
_DTLM_V31_FALLBACK_TAUS = {
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
}


def _load_dtlm_v31_kwargs():
    """Load DTLM v3.1 kwargs: tau from selected_params.json, rest hardcoded."""
    taus = dict(_DTLM_V31_FALLBACK_TAUS)
    params_path = RESULTS_DIR / "dtlm_v3" / "selected_params.json"
    if params_path.exists():
        with open(params_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
            if key in saved:
                taus[key] = saved[key]
        print(f"[phase10] Loaded tau from {params_path}: {taus}")
    else:
        print(f"[phase10] selected_params.json not found, using fallback: {taus}")
    return {
        "physical_delete_requires_pressure": True,
        "p_deactivate": 0.95,
        "hot_threshold": 10,
        "warm_threshold": 1,
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
        **taus,
    }


DTLM_V31_KWARGS = _load_dtlm_v31_kwargs()


def _policy_kwargs_for(policy_name):
    """Return policy_kwargs for a given policy, or None."""
    if policy_name == "dtlm":
        return dict(DTLM_V31_KWARGS)
    return None


def format_float(value):
    return f"{value:.1f}"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_load(path):
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def baseline_path(policy, m_ratio):
    return BASELINE_DIR / f"{policy}_{format_float(m_ratio)}.json"


def sensitivity_path(policy, m_ratio, scale):
    return SENSITIVITY_DIR / f"{policy}_{format_float(m_ratio)}_{format_float(scale)}.json"


def dtlm_tuning_path(m_ratio, multiplier, decay_type):
    return DTLM_TUNING_DIR / f"{format_float(m_ratio)}_{format_float(multiplier)}_{decay_type}.json"


def metric(result, key):
    return result["metrics"][key]


def cost(result):
    return metric(result, "total_cold_start_cost")


def csr(result):
    return metric(result, "cold_start_rate")


def mem_util(result):
    return metric(result, "avg_memory_utilization")


def runtime_seconds(result):
    return metric(result, "runtime_seconds")


def ratio_label_list(ratios):
    return ", ".join(format_float(r) for r in ratios) if ratios else "none"


def render_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def rank_policies(results_by_policy):
    ordered = sorted(results_by_policy.items(), key=lambda item: cost(item[1]))
    return {policy: idx + 1 for idx, (policy, _) in enumerate(ordered)}


def print_progress(index, total, label, result):
    print(
        f"[{index}/{total}] {label} done: "
        f"CSR={csr(result) * 100:.2f}% Cost={cost(result):.0f} Time={runtime_seconds(result):.1f}s"
    )


def run_step1():
    start = time.time()
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
    total = len(POLICIES) * len(BASELINE_M_RATIOS)
    completed = 0
    failed = 0
    results = {}

    for m_ratio in BASELINE_M_RATIOS:
        for policy in POLICIES:
            path = baseline_path(policy, m_ratio)
            label = f"{DISPLAY_NAMES[policy]} M={format_float(m_ratio)}"
            result = safe_load(path)
            if result is None:
                try:
                    result = run_single(
                        data,
                        policy,
                        m_ratio,
                        cold_start_scale=1.0,
                        output_path=str(path),
                        warmup_days=WARMUP_DAYS,
                        warn_threshold_seconds=WARN_THRESHOLD_SECONDS,
                        policy_kwargs=_policy_kwargs_for(policy),
                    )
                except Exception as exc:
                    failed += 1
                    completed += 1
                    print(f"[{completed}/{total}] {label} failed: {exc}")
                    continue
            completed += 1
            results[(policy, m_ratio)] = result
            print_progress(completed, total, label, result)

    grouped_lines = []
    for m_ratio in BASELINE_M_RATIOS:
        grouped_lines.append(f"\nM_ratio={format_float(m_ratio)}:")
        group_results = [(policy, results[(policy, m_ratio)]) for policy in POLICIES if (policy, m_ratio) in results]
        group_results.sort(key=lambda item: cost(item[1]))
        rows = [[DISPLAY_NAMES[policy], f"{csr(result) * 100:.2f}%", f"{cost(result):.0f}", f"{mem_util(result) * 100:.2f}%"] for policy, result in group_results]
        grouped_lines.append(render_table(["策略", "CSR", "Total Cost", "Avg Mem Util"], rows))

    better_than_gdsf = []
    better_than_ttlmin = []
    better_than_adaptive = []
    for m_ratio in BASELINE_M_RATIOS:
        if ("dtlm", m_ratio) not in results:
            continue
        dtlm_result = results[("dtlm", m_ratio)]
        if cost(dtlm_result) < cost(results[("gdsf", m_ratio)]):
            better_than_gdsf.append(m_ratio)
        if cost(dtlm_result) < cost(results[("ttlmin_extnd", m_ratio)]):
            better_than_ttlmin.append(m_ratio)
        if cost(dtlm_result) < cost(results[("adaptive_ttl_lru", m_ratio)]):
            better_than_adaptive.append(m_ratio)

    report = "\n".join([
        "=== Step 1 完成 ===",
        f"总实验数：{total}",
        f"成功：{len(results)}",
        f"失败：{failed}",
        f"总运行时间：{time.time() - start:.1f}s",
        "",
        "汇总表（按 M_ratio 分组，每组内按 Total Cost 排序）：",
        *grouped_lines,
        "",
        "关键观察：",
        f"- DTLM 在哪些 M_ratio 下优于 GDSF？{ratio_label_list(better_than_gdsf)}",
        f"- DTLM 在哪些 M_ratio 下优于 TTLmin_extnd？{ratio_label_list(better_than_ttlmin)}",
        f"- DTLM 在所有 M_ratio 下是否优于 Adaptive-TTL+LRU？{'yes' if len(better_than_adaptive) == len(BASELINE_M_RATIOS) else 'no'}",
    ])
    print(report)
    return report


def run_step2():
    start = time.time()
    SENSITIVITY_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
    total = len(POLICIES) * len(SENSITIVITY_M_RATIOS) * len(COLD_START_SCALES)
    completed = 0
    new_runs = 0
    results = {}

    for m_ratio in SENSITIVITY_M_RATIOS:
        for scale in COLD_START_SCALES:
            for policy in POLICIES:
                target = sensitivity_path(policy, m_ratio, scale)
                label = f"{DISPLAY_NAMES[policy]} M={format_float(m_ratio)} scale={format_float(scale)}"
                result = safe_load(target)
                if result is None:
                    if scale == 1.0:
                        source = baseline_path(policy, m_ratio)
                        if safe_load(source) is None:
                            raise FileNotFoundError(f"Missing baseline result for {policy} {m_ratio}")
                        shutil.copyfile(source, target)
                        result = load_json(target)
                    else:
                        result = run_single(
                            data,
                            policy,
                            m_ratio,
                            cold_start_scale=scale,
                            output_path=str(target),
                            warmup_days=WARMUP_DAYS,
                            warn_threshold_seconds=WARN_THRESHOLD_SECONDS,
                            policy_kwargs=_policy_kwargs_for(policy),
                        )
                        new_runs += 1
                completed += 1
                results[(policy, m_ratio, scale)] = result
                print_progress(completed, total, label, result)

    tables = []
    for m_ratio in SENSITIVITY_M_RATIOS:
        tables.append(f"\nM_ratio={format_float(m_ratio)}:")
        rows = []
        for policy in POLICIES:
            triplet = [results[(policy, m_ratio, scale)] for scale in COLD_START_SCALES]
            rows.append([
                DISPLAY_NAMES[policy],
                f"{csr(triplet[0]) * 100:.2f}%",
                f"{csr(triplet[1]) * 100:.2f}%",
                f"{csr(triplet[2]) * 100:.2f}%",
                f"{cost(triplet[0]):.0f}",
                f"{cost(triplet[1]):.0f}",
                f"{cost(triplet[2]):.0f}",
            ])
        tables.append(render_table(["策略", "CSR(×0.5)", "CSR(×1.0)", "CSR(×2.0)", "Cost(×0.5)", "Cost(×1.0)", "Cost(×2.0)"], rows))

    sensitivity_scores = {}
    for policy in POLICIES:
        ratios = []
        for m_ratio in SENSITIVITY_M_RATIOS:
            c_low = cost(results[(policy, m_ratio, 0.5)])
            c_high = cost(results[(policy, m_ratio, 2.0)])
            ratios.append(c_high / c_low if c_low else float("inf"))
        sensitivity_scores[policy] = float(np.mean(ratios))
    most_sensitive = max(sensitivity_scores.items(), key=lambda item: item[1])[0]

    dtlm_ranks = []
    for m_ratio in SENSITIVITY_M_RATIOS:
        for scale in COLD_START_SCALES:
            ranks = rank_policies({policy: results[(policy, m_ratio, scale)] for policy in POLICIES})
            dtlm_ranks.append(ranks["dtlm"])
    stable = max(dtlm_ranks) == min(dtlm_ranks)

    report = "\n".join([
        "=== Step 2 完成 ===",
        f"新跑实验数：{new_runs}",
        f"总运行时间：{time.time() - start:.1f}s",
        "",
        "敏感性表：",
        *tables,
        "",
        "关键观察：",
        f"- 哪个策略对 c_i 缩放最敏感？{DISPLAY_NAMES[most_sensitive]}",
        f"- DTLM 的排名是否在不同 scale 下稳定？{'stable' if stable else f'no, ranks={dtlm_ranks}'}",
    ])
    print(report)
    return report


def run_step3():
    """DEPRECATED: Step 3 tuning data is invalid (quantile_multiplier/decay_type were
    silently absorbed by **_ignored_kwargs in dtlm.py, so all combos ran identical v3.0).
    Do not run. Existing results in dtlm_tuning/ are废弃."""
    print("=" * 60)
    print("SKIPPED: run_step3() is deprecated.")
    print("Reason: quantile_multiplier / decay_type were caught by **_ignored_kwargs")
    print("in dtlm.py, making all tuning combos identical. Data is invalid.")
    print("See paper_b_framework_v3_5.md section 12.3 for details.")
    print("=" * 60)
    return "Step 3 SKIPPED (deprecated)"


def load_phase10_results():
    payloads = []
    for directory in [BASELINE_DIR, SENSITIVITY_DIR, DTLM_TUNING_DIR]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            payloads.append((directory.name, load_json(path)))
    return payloads


def summary_policy_label(group, payload):
    if group == "dtlm_tuning":
        kwargs = payload.get("policy_kwargs", {})
        return f"dtlm[q={format_float(kwargs.get('quantile_multiplier', 2.0))},{kwargs.get('decay_type', 'linear')}]"
    return payload["policy"]


def run_step4():
    start = time.time()
    payloads = load_phase10_results()

    summary_rows = []
    baseline_rows = []
    sensitivity_rows = []
    tuning_rows = []
    for group, payload in payloads:
        summary_rows.append({
            "policy": summary_policy_label(group, payload),
            "M_ratio": payload["M_ratio"],
            "cold_start_scale": payload["cold_start_scale"],
            "CSR": payload["metrics"]["cold_start_rate"],
            "total_cost": payload["metrics"]["total_cold_start_cost"],
            "avg_mem_util": payload["metrics"]["avg_memory_utilization"],
            "runtime_s": payload["metrics"]["runtime_seconds"],
        })
        if group == "baseline":
            baseline_rows.append(payload)
        elif group == "sensitivity":
            sensitivity_rows.append(payload)
        elif group == "dtlm_tuning":
            tuning_rows.append(payload)

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(RESULTS_DIR / "summary_table.csv", index=False)

    baseline_df = pd.DataFrame([{"M_ratio": p["M_ratio"], "policy": p["policy"], "total_cost": p["metrics"]["total_cold_start_cost"]} for p in baseline_rows])
    baseline_df.pivot(index="M_ratio", columns="policy", values="total_cost").sort_index().to_csv(RESULTS_DIR / "cost_vs_M.csv")

    plt.figure(figsize=(9, 5))
    for policy in POLICIES:
        subset = baseline_df[baseline_df["policy"] == policy].sort_values("M_ratio")
        linestyle = "--" if policy == "iat_adaptive_ttl" else "-"
        plt.plot(subset["M_ratio"], subset["total_cost"], marker="o", linestyle=linestyle, label=DISPLAY_NAMES[policy])
    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(RESULTS_DIR / "cost_vs_M_plot.png", dpi=200)
    plt.close()

    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharey=True)
    sensitivity_df = pd.DataFrame([{"policy": p["policy"], "M_ratio": p["M_ratio"], "scale": p["cold_start_scale"], "total_cost": p["metrics"]["total_cold_start_cost"]} for p in sensitivity_rows])
    for ax, m_ratio in zip(axes, SENSITIVITY_M_RATIOS):
        subset = sensitivity_df[sensitivity_df["M_ratio"] == m_ratio]
        for policy in POLICIES:
            policy_df = subset[subset["policy"] == policy].sort_values("scale")
            ax.plot(policy_df["scale"], policy_df["total_cost"], marker="o", label=DISPLAY_NAMES[policy])
        ax.set_title(f"M_ratio={format_float(m_ratio)}")
        ax.set_xlabel("cold_start_scale")
        ax.set_ylabel("Total Cost")
    axes[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=7)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "sensitivity_plot.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    if tuning_rows:
        fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True, constrained_layout=True)
        tuning_df = pd.DataFrame([{"M_ratio": p["M_ratio"], "multiplier": p.get("policy_kwargs", {}).get("quantile_multiplier", 2.0), "decay_type": p.get("policy_kwargs", {}).get("decay_type", "linear"), "total_cost": p["metrics"]["total_cold_start_cost"]} for p in tuning_rows])
        for ax, m_ratio in zip(axes, SENSITIVITY_M_RATIOS):
            subset = tuning_df[tuning_df["M_ratio"] == m_ratio]
            pivot = subset.pivot(index="multiplier", columns="decay_type", values="total_cost").reindex(index=DTLM_MULTIPLIERS, columns=DTLM_DECAY_TYPES)
            image = ax.imshow(pivot.values, aspect="auto", cmap="viridis_r")
            ax.set_title(f"M_ratio={format_float(m_ratio)}")
            ax.set_xticks(range(len(DTLM_DECAY_TYPES)), DTLM_DECAY_TYPES)
            ax.set_yticks(range(len(DTLM_MULTIPLIERS)), [format_float(v) for v in DTLM_MULTIPLIERS])
            best_idx = np.unravel_index(np.argmin(pivot.values), pivot.values.shape)
            ax.add_patch(plt.Rectangle((best_idx[1] - 0.5, best_idx[0] - 0.5), 1, 1, fill=False, edgecolor="white", linewidth=2))
            for i in range(len(DTLM_MULTIPLIERS)):
                for j in range(len(DTLM_DECAY_TYPES)):
                    ax.text(j, i, f"{pivot.values[i, j]:.0f}", ha="center", va="center", color="white", fontsize=7)
        fig.colorbar(image, ax=axes.ravel().tolist(), shrink=0.8, label="Total Cost")
        fig.savefig(RESULTS_DIR / "dtlm_tuning_heatmap.png", dpi=200, bbox_inches="tight")
        plt.close(fig)
    else:
        print("[step4] Skipping tuning heatmap (no tuning data / step3 deprecated)")

    baseline_lookup = {(p["policy"], p["M_ratio"]): p for p in baseline_rows}
    best_dtlm_ranges = []
    gdsf_advantages = []
    ttl_advantages = []
    for m_ratio in BASELINE_M_RATIOS:
        dtlm_result = baseline_lookup.get(("dtlm", m_ratio))
        if dtlm_result is None:
            continue
        best_cost = min(cost(baseline_lookup[(policy, m_ratio)]) for policy in POLICIES)
        if cost(dtlm_result) == best_cost:
            best_dtlm_ranges.append(m_ratio)
        gdsf_gap = (cost(baseline_lookup[("gdsf", m_ratio)]) - cost(dtlm_result)) / cost(baseline_lookup[("gdsf", m_ratio)]) * 100.0
        ttl_gap = (cost(baseline_lookup[("ttlmin_extnd", m_ratio)]) - cost(dtlm_result)) / cost(baseline_lookup[("ttlmin_extnd", m_ratio)]) * 100.0
        gdsf_advantages.append((gdsf_gap, m_ratio))
        ttl_advantages.append((ttl_gap, m_ratio))

    best_gdsf_gap, best_gdsf_ratio = max(gdsf_advantages)
    best_ttl_gap, best_ttl_ratio = max(ttl_advantages)
    if len(best_dtlm_ranges) >= 2:
        narrative = "成立"
    elif len(best_dtlm_ranges) == 0:
        narrative = "不成立"
    else:
        narrative = "部分成立"

    report = "\n".join([
        "=== Step 4 完成 ===",
        f"产出文件：",
        f"- results/summary_table.csv（{len(summary_df)} 行）",
        "- results/cost_vs_M.csv",
        "- results/cost_vs_M_plot.png",
        "- results/sensitivity_plot.png",
        "- results/dtlm_tuning_heatmap.png",
        "",
        "Cost vs M curve 关键结论：",
        f"- DTLM 在 M_ratio={ratio_label_list(best_dtlm_ranges)} 区间表现最好",
        f"- DTLM 相比 GDSF 的最大优势出现在 M_ratio={format_float(best_gdsf_ratio)}，降幅 {best_gdsf_gap:.1f}%",
        f"- DTLM 相比 TTLmin_extnd 的最大优势出现在 M_ratio={format_float(best_ttl_ratio)}，降幅 {best_ttl_gap:.1f}%",
        "",
        f"最终结论：DTLM 的论文叙事是否成立？{narrative}",
    ])
    print(report)
    return report


def divergence_path(m_ratio, snapshot_interval_sec):
    return DIVERGENCE_DIR / f"dtlm_vs_gdsf_{format_float(m_ratio)}_{int(snapshot_interval_sec)}s.json"


def make_divergence_json_ready(payload):
    snapshots = []
    for row in payload["snapshots"]:
        snapshot = dict(row)
        snapshot["dtlm_warm_set"] = sorted(snapshot["dtlm_warm_set"])
        snapshot["gdsf_warm_set"] = sorted(snapshot["gdsf_warm_set"])
        snapshot["dtlm_only"] = sorted(snapshot["dtlm_only"])
        snapshot["gdsf_only"] = sorted(snapshot["gdsf_only"])
        snapshots.append(snapshot)
    return {
        "snapshots": snapshots,
        "summary": payload["summary"],
    }


def run_step5(m_ratio=0.3, snapshot_interval_sec=60):
    start = time.time()
    DIVERGENCE_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
    result = run_divergence_pair(
        data,
        dtlm_config={
            "M_ratio": m_ratio,
            "warmup_days": WARMUP_DAYS,
            "policy_kwargs": dict(DTLM_V31_KWARGS),
        },
        gdsf_config={
            "M_ratio": m_ratio,
            "warmup_days": WARMUP_DAYS,
        },
        snapshot_interval_sec=snapshot_interval_sec,
    )

    output_path = divergence_path(m_ratio, snapshot_interval_sec)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(make_divergence_json_ready(result), handle, indent=2)
    print(f"SAVED: {output_path}")

    summary = result["summary"]
    report = "\n".join([
        "=== Step 5 完成 ===",
        f"M_ratio={format_float(m_ratio)}, snapshot_interval={int(snapshot_interval_sec)}s",
        f"snapshot_count={len(result['snapshots'])}",
        f"mean_jaccard={summary['mean_jaccard']:.4f}",
        f"divergent_snapshot_ratio={summary['divergent_snapshot_ratio']:.4f}",
        f"mean_interval_delta_cost={summary['mean_interval_delta_cost']:.2f}",
        f"total_delta_cost={summary['total_delta_cost']:.2f}",
        f"总运行时间：{time.time() - start:.1f}s",
    ])
    print(report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["1", "2", "3", "4", "5", "all"], default="all")
    parser.add_argument("--divergence-m-ratio", type=float, default=0.3)
    parser.add_argument("--snapshot-interval-sec", type=int, default=60)
    args = parser.parse_args()

    if args.step in {"1", "all"}:
        run_step1()
    if args.step in {"2", "all"}:
        run_step2()
    if args.step in {"3", "all"}:
        run_step3()
    if args.step in {"4", "all"}:
        run_step4()
    if args.step == "5":
        run_step5(
            m_ratio=args.divergence_m_ratio,
            snapshot_interval_sec=args.snapshot_interval_sec,
        )


if __name__ == "__main__":
    main()
