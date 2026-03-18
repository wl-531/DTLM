"""Phase 6 测试：TTLmin,extnd"""
from policies.ttlmin_extnd import TTLminExtnd
from engine import simulate

functions_info = {
    "f1": {"m_i": 100, "c_i": 200},
    "f2": {"m_i": 150, "c_i": 350},
    "f3": {"m_i": 200, "c_i": 500},
    "f4": {"m_i": 120, "c_i": 300},
    "f5": {"m_i": 130, "c_i": 250},
}
M = 500

stream = []
t = 0
pattern = (["f1"]*10 + ["f2"]*5 + ["f3"]*2 + ["f4"]*2 + ["f5"]*1) * 5
for fid in pattern:
    stream.append((t, fid, "app1", functions_info[fid]["m_i"]))
    t += 60000

policy = TTLminExtnd(M, functions_info)
results = simulate(policy, stream)
cold_count = sum(1 for _, _, cold, _, _ in results if cold)
print(f"TTLmin,extnd: cold starts = {cold_count} / {len(stream)}, "
      f"rate = {cold_count/len(stream):.3f}")
print(f"TTL 延长触发次数: {policy.ttl_extend_count}")
print(f"Eviction 触发次数: {policy.eviction_count}")
print(f"Peak memory: {max(mem for _, _, _, mem, _ in results):.0f}MB")
print("\n=== Phase 6 验证完成 ===")
