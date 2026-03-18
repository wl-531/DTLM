"""Phase 7 测试：DTLM"""
from policies.dtlm import DTLM
from engine import simulate

functions_info = {
    "f1": {"m_i": 100, "c_i": 200},
    "f2": {"m_i": 150, "c_i": 800},   # 高 c_i，应被保护
    "f3": {"m_i": 200, "c_i": 500},
    "f4": {"m_i": 120, "c_i": 100},   # 低 c_i，应优先驱逐
    "f5": {"m_i": 130, "c_i": 250},
}
M = 500

stream = []
t = 0
pattern = (["f1"]*10 + ["f2"]*5 + ["f3"]*2 + ["f4"]*2 + ["f5"]*1) * 5
for fid in pattern:
    stream.append((t, fid, "app1", functions_info[fid]["m_i"]))
    t += 60000

policy = DTLM(M, functions_info, sim_start_ms=0)
results = simulate(policy, stream)
cold_count = sum(1 for _, _, cold, _, _ in results if cold)
print(f"DTLM: cold starts = {cold_count} / {len(stream)}, "
      f"rate = {cold_count/len(stream):.3f}")

# 验证 pressure 变化时 TTL 缩放
print("\n--- Pressure / TTL 缩放验证 ---")
policy2 = DTLM(M, functions_info, sim_start_ms=0)
# 手动设置 ema_iat
for fid in functions_info:
    policy2.ema_iat[fid] = 300000  # 5 分钟 EMA

# 低压力
policy2._mem_used = 200  # 200/500 = 0.4
p_low = policy2._pressure(999999999)
d_low = policy2._decay(p_low)
print(f"pressure=0.4: decay={d_low:.2f} (预期接近 1.0)")

# 高压力
policy2._mem_used = 450  # 450/500 = 0.9
p_high = policy2._pressure(999999999)
d_high = policy2._decay(p_high)
print(f"pressure=0.9: decay={d_high:.2f} (预期接近 0.1~0.3)")

# Eviction score 示例
print("\n--- Eviction score 示例 ---")
policy3 = DTLM(M, functions_info, sim_start_ms=0)
for fid in functions_info:
    policy3.ema_iat[fid] = 300000
policy3.warm = {
    "f2": {"m_i": 150, "c_i": 800, "last_access": 50000, "load_time": 0},
    "f4": {"m_i": 120, "c_i": 100, "last_access": 50000, "load_time": 0},
}
policy3._mem_used = 270
ts_now = 100000
for fid in ["f2", "f4"]:
    score = policy3._eviction_score(fid, ts_now)
    info = functions_info[fid]
    print(f"  {fid}: c_i={info['c_i']}, m_i={info['m_i']}, "
          f"value_density={info['c_i']/info['m_i']:.2f}, "
          f"eviction_score={score:.4f}")

print(f"  f2 score > f4 score: {policy3._eviction_score('f2', ts_now) > policy3._eviction_score('f4', ts_now)} "
      f"(预期 True，f2 的 c_i/m_i 更高应被保护)")

print("\n=== Phase 7 验证完成 ===")
