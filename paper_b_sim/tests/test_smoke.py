"""Phase 9: 冒烟测试 - 全部 8 策略, M_ratio=0.3, day 1-3 (day3 为评估)"""
import sys
sys.path.insert(0, r"D:\code\paper_b_sim")

from runner import prepare_data, run_single, POLICY_MAP, UNCONSTRAINED_POLICIES


def main():
    # 加载数据（只做一次）：day 1-3, day 1-2 warm-up, day 3 评估
    data = prepare_data(seed=42, days=(1, 3))
    M_ratio = 0.3
    M = data["ws_mean"] * M_ratio

    results_table = []
    for pname in POLICY_MAP:
        result = run_single(data, pname, M_ratio)
        m = result["metrics"]
        results_table.append({
            "policy": pname,
            "cold_start_rate": m["cold_start_rate"],
            "total_cost": m["total_cold_start_cost"],
            "avg_mem_util": m["avg_memory_utilization"],
            "peak_mem_mb": m["peak_memory_mb"],
            "runtime": m["runtime_seconds"],
            "unconstrained": m.get("memory_unconstrained", False),
        })

    # 输出结果表
    print(f"\n{'='*80}")
    print(f"冒烟测试结果 (M_ratio={M_ratio}, M={M:.0f}MB, day 3 评估)")
    print(f"{'='*80}")
    header = f"{'策略':<22} {'CSR':>8} {'Total Cost':>12} {'MemUtil':>8} {'PeakMem':>10} {'Time':>6}"
    print(header)
    print("-" * 70)
    for r in results_table:
        flag = " *" if r["unconstrained"] else ""
        print(f"{r['policy']:<22} {r['cold_start_rate']:>8.4f} {r['total_cost']:>12.0f} "
              f"{r['avg_mem_util']:>8.4f} {r['peak_mem_mb']:>8.0f}MB {r['runtime']:>5.1f}s{flag}")

    print("\n* = memory_unconstrained (不建模容量约束)")

    # 合理性检查
    print(f"\n--- 合理性检查 ---")
    all_ok = True
    for r in results_table:
        pname = r["policy"]
        # (a) 所有策略跑通
        # (b) cold_start_rate 在 [0, 1]
        if not (0 <= r["cold_start_rate"] <= 1):
            print(f"FAIL: {pname} CSR={r['cold_start_rate']} out of [0,1]")
            all_ok = False
        # (c) 内存约束策略 peak_mem <= M (允许 1% 误差)
        if not r["unconstrained"] and r["peak_mem_mb"] > M * 1.01:
            print(f"WARN: {pname} peak_mem={r['peak_mem_mb']:.0f} > M={M:.0f}")
            all_ok = False

    if all_ok:
        print("所有检查通过")
    else:
        print("存在异常，需排查")

    print(f"\n=== Phase 9 冒烟测试完成 ===")


if __name__ == "__main__":
    main()
