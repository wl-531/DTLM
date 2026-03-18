# Continuous tau probe summary

1. guardrail 检查（硬条件）
- M=1.0 是否 <= 1.05x GDSF：否
- M=1.0 probe cost=2625720, GDSF=1249850

2. 区分度检查
- tau_base p50 / p90 / p95: 60000.0 / 60000.0 / 60000.0
- hitting tau_max ratio: 84.24%
- hitting tau_min ratio: 0.00%
- v3.1 cold-floor ratio: 80.00%
- continuous hitting tau_min ratio: 0.00%
- 是否仍有 >70% 集中在同一区间：是

3. 性能检查（核心）
- M=0.3~0.7 是否持续优于 v3.1：否
- 达标点：none

4. 最终判定
- FAIL