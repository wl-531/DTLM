"""Phase 3 简单验证：5 函数 100 请求，手动验证 hit/miss"""
from policies.base import CachePolicy
from engine import simulate
from metrics import summary


class SimpleLRU(CachePolicy):
    """最简 LRU 用于测试 engine"""
    def __init__(self, M, functions_info):
        super().__init__(M, functions_info)
        self.warm = {}  # func_id -> {m_i, last_access}

    def on_request(self, timestamp_ms, func_id):
        if func_id in self.warm:
            self.warm[func_id]["last_access"] = timestamp_ms
            return False  # hit
        # cold start: 需要加载
        m_i = self.functions_info[func_id]["m_i"]
        # 驱逐直到空间够
        while self.memory_used() + m_i > self.M and self.warm:
            victim = min(self.warm, key=lambda f: self.warm[f]["last_access"])
            del self.warm[victim]
        self.warm[func_id] = {"m_i": m_i, "last_access": timestamp_ms}
        return True  # cold start

    def memory_used(self):
        return sum(v["m_i"] for v in self.warm.values())

    def get_state(self):
        return dict(self.warm)


# 5 个函数，内存各不同
functions_info = {
    "f1": {"m_i": 100, "c_i": 200},
    "f2": {"m_i": 150, "c_i": 350},
    "f3": {"m_i": 200, "c_i": 500},
    "f4": {"m_i": 120, "c_i": 300},
    "f5": {"m_i": 130, "c_i": 250},
}

# M = 400MB，只能同时放 2-3 个函数
M = 400

# 构造 100 个请求：按时间递增，函数循环出现
import numpy as np
rng = np.random.default_rng(42)
func_ids = list(functions_info.keys())
stream = []
for i in range(100):
    ts = i * 1000  # 每秒一个请求
    fid = func_ids[i % 5]
    app_id = "app1"
    m_i = functions_info[fid]["m_i"]
    stream.append((ts, fid, app_id, m_i))

policy = SimpleLRU(M, functions_info)
results = simulate(policy, stream)

# 手动计算预期：
# 循环 f1,f2,f3,f4,f5,f1,f2,...
# M=400, 第一轮全 cold: f1(100), f2(+150=250), f3(+200=450>400, 驱逐f1→放f3, 用=350)
# f4: 350+120=470>400, 驱逐f2(oldest)→放f4, 用=320
# f5: 320+130=450>400, 驱逐f3(oldest)→放f5, 用=250
# 第二轮: f1 miss, f2 miss, f3 miss, f4 可能 hit/miss, f5 可能 hit/miss
# 由于循环模式，每个函数都会被驱逐再加载，几乎全是 cold start

cold_count = sum(1 for _, _, cold, _, _ in results if cold)
hit_count = sum(1 for _, _, cold, _, _ in results if not cold)
print(f"\n=== Phase 3 简单测试 ===")
print(f"总请求: {len(results)}")
print(f"Cold starts: {cold_count}")
print(f"Hits: {hit_count}")

s = summary(results, functions_info, M, skip_warmup=False)
print(f"Cold start rate: {s['cold_start_rate']:.4f}")
print(f"Total cold start cost: {s['total_cold_start_cost']:.0f}ms")
print(f"Avg memory utilization: {s['avg_memory_utilization']:.4f}")
print(f"Peak memory: {s['peak_memory_mb']:.0f}MB")

# 验证：M=400 下循环访问 5 个函数（总内存 700），cold start rate 应该很高
assert cold_count > 50, f"Expected high cold starts, got {cold_count}"
assert cold_count + hit_count == 100
assert s["peak_memory_mb"] <= M, f"Memory exceeded M: {s['peak_memory_mb']} > {M}"
print("\n所有断言通过！")

# 估算引擎速度
import time
t0 = time.time()
policy2 = SimpleLRU(M, functions_info)
big_stream = stream * 100  # 10000 请求
_ = simulate(policy2, big_stream)
elapsed = time.time() - t0
print(f"引擎每秒处理请求数（估算）: {10000/elapsed:.0f}")

print("\n=== Phase 3 验证完成 ===")
