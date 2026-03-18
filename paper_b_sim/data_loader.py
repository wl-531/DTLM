import os
import numpy as np
import pandas as pd
from config import TRACE_DIR, SEED, N_APPS, SMALL_MEM_THRESHOLD, LARGE_MEM_THRESHOLD, N_SMALL, N_LARGE


def load_memory(days=range(1, 13)):
    """加载 app 级别内存数据，返回 per-app 平均内存 (MB)"""
    frames = []
    for d in days:
        path = os.path.join(TRACE_DIR, f"app_memory_percentiles.anon.d{d:02d}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, usecols=["HashApp", "AverageAllocatedMb"])
        frames.append(df)
    mem = pd.concat(frames, ignore_index=True)
    app_mem = mem.groupby("HashApp")["AverageAllocatedMb"].mean().reset_index()
    app_mem.columns = ["HashApp", "m_mb"]
    print(f"Loaded memory for {len(app_mem)} apps")
    return app_mem


def load_invocations_for_apps(app_set, days=range(1, 15)):
    """加载指定 app 集合的调用数据，返回 per-function 每天每分钟的调用次数"""
    frames = []
    for d in days:
        path = os.path.join(TRACE_DIR, f"invocations_per_function_md.anon.d{d:02d}.csv")
        if not os.path.exists(path):
            continue
        # 分块读取，只保留目标 app
        for chunk in pd.read_csv(path, chunksize=10000):
            filtered = chunk[chunk["HashApp"].isin(app_set)].copy()
            if len(filtered) > 0:
                filtered["day"] = d
                frames.append(filtered)
    if not frames:
        raise ValueError("No invocation data found for sampled apps")
    inv = pd.concat(frames, ignore_index=True)
    print(f"Loaded invocations: {len(inv)} function-day rows")
    return inv


def load_triggers(days=range(1, 15)):
    """加载 trigger 类型信息（只取第一天的去重即可）"""
    path = os.path.join(TRACE_DIR, "invocations_per_function_md.anon.d01.csv")
    df = pd.read_csv(path, usecols=["HashApp", "HashFunction", "Trigger"])
    triggers = df.drop_duplicates(subset=["HashFunction"])[["HashFunction", "Trigger"]]
    print(f"Loaded triggers for {len(triggers)} functions")
    return triggers


def forced_tail_sample(app_mem, seed=SEED):
    """强制尾部抽样：确保包含极端内存的 app"""
    rng = np.random.default_rng(seed)

    small_apps = app_mem[app_mem["m_mb"] <= SMALL_MEM_THRESHOLD]["HashApp"].values
    large_apps = app_mem[app_mem["m_mb"] >= LARGE_MEM_THRESHOLD]["HashApp"].values
    middle_apps = app_mem[
        (app_mem["m_mb"] > SMALL_MEM_THRESHOLD) & (app_mem["m_mb"] < LARGE_MEM_THRESHOLD)
    ]["HashApp"].values

    # 强制选
    forced_small = rng.choice(small_apps, size=min(N_SMALL, len(small_apps)), replace=False)
    forced_large = rng.choice(large_apps, size=min(N_LARGE, len(large_apps)), replace=False)
    forced = set(forced_small) | set(forced_large)

    # 从剩余中随机补齐
    remaining_pool = app_mem[~app_mem["HashApp"].isin(forced)]["HashApp"].values
    n_remaining = N_APPS - len(forced)
    extra = rng.choice(remaining_pool, size=min(n_remaining, len(remaining_pool)), replace=False)

    sampled = list(forced) + list(extra)
    sampled_df = app_mem[app_mem["HashApp"].isin(sampled)].copy()
    print(f"Sampled {len(sampled)} apps: "
          f"{sum(sampled_df['m_mb'] <= SMALL_MEM_THRESHOLD)} small, "
          f"{sum(sampled_df['m_mb'] >= LARGE_MEM_THRESHOLD)} large, "
          f"{sum((sampled_df['m_mb'] > SMALL_MEM_THRESHOLD) & (sampled_df['m_mb'] < LARGE_MEM_THRESHOLD))} middle")
    return sampled_df


def build_request_stream(inv_df, app_mem, day_range=(1, 14), seed=SEED):
    """生成请求流: list of (timestamp_ms, func_id, app_id, m_mb)"""
    rng = np.random.default_rng(seed)
    minute_cols = [str(i) for i in range(1, 1441)]

    # 筛选天数
    inv = inv_df[(inv_df["day"] >= day_range[0]) & (inv_df["day"] <= day_range[1])].copy()

    # 构建 func → app 内存映射
    mem_map = dict(zip(app_mem["HashApp"], app_mem["m_mb"]))

    events = []
    for _, row in inv.iterrows():
        func_id = row["HashFunction"]
        app_id = row["HashApp"]
        day = row["day"]
        m_i = mem_map.get(app_id, 100.0)  # fallback

        for mc in minute_cols:
            count = int(row[mc]) if mc in row.index and pd.notna(row[mc]) else 0
            if count <= 0:
                continue
            # 该分钟的起始时间 (ms)
            minute_idx = int(mc) - 1  # 0-based
            base_ms = (day - 1) * 86400000 + minute_idx * 60000
            if count == 1:
                events.append((base_ms, func_id, app_id, m_i))
            else:
                # Poisson 均匀分布在该分钟内
                offsets = np.sort(rng.uniform(0, 60000, size=count)).astype(int)
                for off in offsets:
                    events.append((base_ms + off, func_id, app_id, m_i))

    events.sort(key=lambda x: x[0])
    print(f"Built request stream: {len(events)} events, "
          f"day {day_range[0]}-{day_range[1]}")
    return events


def compute_working_set(request_stream, app_mem, window_minutes=60):
    """计算 working set 时间序列（每分钟采样），返回均值 (MB)"""
    if not request_stream:
        return 0.0

    mem_map = dict(zip(app_mem["HashApp"], app_mem["m_mb"]))
    window_ms = window_minutes * 60000

    # 按分钟桶统计活跃函数
    func_last_seen = {}  # func_id → last timestamp
    func_mem = {}  # func_id → m_mb

    # 采样点：每分钟一个
    t_start = request_stream[0][0]
    t_end = request_stream[-1][0]
    sample_interval = 60000  # 1 分钟

    # 先建索引：按时间排序的事件（已排序）
    event_idx = 0
    ws_values = []

    t = t_start
    while t <= t_end:
        # 推进事件到当前时刻
        while event_idx < len(request_stream) and request_stream[event_idx][0] <= t:
            _, fid, aid, m_i = request_stream[event_idx]
            func_last_seen[fid] = request_stream[event_idx][0]
            func_mem[fid] = m_i
            event_idx += 1

        # 计算窗口内活跃函数的总内存
        cutoff = t - window_ms
        ws = sum(func_mem[fid] for fid, ts in func_last_seen.items() if ts >= cutoff)
        ws_values.append(ws)
        t += sample_interval

    mean_ws = np.mean(ws_values)
    print(f"Working set: mean={mean_ws:.1f} MB, "
          f"min={min(ws_values):.1f}, max={max(ws_values):.1f}, "
          f"samples={len(ws_values)}")
    return mean_ws


if __name__ == "__main__":
    print("=== Phase 1 验证 ===")
    # 1. 加载内存
    app_mem = load_memory()

    # 2. 强制尾部抽样
    sampled = forced_tail_sample(app_mem)
    sampled_apps = set(sampled["HashApp"])

    # 3. 加载抽样子集的调用数据
    inv = load_invocations_for_apps(sampled_apps)

    # 4. 统计函数数量
    func_ids = inv["HashFunction"].unique()
    print(f"\n子集函数数: {len(func_ids)}")

    # 5. 计算日调用量（取所有天的平均）
    minute_cols = [str(i) for i in range(1, 1441)]
    daily_counts = inv[minute_cols].sum(axis=1).groupby(inv["day"]).sum()
    print(f"子集日调用量 (各天): \n{daily_counts}")
    print(f"子集日调用量均值: {daily_counts.mean():.0f}")

    # 6. 构建请求流（只用 day 3 做快速验证）
    print("\n构建请求流 (day 3 only)...")
    stream = build_request_stream(inv, sampled, day_range=(3, 3))

    # 7. 计算 working set
    print("\n计算 working set...")
    ws_mean = compute_working_set(stream, sampled)
    print(f"\n子集 working set 均值: {ws_mean:.1f} MB = {ws_mean/1024:.2f} GB")

    print("\n=== Phase 1 验证完成 ===")
