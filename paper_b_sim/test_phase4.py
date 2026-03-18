"""Phase 4 测试：LRU / LFU / Fixed-TTL+LRU"""
from policies.lru import LRU
from policies.lfu import LFU
from policies.fixed_ttl_lru import FixedTTL_LRU
from engine import simulate

functions_info = {
    "f1": {"m_i": 100, "c_i": 200},
    "f2": {"m_i": 150, "c_i": 350},
    "f3": {"m_i": 200, "c_i": 500},
    "f4": {"m_i": 120, "c_i": 300},
    "f5": {"m_i": 130, "c_i": 250},
}
M = 500  # 稍大一点，能放 2-3 个函数

# 构造有局部性的请求流：f1 连续调用多次，然后 f2，偶尔 f3/f4/f5
import numpy as np
rng = np.random.default_rng(42)
stream = []
t = 0
# f1 被频繁调用，f2 次之，f3/f4/f5 偶尔
pattern = (["f1"]*10 + ["f2"]*5 + ["f3"]*2 + ["f4"]*2 + ["f5"]*1) * 5
for fid in pattern:
    stream.append((t, fid, "app1", functions_info[fid]["m_i"]))
    t += 1000  # 每秒一个

print(f"请求流: {len(stream)} 个请求")
print(f"M={M}MB")

for name, cls in [("LRU", LRU), ("LFU", LFU), ("Fixed-TTL+LRU", FixedTTL_LRU)]:
    policy = cls(M, functions_info)
    results = simulate(policy, stream)
    cold_count = sum(1 for _, _, cold, _, _ in results if cold)
    print(f"{name}: cold starts = {cold_count} / {len(stream)}, "
          f"rate = {cold_count/len(stream):.3f}")

# 用更大 M 再测一次（应该 cold starts 更少）
M2 = 800  # 能放 4-5 个函数
print(f"\nM={M2}MB:")
for name, cls in [("LRU", LRU), ("LFU", LFU), ("Fixed-TTL+LRU", FixedTTL_LRU)]:
    policy = cls(M2, functions_info)
    results = simulate(policy, stream)
    cold_count = sum(1 for _, _, cold, _, _ in results if cold)
    print(f"{name}: cold starts = {cold_count} / {len(stream)}, "
          f"rate = {cold_count/len(stream):.3f}")

print("\n=== Phase 4 验证完成 ===")
