import numpy as np
import pandas as pd


def _base_latency(m_mb):
    """基于内存大小的基础冷启动延迟 (ms)"""
    if m_mb <= 128:
        return 200
    elif m_mb <= 256:
        return 350
    elif m_mb <= 512:
        return 500
    elif m_mb <= 1024:
        return 800
    else:
        return 1200


def _language_factor(trigger):
    """基于 trigger 类型的语言代理因子"""
    if pd.isna(trigger):
        return 1.0
    t = str(trigger).strip().lower()
    if t in ("queue", "timer"):
        return 0.8
    return 1.0


def calibrate_cold_start(functions_df, scale=1.0):
    """为每个函数计算冷启动代价 c_i (ms)

    functions_df 需要包含: HashFunction, m_mb, Trigger(可选)
    返回 dict: func_id → c_i
    """
    result = {}
    for _, row in functions_df.iterrows():
        base = _base_latency(row["m_mb"])
        lf = _language_factor(row.get("Trigger", None))
        c_i = base * lf * scale
        result[row["HashFunction"]] = c_i
    return result


if __name__ == "__main__":
    from data_loader import load_memory, forced_tail_sample, load_invocations_for_apps, load_triggers

    print("=== Phase 2 验证 ===")
    app_mem = load_memory()
    sampled = forced_tail_sample(app_mem)
    sampled_apps = set(sampled["HashApp"])

    inv = load_invocations_for_apps(sampled_apps)
    triggers = load_triggers()

    # 构建 functions_df: 每个函数的 m_mb 和 trigger
    func_info = inv[["HashApp", "HashFunction"]].drop_duplicates()
    func_info = func_info.merge(sampled[["HashApp", "m_mb"]], on="HashApp", how="left")
    func_info = func_info.merge(triggers, on="HashFunction", how="left")

    # 校准
    c_map = calibrate_cold_start(func_info)

    # 描述统计
    c_values = np.array(list(c_map.values()))
    m_values = func_info.set_index("HashFunction")["m_mb"].reindex(c_map.keys()).values

    print(f"\nc_i 范围: {c_values.min():.0f}ms ~ {c_values.max():.0f}ms")
    print(f"c_i 中位数: {np.median(c_values):.0f}ms")
    corr = np.corrcoef(m_values, c_values)[0, 1]
    print(f"c_i 与 m_i 相关系数: {corr:.4f}")

    # trigger 类型分布
    trigger_counts = func_info["Trigger"].value_counts()
    print(f"\nTrigger 类型分布:\n{trigger_counts}")
    has_trigger = func_info["Trigger"].notna().sum()
    print(f"trigger 类型是否可用: {'是' if has_trigger > 0 else '否'} ({has_trigger}/{len(func_info)})")

    print("\n=== Phase 2 验证完成 ===")
