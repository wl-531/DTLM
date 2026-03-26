# test_admission_gate.py
from policies.adaptive_ttl_only import AdaptiveTTLOnly
from engine import simulate
from metrics import summary

# === Test 1: Basic admission failure behavior ===
functions_info = {
    "hot": {"m_i": 300, "c_i": 500},
    "warm": {"m_i": 300, "c_i": 300},
    "cold": {"m_i": 300, "c_i": 200},
}
M = 500  # 只能同时放 1 个函数（300 < 500 < 600）

stream = []
t = 0
for i in range(20):
    fid = "hot" if i % 3 != 2 else "warm"
    stream.append((t, fid, "app", functions_info[fid]["m_i"]))
    t += 120000  # 2 分钟间隔

policy = AdaptiveTTLOnly(M, functions_info)
results = simulate(policy, stream)
metrics = summary(results, functions_info, M, policy=policy, skip_warmup=False)
bd = metrics["cold_start_breakdown"]

print(f"admission_failure: {bd['admission_failure_cold_starts']}")
print(f"initial: {bd['initial_cold_starts']}")
print(f"expiry_induced: {bd['expiry_induced_cold_starts']}")
print(f"eviction_induced: {bd['eviction_induced_cold_starts']}")
print(f"peak_memory: {metrics['peak_memory_mb']}")

# 关键断言
total_cold = metrics["total_cold_starts"]
sum_causes = (bd["initial_cold_starts"] + bd["expiry_induced_cold_starts"]
              + bd["eviction_induced_cold_starts"] + bd["admission_failure_cold_starts"])
assert sum_causes == total_cold, f"Cause taxonomy not closed: {sum_causes} vs {total_cold}"
assert bd["admission_failure_cold_starts"] > 0, "Expected some admission failures at M=500"
assert metrics["peak_memory_mb"] <= M, f"Memory exceeded M: {metrics['peak_memory_mb']} > {M}"
assert bd["eviction_induced_cold_starts"] == 0, "TTL-only policy should have zero evictions"
print("TEST 1 PASSED: Basic admission failure")

# === Test 2: Edge case — function larger than total memory ===
big_functions = {
    "giant": {"m_i": 800, "c_i": 1000},  # m_i > M
    "small": {"m_i": 100, "c_i": 200},
}
M_small = 500

stream2 = [
    (0, "small", "app", 100),
    (60000, "giant", "app", 800),       # admission failure: 800 > 500
    (120000, "giant", "app", 800),      # again
    (180000, "small", "app", 100),      # should hit (still cached)
    (240000, "giant", "app", 800),      # still can't fit
]

policy2 = AdaptiveTTLOnly(M_small, big_functions)
results2 = simulate(policy2, stream2)
metrics2 = summary(results2, big_functions, M_small, policy=policy2, skip_warmup=False)
bd2 = metrics2["cold_start_breakdown"]

assert bd2["admission_failure_cold_starts"] == 3, \
    f"Giant function should fail admission 3 times, got {bd2['admission_failure_cold_starts']}"
assert metrics2["peak_memory_mb"] <= M_small, \
    f"Memory exceeded M: {metrics2['peak_memory_mb']} > {M_small}"

# small 的第二次请求（t=180000）应该是 hit
cold_at_180k = [r for r in results2 if r[0] == 180000 and r[2] == True]
assert len(cold_at_180k) == 0, "small@180000 should be a hit (still cached)"

print("TEST 2 PASSED: m_i > M edge case")
print("\nALL TESTS PASSED")
