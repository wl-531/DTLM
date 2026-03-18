# DTLM v3 M=1.0 定向诊断

## 核心结论
- M=1.0 的额外成本主要来自：**TTL expiry**。
- 删除是否大多发生在低压/非紧张状态：**是**。
- tau_base 是否存在明显饱和：**当前实现没有 tau_base；仅有离散 TTL，且存在明显饱和现象**。
- 当前故障更像：**边界没关住**。
- 是否推荐进入 v3.1 修补：**推荐**。

## 事实
- DTLM v3 total cost: 2271460
- GDSF total cost: 1249850
- Delta cost vs GDSF: 1021610
- Expiry-induced cold starts: 5752
- Eviction-induced cold starts: 546
- Other cold starts: 66
- Expiry-induced cost: 2045660
- Eviction-induced cost: 201650
- pressure > p_deactivate ratio: 3.52%
- eviction request ratio: 0.03%
- TTL delete scan ratio: 28.85%
- mean / p50 / p95 utilization: 0.575 / 0.546 / 0.944
- TTL deletes at util <= 0.85: 95.55%

## 对 tau_base 的说明
- 当前 dtlm.py 不存在 tau_base 计算、上下限 clamp 或 cap 命中逻辑。
- 当前实现是基于最近 1 小时调用次数的三档离散 TTL：hot / warm / cold。
- 因此本报告把 tau_base_distribution 解释为 effective TTL distribution，并明确标注这不是连续 tau_base。