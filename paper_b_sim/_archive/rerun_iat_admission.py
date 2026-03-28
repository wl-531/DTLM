"""重跑 IAT-Adaptive TTL (Admission-Gated) 并执行 sanity check。

只重跑 iat_adaptive_ttl 策略，其他 8 个策略的结果不受影响。
"""
import json
import shutil
import time
from pathlib import Path

from runner import prepare_data, run_single

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
BASELINE_DIR = RESULTS_DIR / "baseline"
SENSITIVITY_DIR = RESULTS_DIR / "sensitivity"
BACKUP_DIR = RESULTS_DIR / "backup_unconstrained_iat"

POLICY = "iat_adaptive_ttl"
SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

BASELINE_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SENSITIVITY_M_RATIOS = [0.2, 0.3, 0.5]
COLD_START_SCALES = [0.5, 1.0, 2.0]


def fmt(v):
    return f"{v:.1f}"


def baseline_path(m_ratio):
    return BASELINE_DIR / f"{POLICY}_{fmt(m_ratio)}.json"


def sensitivity_path(m_ratio, scale):
    return SENSITIVITY_DIR / f"{POLICY}_{fmt(m_ratio)}_{fmt(scale)}.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cost(result):
    return result["metrics"]["total_cold_start_cost"]


def step1_delete_old():
    """删除旧的 iat_adaptive_ttl 结果，强制重跑。"""
    print("=" * 60)
    print("Step 1: 删除旧结果文件")
    print("=" * 60)
    deleted = 0
    for m in BASELINE_M_RATIOS:
        p = baseline_path(m)
        if p.exists():
            p.unlink()
            deleted += 1
    for m in SENSITIVITY_M_RATIOS:
        for s in COLD_START_SCALES:
            p = sensitivity_path(m, s)
            if p.exists():
                p.unlink()
                deleted += 1
    print(f"已删除 {deleted} 个旧结果文件\n")


def step2_rerun_baseline(data):
    """重跑 baseline：6 M_ratio × scale=1.0"""
    print("=" * 60)
    print("Step 2: 重跑 baseline（6 M_ratio）")
    print("=" * 60)
    results = {}
    for i, m in enumerate(BASELINE_M_RATIOS, 1):
        path = baseline_path(m)
        t0 = time.time()
        result = run_single(
            data, POLICY, m,
            cold_start_scale=1.0,
            output_path=str(path),
            warmup_days=WARMUP_DAYS,
        )
        elapsed = time.time() - t0
        metrics = result["metrics"]
        print(f"  [{i}/6] M={fmt(m)}: cost={cost(result):.0f}, "
              f"CSR={metrics['cold_start_rate']*100:.2f}%, "
              f"peak_mem={metrics['peak_memory_mb']:.0f}MB, "
              f"time={elapsed:.1f}s")
        results[m] = result
    print()
    return results


def step3_rerun_sensitivity(data):
    """重跑 sensitivity：3 M × 3 scale = 9 组合。
    其中 scale=1.0 的 3 组从 baseline 复制，其余 6 组实际运行。
    """
    print("=" * 60)
    print("Step 3: 重跑 sensitivity（9 组合：3 复制 + 6 实跑）")
    print("=" * 60)
    combos = [(m, s) for m in SENSITIVITY_M_RATIOS for s in COLD_START_SCALES]
    for i, (m, s) in enumerate(combos, 1):
        path = sensitivity_path(m, s)
        if s == 1.0:
            src = baseline_path(m)
            shutil.copyfile(src, path)
            result = load_json(path)
            print(f"  [{i}/{len(combos)}] M={fmt(m)} scale={fmt(s)}: "
                  f"copied from baseline, cost={cost(result):.0f}")
        else:
            t0 = time.time()
            result = run_single(
                data, POLICY, m,
                cold_start_scale=s,
                output_path=str(path),
                warmup_days=WARMUP_DAYS,
            )
            elapsed = time.time() - t0
            print(f"  [{i}/{len(combos)}] M={fmt(m)} scale={fmt(s)}: "
                  f"cost={cost(result):.0f}, time={elapsed:.1f}s")
    print()


def step4_sanity_check(new_results):
    """Sanity check: 3 hard checks + 3 expected trends."""
    print("=" * 60)
    print("Step 4: Sanity Check")
    print("=" * 60)

    hard_pass = True
    lines = ["# IAT-Adaptive TTL (Admission-Gated) 重跑报告\n"]

    # ===== 新旧对比表 =====
    lines.append("## 新旧 Cost 对比\n")
    lines.append("| M_ratio | Old Cost | New Cost | Ratio | admission_failure | peak_mem | eviction_induced |")
    lines.append("|---------|----------|----------|-------|-------------------|----------|------------------|")

    for m in BASELINE_M_RATIOS:
        new = new_results[m]
        new_cost = cost(new)
        bd = new["metrics"]["cold_start_breakdown"]
        af = bd.get("admission_failure_cold_starts", 0)
        peak = new["metrics"]["peak_memory_mb"]
        evict = bd.get("eviction_induced_cold_starts", 0)

        old_path = BACKUP_DIR / f"{POLICY}_{fmt(m)}.json"
        if old_path.exists():
            old = load_json(old_path)
            old_cost = cost(old)
            ratio = new_cost / old_cost if old_cost > 0 else float("inf")
            lines.append(f"| {fmt(m)} | {old_cost:,.0f} | {new_cost:,.0f} | "
                         f"{ratio:.3f} | {af} | {peak:.0f} | {evict} |")
        else:
            lines.append(f"| {fmt(m)} | N/A | {new_cost:,.0f} | N/A | {af} | {peak:.0f} | {evict} |")

    # ===== HARD CHECKS =====
    lines.append("\n## Hard Checks（必须全部 PASS）\n")

    # Hard 1: Cause taxonomy 闭合
    taxonomy_ok = True
    for m in BASELINE_M_RATIOS:
        metrics = new_results[m]["metrics"]
        total_cold = metrics["total_cold_starts"]
        bd = metrics["cold_start_breakdown"]
        sum_causes = (bd.get("initial_cold_starts", 0)
                      + bd.get("expiry_induced_cold_starts", 0)
                      + bd.get("eviction_induced_cold_starts", 0)
                      + bd.get("admission_failure_cold_starts", 0))
        if sum_causes != total_cold:
            taxonomy_ok = False
            lines.append(f"- H1 Taxonomy 闭合 M={fmt(m)}: **FAIL** "
                         f"(sum={sum_causes}, total={total_cold})")
    if taxonomy_ok:
        lines.append("- H1 Cause taxonomy 闭合（所有 M）: **PASS**")
    else:
        hard_pass = False

    # Hard 2: peak_memory_mb <= M for all M
    peak_ok = True
    for m in BASELINE_M_RATIOS:
        M_mb = new_results[m]["M_MB"]
        peak = new_results[m]["metrics"]["peak_memory_mb"]
        if peak > M_mb + 0.1:  # 浮点容差
            peak_ok = False
            lines.append(f"- H2 peak_memory <= M at M={fmt(m)}: **FAIL** "
                         f"(peak={peak:.1f}, M={M_mb:.1f})")
    if peak_ok:
        lines.append("- H2 peak_memory_mb <= M（所有 M）: **PASS**")
    else:
        hard_pass = False

    # Hard 3: eviction_induced_cold_starts == 0 for all M
    eviction_ok = True
    for m in BASELINE_M_RATIOS:
        bd = new_results[m]["metrics"]["cold_start_breakdown"]
        evict = bd.get("eviction_induced_cold_starts", 0)
        if evict != 0:
            eviction_ok = False
            lines.append(f"- H3 zero eviction at M={fmt(m)}: **FAIL** "
                         f"(eviction_induced={evict})")
    if eviction_ok:
        lines.append("- H3 eviction_induced_cold_starts == 0（所有 M）: **PASS**")
    else:
        hard_pass = False

    # ===== EXPECTED TRENDS =====
    lines.append("\n## Expected Trends（预期方向，不判硬失败）\n")

    # Trend 1: M=1.0 新旧 cost 接近
    old_1_path = BACKUP_DIR / f"{POLICY}_1.0.json"
    if old_1_path.exists():
        old_1_cost = cost(load_json(old_1_path))
        new_1_cost = cost(new_results[1.0])
        diff_pct = abs(new_1_cost - old_1_cost) / old_1_cost * 100 if old_1_cost > 0 else 999
        label = "OK" if diff_pct < 5 else "REVIEW"
        lines.append(f"- T1 M=1.0 新旧 cost 差异: **{label}** "
                     f"(old={old_1_cost:.0f}, new={new_1_cost:.0f}, diff={diff_pct:.1f}%)")
    else:
        lines.append("- T1 M=1.0 新旧对比: SKIP (无备份)")

    # Trend 2: M=0.1 新 cost > 旧 cost
    old_01_path = BACKUP_DIR / f"{POLICY}_0.1.json"
    if old_01_path.exists():
        old_01_cost = cost(load_json(old_01_path))
        new_01_cost = cost(new_results[0.1])
        ratio_01 = new_01_cost / old_01_cost if old_01_cost > 0 else 0
        label = "OK" if new_01_cost > old_01_cost else "REVIEW"
        lines.append(f"- T2 M=0.1 新 cost > 旧 cost: **{label}** "
                     f"(old={old_01_cost:.0f}, new={new_01_cost:.0f}, ratio={ratio_01:.1f}×)")
    else:
        lines.append("- T2 M=0.1 对比: SKIP (无备份)")

    # Trend 3: admission_failure 总体随 M 下降
    af_counts = []
    for m in BASELINE_M_RATIOS:
        bd = new_results[m]["metrics"]["cold_start_breakdown"]
        af_counts.append((m, bd.get("admission_failure_cold_starts", 0)))
    af_01 = af_counts[0][1]  # M=0.1
    af_10 = af_counts[-1][1]  # M=1.0
    trend_ok = af_01 > af_10
    label = "OK" if trend_ok else "REVIEW"
    af_str = ", ".join(f"M={m}: {c}" for m, c in af_counts)
    lines.append(f"- T3 admission_failure M=0.1 > M=1.0: **{label}** ({af_str})")

    # ===== 总结 =====
    if hard_pass:
        lines.append("\n## 总结: **HARD CHECKS ALL PASS**")
    else:
        lines.append("\n## 总结: **HARD CHECK FAILED — 实现有误，需排查**")

    report = "\n".join(lines)
    report_path = RESULTS_DIR / "iat_admission_rerun_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已保存: {report_path}")
    return hard_pass


def main():
    t0 = time.time()

    print("加载数据...\n")
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    step1_delete_old()
    new_results = step2_rerun_baseline(data)
    step3_rerun_sensitivity(data)
    hard_pass = step4_sanity_check(new_results)

    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")

    if not hard_pass:
        print("\n[WARN] Hard check 未通过，实现有误，请将报告回传排查。")
    else:
        print("\n[OK] Hard checks 全部通过。")
        print("下一步：重新生成包含 iat_adaptive_ttl 的汇总图表。")


if __name__ == "__main__":
    main()
