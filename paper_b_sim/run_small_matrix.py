"""Small matrix validation (v3.1): DTLM / GDSF / C2RD-SR × M_ratio {0.3,0.5,0.7,1.0}
Validates Group 1 (cold-start breakdown), Group 3 (deletion-time utilization),
and confirms Group 4 (per_function_stats) is populated.

DTLM runs with paper-frozen v3.1 parameters (physical_delete_requires_pressure=True,
p_deactivate=0.95). Tau values are loaded from results/dtlm_v3/selected_params.json
if available, otherwise hardcoded fallback values are used.
"""
import json
import os
import time
from pathlib import Path

from runner import prepare_data, run_single

# --- 实验参数 ---
POLICIES = ["dtlm", "gdsf", "c2rd_sr"]
M_RATIOS = [0.3, 0.5, 0.7, 1.0]
COLD_START_SCALE = 1.0
SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

_PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_DIR / "results" / "small_matrix_v3_1"

# --- DTLM v3.1 参数 ---
_DTLM_V31_FALLBACK_TAUS = {
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
}

def _load_dtlm_v31_kwargs():
    """Load v3.1 DTLM kwargs: tau from selected_params.json if available, rest fixed."""
    taus = dict(_DTLM_V31_FALLBACK_TAUS)
    params_path = _PROJECT_DIR / "results" / "dtlm_v3" / "selected_params.json"
    if params_path.exists():
        with open(params_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
            if key in saved:
                taus[key] = saved[key]
        print(f"Loaded tau values from {params_path}: {taus}")
    else:
        print(f"selected_params.json not found, using fallback tau values: {taus}")
    return {
        "physical_delete_requires_pressure": True,
        "p_deactivate": 0.95,
        "hot_threshold": 10,
        "warm_threshold": 1,
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
        **taus,
    }

POLICY_KWARGS = {
    "dtlm": _load_dtlm_v31_kwargs(),
}
REQUIRED_FIELDS = ["cold_start_breakdown", "utilization_stats", "per_function_stats"]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    total = len(POLICIES) * len(M_RATIOS)
    count = 0
    results = {}
    missing = []

    for m_ratio in M_RATIOS:
        for policy_name in POLICIES:
            count += 1
            output_path = OUTPUT_DIR / f"{policy_name}_{m_ratio}.json"
            label = f"{policy_name} M={m_ratio}"

            print(f"\n{'='*60}")
            print(f"[{count}/{total}] {label}")
            print(f"{'='*60}")

            t0 = time.time()
            result = run_single(
                data,
                policy_name,
                m_ratio,
                cold_start_scale=COLD_START_SCALE,
                output_path=str(output_path),
                warmup_days=WARMUP_DAYS,
                policy_kwargs=POLICY_KWARGS.get(policy_name),
            )
            elapsed = time.time() - t0

            # 检查必要字段
            metrics = result["metrics"]
            for field in REQUIRED_FIELDS:
                if field not in metrics or not metrics[field]:
                    missing.append({
                        "field": field,
                        "policy": policy_name,
                        "M_ratio": m_ratio,
                    })

            results[(policy_name, m_ratio)] = result
            cost_val = metrics["total_cold_start_cost"]
            csr_val = metrics["cold_start_rate"]
            print(f"  CSR={csr_val*100:.2f}% Cost={cost_val:.0f} Time={elapsed:.1f}s")

    # --- 缺失字段检查 ---
    if missing:
        print("\n!!! 缺失字段 !!!")
        for m in missing:
            print(f"  {m['field']} @ {m['policy']} M={m['M_ratio']}")
            print(f"  可能相关: metrics.py summary() / runner.py run_single() / engine.py simulate()")
        # 写出缺失报告
        with open(OUTPUT_DIR / "missing_fields.json", "w", encoding="utf-8") as f:
            json.dump(missing, f, indent=2)
        print("缺失报告已写入 missing_fields.json，终止汇总。")
        return

    # --- 汇总 ---
    build_summary(results)
    print(f"\n汇总已写入 {OUTPUT_DIR / 'summary.md'}")


def build_summary(results):
    lines = []
    lines.append("# Small Matrix Validation Results\n")
    lines.append(f"策略: DTLM, GDSF, C2RD-SR | M_ratio: 0.3, 0.5, 0.7, 1.0 | cost_scaling: 1.0 | seed: {SEED}\n")

    # --- 表 1: Total cost 对比 ---
    lines.append("## 1. Total Cost 对比\n")
    lines.append("| M_ratio | DTLM | GDSF | C2RD-SR | DTLM/GDSF | DTLM/C2RD-SR |")
    lines.append("|---------|------|------|---------|-----------|--------------|")
    for m in M_RATIOS:
        dtlm_cost = results[("dtlm", m)]["metrics"]["total_cold_start_cost"]
        gdsf_cost = results[("gdsf", m)]["metrics"]["total_cold_start_cost"]
        c2rd_cost = results[("c2rd_sr", m)]["metrics"]["total_cold_start_cost"]
        ratio_gdsf = dtlm_cost / gdsf_cost if gdsf_cost else float("inf")
        ratio_c2rd = dtlm_cost / c2rd_cost if c2rd_cost else float("inf")
        lines.append(f"| {m} | {dtlm_cost:.0f} | {gdsf_cost:.0f} | {c2rd_cost:.0f} | {ratio_gdsf:.4f} | {ratio_c2rd:.4f} |")

    # --- 表 2: 冷启动分解 (DTLM + C2RD-SR) ---
    lines.append("\n## 2. 冷启动分解（DTLM 和 C2RD-SR）\n")
    lines.append("| 策略 | M_ratio | initial(n/cost) | expiry_induced(n/cost) | eviction_induced(n/cost) |")
    lines.append("|------|---------|-----------------|------------------------|--------------------------|")
    for policy in ["dtlm", "c2rd_sr"]:
        display = "DTLM" if policy == "dtlm" else "C2RD-SR"
        for m in M_RATIOS:
            bd = results[(policy, m)]["metrics"]["cold_start_breakdown"]
            lines.append(
                f"| {display} | {m} "
                f"| {bd['initial_cold_starts']}/{bd['initial_cold_cost']:.0f} "
                f"| {bd['expiry_induced_cold_starts']}/{bd['expiry_induced_cold_cost']:.0f} "
                f"| {bd['eviction_induced_cold_starts']}/{bd['eviction_induced_cold_cost']:.0f} |"
            )

    # --- 表 3: 删除时刻利用率 (DTLM only) ---
    lines.append("\n## 3. 删除时刻利用率（DTLM）\n")
    lines.append("| M_ratio | 类型 | count | mean | median | P95 |")
    lines.append("|---------|------|-------|------|--------|-----|")
    for m in M_RATIOS:
        util = results[("dtlm", m)]["metrics"]["utilization_stats"]
        dt = util["deletion_time"]
        for reason in ["expiry", "eviction"]:
            d = dt[reason]
            lines.append(
                f"| {m} | {reason} | {d['count']} "
                f"| {d['mean']:.4f} | {d['median']:.4f} | {d['p95']:.4f} |"
            )

    # --- 表 4: DTLM vs C2RD-SR expiry 行为对比 (M=0.7, 1.0) ---
    lines.append("\n## 4. DTLM vs C2RD-SR Expiry 行为对比（M=0.7, 1.0）\n")
    lines.append("| M_ratio | 策略 | expiry_induced_starts | expiry_induced_cost | eviction_induced_starts | eviction_induced_cost |")
    lines.append("|---------|------|-----------------------|---------------------|-------------------------|-----------------------|")
    for m in [0.7, 1.0]:
        for policy in ["dtlm", "c2rd_sr"]:
            display = "DTLM" if policy == "dtlm" else "C2RD-SR"
            bd = results[(policy, m)]["metrics"]["cold_start_breakdown"]
            lines.append(
                f"| {m} | {display} "
                f"| {bd['expiry_induced_cold_starts']} | {bd['expiry_induced_cold_cost']:.0f} "
                f"| {bd['eviction_induced_cold_starts']} | {bd['eviction_induced_cold_cost']:.0f} |"
            )

    # --- 表 5: per_function_stats 确认 ---
    lines.append("\n## 5. per_function_stats 采集确认\n")
    lines.append("| 策略 | M_ratio | functions_tracked | total_request_count | total_cold_cost |")
    lines.append("|------|---------|-------------------|---------------------|-----------------|")
    for policy in POLICIES:
        display = {"dtlm": "DTLM", "gdsf": "GDSF", "c2rd_sr": "C2RD-SR"}[policy]
        for m in M_RATIOS:
            pfs = results[(policy, m)]["metrics"]["per_function_stats"]
            n_funcs = len(pfs)
            total_req = sum(v["request_count"] for v in pfs.values())
            total_cc = sum(v["cold_start_cost"] for v in pfs.values())
            lines.append(f"| {display} | {m} | {n_funcs} | {total_req} | {total_cc:.0f} |")

    # --- 结论 ---
    lines.append("\n## 结论\n")

    # 计算关键数据
    conclusions = []

    # DTLM vs GDSF / C2RD-SR 胜负
    dtlm_wins_gdsf = []
    dtlm_wins_c2rd = []
    for m in M_RATIOS:
        dtlm_cost = results[("dtlm", m)]["metrics"]["total_cold_start_cost"]
        gdsf_cost = results[("gdsf", m)]["metrics"]["total_cold_start_cost"]
        c2rd_cost = results[("c2rd_sr", m)]["metrics"]["total_cold_start_cost"]
        if dtlm_cost < gdsf_cost:
            dtlm_wins_gdsf.append(m)
        if dtlm_cost < c2rd_cost:
            dtlm_wins_c2rd.append(m)

    conclusions.append(f"1. DTLM 在 M_ratio={dtlm_wins_gdsf} 上优于 GDSF（total cost 更低）。")
    conclusions.append(f"2. DTLM 在 M_ratio={dtlm_wins_c2rd} 上优于 C2RD-SR。")

    # DTLM 收益来源分析
    for m in M_RATIOS:
        dtlm_bd = results[("dtlm", m)]["metrics"]["cold_start_breakdown"]
        gdsf_bd = results[("gdsf", m)]["metrics"]["cold_start_breakdown"]
        eviction_delta = gdsf_bd["eviction_induced_cold_cost"] - dtlm_bd["eviction_induced_cold_cost"]
        expiry_cost = dtlm_bd["expiry_induced_cold_cost"]
        if eviction_delta > 0:
            conclusions.append(
                f"3-{m}. M={m}: DTLM 通过减少 eviction-induced cost 节省 {eviction_delta:.0f}，"
                f"expiry-induced cost 为 {expiry_cost:.0f}。"
            )

    # TTL 删除利用率分析
    for m in M_RATIOS:
        util = results[("dtlm", m)]["metrics"]["utilization_stats"]
        expiry_dt = util["deletion_time"]["expiry"]
        if expiry_dt["count"] > 0:
            conclusions.append(
                f"4-{m}. M={m}: DTLM TTL expiry 删除时平均利用率={expiry_dt['mean']:.4f}，"
                f"中位数={expiry_dt['median']:.4f}（count={expiry_dt['count']}）。"
            )
        else:
            conclusions.append(f"4-{m}. M={m}: DTLM 无 TTL expiry 删除（pressure gate 生效，TTL 层未执行物理删除）。")

    for c in conclusions:
        lines.append(f"- {c}")

    with open(OUTPUT_DIR / "summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
