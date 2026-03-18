import argparse
import ast
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from engine import simulate
from metrics import summary
from runner import POLICY_MAP, prepare_data

ROOT = Path(r"D:\code\paper_b_sim")
RESULTS_DIR = ROOT / "results" / "dtlm_v3"
QUICK_DIR = RESULTS_DIR / "quick"
TUNING_DIR = RESULTS_DIR / "tuning"
FINAL_DIR = RESULTS_DIR / "final"
BASELINE_DIR = ROOT / "results" / "baseline"
STEP3_SELECTION_PATH = RESULTS_DIR / "selected_params.json"

SEED = 42
DAY_MS = 24 * 60 * 60 * 1000
WARMUP_DAYS = 2
STEP2_M_RATIOS = [0.2, 0.3, 0.5]
STEP4_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
STEP3_P_DEACTIVATE = [0.80, 0.85, 0.90, 0.95]
STEP3_TAU_COLD = [30000, 60000, 120000]
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
    "dtlm_v3": "DTLM v3",
}
DEFAULT_DTLM_KWARGS = {
    "p_deactivate": 0.90,
    "hot_threshold": 10,
    "warm_threshold": 1,
    "tau_hot_ms": 600000,
    "tau_warm_ms": 180000,
    "tau_cold_ms": 60000,
    "t_protect_ms": 60000,
    "ttl_scan_interval_ms": 60000,
}


def ensure_dirs():
    for path in [RESULTS_DIR, QUICK_DIR, TUNING_DIR, FINAL_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def format_ratio(value):
    return f"{value:.1f}"


def format_p(value):
    return f"{value:.2f}"


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"SAVED: {path}")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"SAVED: {path}")


def scale_functions_info(functions_info, cold_start_scale):
    if cold_start_scale == 1.0:
        return functions_info
    scaled = {}
    for func_id, info in functions_info.items():
        scaled[func_id] = {
            "m_i": info["m_i"],
            "c_i": info["c_i"] * cold_start_scale,
        }
    return scaled


def collect_dtlm_diagnostics(policy, metrics, final_timestamp):
    counts = {"hot": 0, "warm": 0, "cold": 0}
    for func_id in list(policy.warm_pool):
        counts[policy._classify_function(func_id, final_timestamp)] += 1
    state = policy.get_state()
    return {
        "ttl_reclaim_count": state["ttl_reclaim_count"],
        "eviction_count": state["eviction_count"],
        "ttl_active_scans": state["ttl_active_scans"],
        "ttl_skipped_scans": state["ttl_skipped_scans"],
        "pressure_mean": metrics["avg_memory_utilization"],
        "warm_class_counts": counts,
        "clock": state["clock"],
        "warm_count": state["warm_count"],
    }


def run_policy(data, policy_name, m_ratio, cold_start_scale=1.0, policy_kwargs=None,
               warmup_days=WARMUP_DAYS, output_path=None, result_policy_name=None):
    functions_info = scale_functions_info(data["functions_info"], cold_start_scale)
    policy_kwargs = dict(policy_kwargs or {})
    M = data["ws_mean"] * m_ratio
    policy = POLICY_MAP[policy_name](M, functions_info, **policy_kwargs)
    warmup_end_ms = data["day_offset_ms"] + warmup_days * DAY_MS

    start = time.time()
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    elapsed = time.time() - start
    metrics = summary(results, functions_info, M,
                      policy=policy,
                      memory_unconstrained=(policy_name == "iat_adaptive_ttl"),
                      skip_warmup=True)
    metrics["runtime_seconds"] = round(elapsed, 1)

    payload = {
        "policy": result_policy_name or policy_name,
        "source_policy": policy_name,
        "M_ratio": m_ratio,
        "M_MB": round(M, 1),
        "cold_start_scale": cold_start_scale,
        "seed": data["seed"],
        "days": list(data["days"]),
        "working_set_days": list(data["working_set_days"]),
        "warmup_days": warmup_days,
        "working_set_mean_MB": round(data["ws_mean"], 1),
        "metrics": metrics,
    }
    if policy_kwargs:
        payload["policy_kwargs"] = policy_kwargs
    if policy_name == "dtlm":
        final_timestamp = data["stream"][-1][0] if data["stream"] else 0
        payload["diagnostics"] = collect_dtlm_diagnostics(policy, metrics, final_timestamp)
    if output_path is not None:
        write_json(output_path, payload)
    return payload


def quick_result_path(policy_name, m_ratio):
    return QUICK_DIR / f"{policy_name}_{format_ratio(m_ratio)}.json"


def tuning_result_path(m_ratio, p_deactivate, tau_cold_ms):
    return TUNING_DIR / f"{format_ratio(m_ratio)}_{format_p(p_deactivate)}_{tau_cold_ms}.json"


def final_result_path(m_ratio):
    return FINAL_DIR / f"dtlm_v3_{format_ratio(m_ratio)}.json"


def baseline_result_path(policy_name, m_ratio):
    return BASELINE_DIR / f"{policy_name}_{format_ratio(m_ratio)}.json"


def load_or_run(data, path, policy_name, m_ratio, policy_kwargs=None, result_policy_name=None):
    if path.exists():
        return read_json(path)
    return run_policy(
        data,
        policy_name,
        m_ratio,
        policy_kwargs=policy_kwargs,
        output_path=path,
        result_policy_name=result_policy_name,
    )


def run_step1():
    ensure_dirs()
    start = time.time()
    required_paths = [
        ROOT / "policies" / "dtlm.py",
        ROOT / "policies" / "dtlm_v2_backup.py",
        ROOT / "tests" / "test_regressions.py",
    ]
    for path in required_paths:
        ast.parse(path.read_text(encoding="utf-8-sig"))

    test_cmd = [sys.executable, "-B", "-m", "unittest", "tests.test_regressions"]
    completed = subprocess.run(test_cmd, cwd=ROOT, capture_output=True, text=True)
    syntax_ok = completed.returncode == 0
    line_count = sum(1 for _ in (ROOT / "policies" / "dtlm.py").open(encoding="utf-8-sig"))
    report = "\n".join([
        "=== Step 1 完成 ===",
        "产出文件：policies/dtlm_v2_backup.py, policies/dtlm.py (v3)",
        f"代码行数：约 {line_count}",
        f"编译/语法检查：{'通过' if syntax_ok else '不通过'}",
    ])
    write_text(RESULTS_DIR / "step1_report.txt", report)
    print(report)
    print(f"Step 1 runtime: {time.time() - start:.1f}s")
    return report


def load_quick_data():
    return prepare_data(seed=SEED, days=(1, 3), working_set_days=(3, 3))


def summarize_step2_line(m_ratio, result):
    metrics = result["metrics"]
    diag = result["diagnostics"]
    counts = diag["warm_class_counts"]
    lines = [
        f"M_ratio={format_ratio(m_ratio)}:",
        f"  CSR: {metrics['cold_start_rate'] * 100:.2f}%",
        f"  Total Cost: {metrics['total_cold_start_cost']:.0f}",
        f"  TTL reclaim count: {diag['ttl_reclaim_count']}",
        f"  Eviction count: {diag['eviction_count']}",
        f"  TTL active scans: {diag['ttl_active_scans']}",
        f"  TTL skipped scans: {diag['ttl_skipped_scans']}",
        f"  Pressure (mean over run): {diag['pressure_mean']:.4f}",
        f"  Warm pool 函数分类 (最后时刻): hot={counts['hot']} warm={counts['warm']} cold={counts['cold']}",
    ]
    return "\n".join(lines)


def run_step2():
    ensure_dirs()
    start = time.time()
    data = load_quick_data()
    dtlm_results = {}
    gdsf_results = {}
    for m_ratio in STEP2_M_RATIOS:
        dtlm_results[m_ratio] = load_or_run(
            data,
            quick_result_path("dtlm_v3", m_ratio),
            "dtlm",
            m_ratio,
            policy_kwargs=DEFAULT_DTLM_KWARGS,
            result_policy_name="dtlm_v3",
        )
        gdsf_results[m_ratio] = load_or_run(
            data,
            quick_result_path("gdsf", m_ratio),
            "gdsf",
            m_ratio,
            result_policy_name="gdsf",
        )

    gdsf_gap = (dtlm_results[0.2]["metrics"]["cold_start_rate"] - gdsf_results[0.2]["metrics"]["cold_start_rate"]) * 100
    if dtlm_results[0.2]["metrics"]["cold_start_rate"] - gdsf_results[0.2]["metrics"]["cold_start_rate"] > 0.05:
        raise RuntimeError("M_ratio=0.2 的 CSR 相比 GDSF 超出 5 个百分点，需先排查 GDSF 一致性。")

    checks = []
    checks.append((
        "M_ratio=0.2 skipped scans > active scans",
        dtlm_results[0.2]["diagnostics"]["ttl_skipped_scans"] > dtlm_results[0.2]["diagnostics"]["ttl_active_scans"],
    ))
    checks.append((
        "M_ratio=0.3 CSR <= GDSF",
        dtlm_results[0.3]["metrics"]["cold_start_rate"] <= gdsf_results[0.3]["metrics"]["cold_start_rate"],
    ))
    checks.append((
        "M_ratio=0.5 TTL reclaim count > 0",
        dtlm_results[0.5]["diagnostics"]["ttl_reclaim_count"] > 0,
    ))
    failures = [label for label, ok in checks if not ok]

    report_lines = ["=== Step 2 完成 ==="]
    for m_ratio in STEP2_M_RATIOS:
        report_lines.append(summarize_step2_line(m_ratio, dtlm_results[m_ratio]))
        report_lines.append("")
    report_lines.extend([
        f"M_ratio=0.2: CSR={dtlm_results[0.2]['metrics']['cold_start_rate'] * 100:.2f}%, Cost={dtlm_results[0.2]['metrics']['total_cold_start_cost']:.0f}, TTL reclaims={dtlm_results[0.2]['diagnostics']['ttl_reclaim_count']}, Evictions={dtlm_results[0.2]['diagnostics']['eviction_count']}",
        f"M_ratio=0.3: CSR={dtlm_results[0.3]['metrics']['cold_start_rate'] * 100:.2f}%, Cost={dtlm_results[0.3]['metrics']['total_cold_start_cost']:.0f}, TTL reclaims={dtlm_results[0.3]['diagnostics']['ttl_reclaim_count']}, Evictions={dtlm_results[0.3]['diagnostics']['eviction_count']}",
        f"M_ratio=0.5: CSR={dtlm_results[0.5]['metrics']['cold_start_rate'] * 100:.2f}%, Cost={dtlm_results[0.5]['metrics']['total_cold_start_cost']:.0f}, TTL reclaims={dtlm_results[0.5]['diagnostics']['ttl_reclaim_count']}, Evictions={dtlm_results[0.5]['diagnostics']['eviction_count']}",
        "",
        f"M_ratio=0.2 的 CSR 与 GDSF 的差距：{gdsf_gap:.2f}% （目标 < 5%）",
        f"预期行为检查：{'全部通过' if not failures else ' / '.join(failures)}",
        f"总运行时间：{time.time() - start:.1f}s",
    ])
    report = "\n".join(report_lines)
    write_text(RESULTS_DIR / "step2_report.txt", report)
    print(report)
    return report


def step3_combo_summary(results_by_combo):
    combo_rows = []
    for params, per_ratio in results_by_combo.items():
        ranks = list(per_ratio["ranks"].values())
        avg_rank = sum(ranks) / len(ranks)
        avg_cost = sum(result["metrics"]["total_cold_start_cost"] for result in per_ratio["results"].values()) / len(per_ratio["results"])
        combo_rows.append({
            "p_deactivate": params[0],
            "tau_cold_ms": params[1],
            "avg_rank": avg_rank,
            "avg_cost": avg_cost,
            "results": per_ratio["results"],
        })
    combo_rows.sort(key=lambda row: (row["avg_rank"], row["avg_cost"]))
    return combo_rows


def select_best_combo(combo_rows):
    top3 = combo_rows[:3]
    if top3 and (top3[-1]["avg_rank"] - top3[0]["avg_rank"]) < 0.5:
        return sorted(top3, key=lambda row: (-row["p_deactivate"], row["avg_rank"], row["avg_cost"]))[0], "top 3 平均 rank 差异 < 0.5，按更保守的更高 p_deactivate 选择"
    return combo_rows[0], "平均 rank 最优"


def run_step3():
    ensure_dirs()
    start = time.time()
    data = load_quick_data()
    results = {}
    for m_ratio in STEP2_M_RATIOS:
        for p_deactivate in STEP3_P_DEACTIVATE:
            for tau_cold_ms in STEP3_TAU_COLD:
                kwargs = dict(DEFAULT_DTLM_KWARGS)
                kwargs.update({
                    "p_deactivate": p_deactivate,
                    "tau_cold_ms": tau_cold_ms,
                    "tau_warm_ms": 3 * tau_cold_ms,
                    "tau_hot_ms": 10 * tau_cold_ms,
                })
                path = tuning_result_path(m_ratio, p_deactivate, tau_cold_ms)
                result = load_or_run(
                    data,
                    path,
                    "dtlm",
                    m_ratio,
                    policy_kwargs=kwargs,
                    result_policy_name="dtlm_v3",
                )
                results[(m_ratio, p_deactivate, tau_cold_ms)] = result

    results_by_combo = {}
    for p_deactivate in STEP3_P_DEACTIVATE:
        for tau_cold_ms in STEP3_TAU_COLD:
            params = (p_deactivate, tau_cold_ms)
            results_by_combo[params] = {"results": {}, "ranks": {}}
            for m_ratio in STEP2_M_RATIOS:
                results_by_combo[params]["results"][m_ratio] = results[(m_ratio, p_deactivate, tau_cold_ms)]

    for m_ratio in STEP2_M_RATIOS:
        ordered = sorted(
            [(p, tau, results[(m_ratio, p, tau)]) for p in STEP3_P_DEACTIVATE for tau in STEP3_TAU_COLD],
            key=lambda item: item[2]["metrics"]["total_cold_start_cost"],
        )
        for rank, (p_deactivate, tau_cold_ms, _) in enumerate(ordered, start=1):
            results_by_combo[(p_deactivate, tau_cold_ms)]["ranks"][m_ratio] = rank

    combo_rows = step3_combo_summary(results_by_combo)
    top3 = combo_rows[:3]
    selected, selection_reason = select_best_combo(combo_rows)
    selection_payload = {
        "p_deactivate": selected["p_deactivate"],
        "tau_cold_ms": selected["tau_cold_ms"],
        "tau_warm_ms": 3 * selected["tau_cold_ms"],
        "tau_hot_ms": 10 * selected["tau_cold_ms"],
        "selection_reason": selection_reason,
    }
    write_json(STEP3_SELECTION_PATH, selection_payload)

    lines = [
        "=== Step 3 完成 ===",
        "实验数：36",
        f"总运行时间：{time.time() - start:.1f}s",
        "",
        "Top 3 参数组合（按平均 rank）：",
    ]
    for idx, row in enumerate(top3, start=1):
        m02 = row["results"][0.2]["metrics"]["cold_start_rate"] * 100
        m03 = row["results"][0.3]["metrics"]["cold_start_rate"] * 100
        m05 = row["results"][0.5]["metrics"]["cold_start_rate"] * 100
        lines.append(
            f"{idx}. p_deactivate={row['p_deactivate']:.2f}, tau_cold={row['tau_cold_ms']}ms → avg rank={row['avg_rank']:.2f}, "
            f"M=0.2 CSR={m02:.2f}, M=0.3 CSR={m03:.2f}, M=0.5 CSR={m05:.2f}"
        )
    lines.extend([
        "",
        f"最终选定参数：p_deactivate={selected['p_deactivate']:.2f}, tau_cold={selected['tau_cold_ms']}ms",
        f"理由：{selection_reason}",
    ])
    report = "\n".join(lines)
    write_text(RESULTS_DIR / "step3_report.txt", report)
    print(report)
    return report


def load_selected_params():
    if STEP3_SELECTION_PATH.exists():
        return read_json(STEP3_SELECTION_PATH)

    results_by_combo = {}
    for p_deactivate in STEP3_P_DEACTIVATE:
        for tau_cold_ms in STEP3_TAU_COLD:
            params = (p_deactivate, tau_cold_ms)
            results_by_combo[params] = {"results": {}, "ranks": {}}
            for m_ratio in STEP2_M_RATIOS:
                path = tuning_result_path(m_ratio, p_deactivate, tau_cold_ms)
                if not path.exists():
                    raise FileNotFoundError(f"Missing tuning result: {path}")
                results_by_combo[params]["results"][m_ratio] = read_json(path)

    for m_ratio in STEP2_M_RATIOS:
        ordered = sorted(
            [(p, tau, results_by_combo[(p, tau)]["results"][m_ratio]) for p in STEP3_P_DEACTIVATE for tau in STEP3_TAU_COLD],
            key=lambda item: item[2]["metrics"]["total_cold_start_cost"],
        )
        for rank, (p_deactivate, tau_cold_ms, _) in enumerate(ordered, start=1):
            results_by_combo[(p_deactivate, tau_cold_ms)]["ranks"][m_ratio] = rank

    combo_rows = step3_combo_summary(results_by_combo)
    selected, selection_reason = select_best_combo(combo_rows)
    selection_payload = {
        "p_deactivate": selected["p_deactivate"],
        "tau_cold_ms": selected["tau_cold_ms"],
        "tau_warm_ms": 3 * selected["tau_cold_ms"],
        "tau_hot_ms": 10 * selected["tau_cold_ms"],
        "selection_reason": selection_reason,
    }
    write_json(STEP3_SELECTION_PATH, selection_payload)
    return selection_payload


def load_baseline_result(policy_name, m_ratio):
    path = baseline_result_path(policy_name, m_ratio)
    if not path.exists():
        raise FileNotFoundError(f"Missing baseline result: {path}")
    return read_json(path)


def write_results_table(rows):
    path = FINAL_DIR / "results_table.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"SAVED: {path}")


def plot_cost_vs_m(rows):
    path = FINAL_DIR / "cost_vs_M.png"
    grouped = {}
    for row in rows:
        grouped.setdefault(row["policy"], []).append(row)

    plt.figure(figsize=(9, 5))
    order = BASELINE_POLICIES + ["dtlm_v3"]
    for policy in order:
        policy_rows = sorted(grouped[policy], key=lambda item: item["M_ratio"])
        x = [row["M_ratio"] for row in policy_rows]
        y = [row["total_cost"] for row in policy_rows]
        linestyle = "--" if policy == "iat_adaptive_ttl" else "-"
        linewidth = 2.8 if policy == "dtlm_v3" else 1.8
        marker = "o" if policy == "dtlm_v3" else None
        plt.plot(x, y, linestyle=linestyle, linewidth=linewidth, marker=marker, label=DISPLAY_NAMES[policy])
    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()
    print(f"SAVED: {path}")


def build_ranking_table(rows):
    path = FINAL_DIR / "ranking_table.txt"
    grouped = {}
    for row in rows:
        grouped.setdefault(row["M_ratio"], []).append(row)

    dtlm_ranks = {}
    lines = []
    for m_ratio in STEP4_M_RATIOS:
        lines.append(f"M_ratio={format_ratio(m_ratio)}")
        ordered = sorted(grouped[m_ratio], key=lambda item: item["total_cost"])
        for rank, row in enumerate(ordered, start=1):
            lines.append(f"  {rank}. {row['display_name']}: cost={row['total_cost']:.0f}, CSR={row['csr'] * 100:.2f}%")
            if row["policy"] == "dtlm_v3":
                dtlm_ranks[m_ratio] = rank
        lines.append("")
    avg_rank = sum(dtlm_ranks[m_ratio] for m_ratio in STEP4_M_RATIOS) / len(STEP4_M_RATIOS)
    lines.append("DTLM v3 排名汇总")
    for m_ratio in STEP4_M_RATIOS:
        lines.append(f"- M_ratio={format_ratio(m_ratio)}: rank {dtlm_ranks[m_ratio]}")
    lines.append(f"- 平均 rank: {avg_rank:.2f}")
    text = "\n".join(lines)
    write_text(path, text)
    return dtlm_ranks, avg_rank


def run_step4():
    ensure_dirs()
    start = time.time()
    selected = load_selected_params()
    data = prepare_data(seed=SEED, days=(3, 12), working_set_days=(5, 12))
    dtlm_kwargs = dict(DEFAULT_DTLM_KWARGS)
    dtlm_kwargs.update({
        "p_deactivate": selected["p_deactivate"],
        "tau_cold_ms": selected["tau_cold_ms"],
        "tau_warm_ms": selected["tau_warm_ms"],
        "tau_hot_ms": selected["tau_hot_ms"],
    })

    rows = []
    dtlm_results = {}
    gdsf_results = {}
    adaptive_results = {}
    for m_ratio in STEP4_M_RATIOS:
        for policy_name in BASELINE_POLICIES:
            result = load_baseline_result(policy_name, m_ratio)
            rows.append({
                "policy": policy_name,
                "display_name": DISPLAY_NAMES[policy_name],
                "M_ratio": m_ratio,
                "csr": result["metrics"]["cold_start_rate"],
                "total_cost": result["metrics"]["total_cold_start_cost"],
                "avg_mem_util": result["metrics"]["avg_memory_utilization"],
                "runtime_seconds": result["metrics"].get("runtime_seconds", 0.0),
                "result_path": str(baseline_result_path(policy_name, m_ratio)),
            })
            if policy_name == "gdsf":
                gdsf_results[m_ratio] = result
            if policy_name == "adaptive_ttl_lru":
                adaptive_results[m_ratio] = result

        dtlm_result = load_or_run(
            data,
            final_result_path(m_ratio),
            "dtlm",
            m_ratio,
            policy_kwargs=dtlm_kwargs,
            result_policy_name="dtlm_v3",
        )
        dtlm_results[m_ratio] = dtlm_result
        rows.append({
            "policy": "dtlm_v3",
            "display_name": DISPLAY_NAMES["dtlm_v3"],
            "M_ratio": m_ratio,
            "csr": dtlm_result["metrics"]["cold_start_rate"],
            "total_cost": dtlm_result["metrics"]["total_cold_start_cost"],
            "avg_mem_util": dtlm_result["metrics"]["avg_memory_utilization"],
            "runtime_seconds": dtlm_result["metrics"].get("runtime_seconds", 0.0),
            "result_path": str(final_result_path(m_ratio)),
        })

    rows.sort(key=lambda item: (item["M_ratio"], item["total_cost"]))
    write_results_table(rows)
    plot_cost_vs_m(rows)
    dtlm_ranks, avg_rank = build_ranking_table(rows)

    low_budget_gap = max(
        abs((dtlm_results[m]["metrics"]["cold_start_rate"] - gdsf_results[m]["metrics"]["cold_start_rate"]) * 100)
        for m in [0.1, 0.2]
    )
    mid_budget_pass = all(
        dtlm_results[m]["metrics"]["total_cold_start_cost"] <= gdsf_results[m]["metrics"]["total_cold_start_cost"]
        for m in [0.3, 0.5]
    )
    high_budget_pass = all(
        dtlm_results[m]["metrics"]["total_cold_start_cost"] < adaptive_results[m]["metrics"]["total_cold_start_cost"]
        for m in [0.7, 1.0]
    )
    avg_rank_pass = avg_rank <= 3.0
    guardrail_pass = all(
        dtlm_results[m]["metrics"]["total_cold_start_cost"] <= 1.1 * gdsf_results[m]["metrics"]["total_cold_start_cost"]
        for m in STEP4_M_RATIOS
    )

    conclusion = "满足主要成功标准" if all([
        low_budget_gap < 5.0,
        mid_budget_pass,
        high_budget_pass,
        avg_rank_pass,
        guardrail_pass,
    ]) else "部分满足成功标准，需要进一步调参或复核"

    lines = [
        "=== Step 4 完成 ===",
        "",
        "DTLM v3 排名：",
        f"- M_ratio=0.1: rank {dtlm_ranks[0.1]}",
        f"- M_ratio=0.2: rank {dtlm_ranks[0.2]}",
        f"- M_ratio=0.3: rank {dtlm_ranks[0.3]}",
        f"- M_ratio=0.5: rank {dtlm_ranks[0.5]}",
        f"- M_ratio=0.7: rank {dtlm_ranks[0.7]}",
        f"- M_ratio=1.0: rank {dtlm_ranks[1.0]}",
        f"- 平均 rank: {avg_rank:.2f}",
        "",
        "成功标准：",
        f"1. 低预算 CSR 接近 GDSF：{'通过' if low_budget_gap < 5.0 else '不通过'}（差距 {low_budget_gap:.2f}%）",
        f"2. 中预算 cost ≤ GDSF：{'通过' if mid_budget_pass else '不通过'}",
        f"3. 高预算 cost < Adaptive-TTL+LRU：{'通过' if high_budget_pass else '不通过'}",
        f"4. 平均 rank ≤ 3：{'通过' if avg_rank_pass else '不通过'}",
        f"5. 任何 M_ratio 下 ≤ 1.1× GDSF：{'通过' if guardrail_pass else '不通过'}",
        "",
        "产出文件：",
        "- results_table.csv",
        "- cost_vs_M.png",
        "- ranking_table.txt",
        "",
        f"最终结论：{conclusion}",
        f"总运行时间：{time.time() - start:.1f}s",
    ]
    report = "\n".join(lines)
    write_text(RESULTS_DIR / "step4_report.txt", report)
    print(report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", choices=["1", "2", "3", "4", "all"], default="all")
    args = parser.parse_args()

    if args.step in {"1", "all"}:
        run_step1()
    if args.step in {"2", "all"}:
        run_step2()
    if args.step in {"3", "all"}:
        run_step3()
    if args.step in {"4", "all"}:
        run_step4()


if __name__ == "__main__":
    main()
