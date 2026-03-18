"""Phase 5 测试：GDSF / Adaptive-TTL-only / Adaptive-TTL+LRU"""
from policies.gdsf import GDSF
from policies.adaptive_ttl_only import AdaptiveTTLOnly
from policies.adaptive_ttl_lru import AdaptiveTTL_LRU
from engine import simulate

functions_info = {
    "f1": {"m_i": 100, "c_i": 200},
    "f2": {"m_i": 150, "c_i": 350},
    "f3": {"m_i": 200, "c_i": 500},
    "f4": {"m_i": 120, "c_i": 300},
    "f5": {"m_i": 130, "c_i": 250},
}
M = 500

# 有局部性 + 时间间隔足以触发 TTL
stream = []
t = 0
pattern = (["f1"]*10 + ["f2"]*5 + ["f3"]*2 + ["f4"]*2 + ["f5"]*1) * 5
for fid in pattern:
    stream.append((t, fid, "app1", functions_info[fid]["m_i"]))
    t += 60000  # 每分钟一个请求（让 TTL 有意义）

print(f"请求流: {len(stream)} 个请求, 时间跨度: {t/60000:.0f} 分钟")
print(f"M={M}MB")

for name, cls in [("GDSF", GDSF), ("Adaptive-TTL-only", AdaptiveTTLOnly),
                   ("Adaptive-TTL+LRU", AdaptiveTTL_LRU)]:
    policy = cls(M, functions_info)
    results = simulate(policy, stream)
    cold_count = sum(1 for _, _, cold, _, _ in results if cold)
    peak_mem = max(mem for _, _, _, mem, _ in results)
    print(f"{name}: cold starts = {cold_count} / {len(stream)}, "
          f"rate = {cold_count/len(stream):.3f}, peak_mem = {peak_mem:.0f}MB")

print("\n=== Phase 5 验证完成 ===")
