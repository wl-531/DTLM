# Paper B — 思路文件

**When Should a TTL Actually Delete? Pressure-Gated Coordination of Idle Expiry and Capacity Eviction in Edge Serverless**

状态：路径B收口，冻结 v3.1，进入论文级实验与写作 | 更新：2026-03-17（v3.3，文献扫描后修订）

---

## 一、问题定义

### 1.1 背景与痛点

边缘 serverless 平台上，函数容器实例离开内存有两种触发原因：空闲超时回收（idle-timeout expiry）和内存压力驱逐（pressure-driven eviction）。围绕这两类触发，现有文献形成了三类研究路线：

- **TTL-only 路线**：Serverless in the Wild (ATC 2020)、平台默认（AWS Lambda/OpenWhisk ~10min）。设定 per-function TTL 策略，但不建模有限内存容量——假设内存弹性或通过请求路由转移容量压力。
- **Eviction-only 路线**：FaasCache (ASPLOS 2021)、CIDRE (ASPLOS 2025)、FaasCamp (2024)。用 GDSF 等评分策略完全替代 TTL，容器无限期驻留直到被内存压力挤出。
- **Retention-aware / Cost-aware 统一路线**：Pan et al. C2RD (INFOCOM 2022)、S-Cache (EdgeSys 2023)、OnCoLa (TPDS 2025)、以及一般缓存理论中的 TTL + eviction / ski-rental 组合抽象 (CIDR 2025)。这些工作从 retention-aware caching 或 cost-aware caching 角度联合处理冷启动与容量成本，将 keep-alive time、cold-start cost、container size 等因素放入统一评分或统一约束优化中。

**第三类工作已经覆盖了"联合优化冷启动与容量"这一层面。** 但它们的共同特点是将 TTL/retention 和 eviction 融合为一个统一的缓存决策（统一评分、统一约束），而不是将 idle-timeout expiry 和 pressure-driven deletion 作为两条可独立观测、可能冲突的系统触发链来诊断和控制。

**本文的出发点**：在边缘 serverless 的硬内存约束下，将这两类触发简单融合为统一缓存决策是否足够？还是说，它们的交互会产生不被统一抽象预见的失败模式？本文通过系统性的 design-space exploration 实证回答这一问题，并提出一个可证伪的协调原则。

### 1.2 理论锚点

Elsayed, Geyer & Rizk, *"Utility-driven Optimization of TTL Cache Hierarchies"* (IFIP Networking 2024)。该工作提出 TTLmin,extnd 策略——TTL 到期时检查缓存是否满再决定是否移除，eviction 选择剩余 TTL 最短的对象——在 CDN/内容缓存中实现了 <1% 偏差于解析最优。但该工作面向 CDN，未处理 serverless 容器的异构大小、异构冷启动代价等因素。

### 1.3 本文定位

**创新套路：套路1（知识搬运工）+ 套路2（修改创新）**。

迁移层面：将 CDN 缓存理论中的 TTL-eviction 协调思路迁移到边缘 serverless 容器缓存，适配 variable container size、heterogeneous cold-start cost 等 serverless-specific 复杂性。

修改层面：不是直接复用已有的统一缓存抽象，而是通过 failure diagnosis 识别统一抽象在 serverless 边缘场景下的失效模式，并提出针对性的协调原则（pressure gating）。

---

## 二、形式化

- **系统**：单边缘节点，内存预算 M（MB），硬约束
- **函数集**：N 个异构 serverless 函数，函数 i 的参数：容器内存占用 m_i（MB），冷启动代价 c_i（ms）
- **目标**：min Σ_t c_{f(r_t)} · 1[cold start at t]，s.t. Σ_{Warm(t)} m_i ≤ M
- **实例生命周期**：冷启动→活跃服务→空闲→（idle-timeout expiry | pressure-driven eviction）→冷状态
- **双过程形式化**：将 idle-timeout expiry 和 pressure-driven eviction 显式建模为两条独立但可协调的触发链，每条链有独立的触发条件、独立的受害者选择逻辑、以及可观测的交互效应

---

## 三、贡献结构（已冻结）

| # | 贡献 | 内容 |
|---|------|------|
| C1 | 显式双过程形式化 | 面向 edge serverless，将 idle-timeout expiry 与 pressure-driven eviction 作为两条可观测、可诊断、可协调的失效过程进行形式化，区别于已有工作将二者融合为统一缓存决策的处理方式 |
| C2 | 失效模式诊断与协调原则 | 通过 design-space exploration 识别 serverless-specific 的关键失败模式：naive TTL overlay 在低压状态下产生有害 physical deletion（low-pressure deletion pathology）。提出 pressure-gated logical-expiry 原则，将"逻辑过期判断"与"物理删除执行"解耦 |
| C3 | 系统实证 | Azure Functions trace 驱动仿真，系统比较 TTL-only / eviction-only / cost-aware unified / naive bridge / 朴素组合 / DTLM，给出冷启动分解、分歧度、利用率过程、函数级归因等解释性分析，证明 C2 在正确的状态下起作用 |

**贡献定位说明**：

本文不主张"首次联合建模 TTL 与 eviction"——已有 retention-aware/cost-aware 工作已从约束优化或统一评分角度覆盖了这一层面。本文的新意在于：(1) 将两类触发显式区分为两条过程而非融合为一个决策；(2) 通过系统性反面案例证明融合抽象在 edge serverless 下的失效模式；(3) 提出一个可证伪的协调原则并用解释性实证支撑。

论文重心在 **C2（失效模式诊断 + 协调原则）** 和 **C3（系统实证 + 解释性指标）**，C1 是支撑前两者的建模基础设施。

---

## 四、设计空间探索（支撑 C2 的实证推导链）

这一节将作为论文的核心叙事之一，展示"反面案例→失败模式识别→设计原则"的推导过程。其核心论证目标是：已有的统一缓存抽象为什么在 edge serverless 场景下不够，TTL 层和 eviction 层的交互为什么需要显式协调。

### 4.1 DTLM v1：高压自伤

- **机制**：TTL 层在内存压力高时压缩 TTL，主动回收容器
- **失败模式**：高压时 TTL 层疯狂回收，内存反而没用满（利用率仅 85.7%），性能比 LRU 还差
- **诊断**：TTL 层在帮倒忙——主动回收的速度超过了缓存再充填的速度

### 4.2 DTLM v2：低预算失效

- **修复尝试**：高压时让 TTL 层完全不回收，把决定权交给 eviction 层
- **新失败模式**：低预算下 pressure 几乎永远超过阈值，TTL 层永久"装死"，DTLM 退化为纯 eviction 策略；自创 eviction 评分不如 GDSF
- **另一问题**：τ_base 因 EMA_IAT × multiplier 大面积打到 30 分钟上限，TTL 层彻底失去区分度

### 4.3 DTLM v3：地板正确，边界没关住

- **设计转向**：eviction 层直接复用 GDSF，不再自创评分。核心保证：最坏情况 DTLM v3 = GDSF
- **残余问题**：M=1.0 时 total cost 是 GDSF 的 1.82x。诊断确认：95.55% 的 TTL 删除发生在 util ≤ 0.85，expiry-induced cost 占 91%
- **失败模式命名**：**low-pressure deletion pathology**——当系统不缺内存时，TTL 层仍执行物理删除，制造不必要的冷启动

### 4.4 DTLM v3.1：压力门控逻辑过期（当前版本）

- **核心修复**：TTL 持续维护 logical expiry 判断，但只有 pressure > p_deactivate 时才允许 physical deletion
- **设计原则**："逻辑过期判断"与"物理删除执行"解耦。TTL 可以一直计时、一直更新，但删除动作只在系统需要空间时执行
- **结果**：M=1.0 从 1.82x GDSF 降到 0.945x GDSF；低 M 无回归；M=0.3 反而有改善

### 4.5 连续 τ_base 试探（已终止）

曾进行一次受控试探，目标是验证连续 τ_base（基于 per-function IAT 分位数）是否能在不破坏 v3.1 guardrail 的前提下，稳定提升 0.3–0.7 区间表现并显著降低 cold-floor 饱和。

**结果**：连续 τ_base 未能有效展开分布，破坏 M=1.0 guardrail，且在 0.3–0.7 区间全面劣于 v3.1。三条止损条件均未满足，该方向已终止，不作为本文贡献来源。

**此负结果反向支撑 C2**：pressure gating 的价值不依赖于 TTL 层的精细程度；即使 TTL 层是粗粒度的三档离散规则，只要 physical deletion 被正确门控，就能避免关键失败模式。

*这一迭代过程本身就是 C2 的核心证据：通过系统性的反面案例展示已有统一抽象的不足，以及为什么需要显式的跨过程协调（pressure gating）。*

---

## 五、算法设计（DTLM v3.1）

### 5.1 Eviction 层

直接复用 GDSF (Greedy-Dual-Size-Frequency)。当内存压力触发 eviction 时，驱逐评分最低者。不做任何修改——这是地板保证的来源。

### 5.2 TTL 层

- **三档离散 TTL**：基于函数调用频率分为 hot / warm / cold 三档，各档对应不同的 TTL 值
- **逻辑过期**：TTL 计时器持续运行，达到阈值时标记 logical expiry，但不执行删除
- **Pressure gate**：只有当 pressure > p_deactivate (0.95) 时，逻辑过期的实例才被物理删除
- **跨层耦合**：TTL 层的物理删除为 eviction 层减压；eviction 事件反馈压力信号给 TTL 层

### 5.3 关键参数

| 参数 | 值 | 说明 |
|------|---|------|
| p_deactivate | 0.95 | pressure 超过此阈值时 TTL 物理删除才被允许 |
| τ_cold | 120,000 ms | cold 档函数的 TTL |
| τ_warm / τ_hot | 待确认 | 基于 Step 3 参数扫描结果 |

---

## 六、实验设计

### 6.1 数据

Azure Functions Trace 2019，14 天，~19,700 apps，~72,000 functions，2.75B invocations。使用强制尾部抽样方案（N=50 apps）模拟单边缘节点负载，working set ~5.3 GB。抽样方案已通过三项特征验证（Zipf 重尾、IAT 双峰、内存变幅）。

### 6.2 Baselines（8 个）

| Baseline | 定位 | 文献分类 |
|----------|------|----------|
| Fixed-TTL-10min + LRU | 工业默认 | TTL-only |
| IAT-Adaptive TTL (no capacity) | 纯 TTL 策略代表，per-function TTL = 2×EMA_IAT，无容量约束，SitW 简化版。与 Pan et al. C2RD 无关 | TTL-only |
| GDSF (FaasCache) | 纯 eviction 策略代表 | Eviction-only |
| S-Cache priority | Cost-aware unified 策略代表（Clock + Freq×ColdStartTime/Size） | Retention/cost-aware unified |
| LRU | 经典缓存 | 通用基线 |
| LFU | 经典缓存 | 通用基线 |
| TTLmin,extnd (naive bridge) | 理论锚点，最接近的 TTL-eviction bridge，关键比较对象 | 理论 bridge |
| IAT-Adaptive TTL + LRU（朴素组合） | 消融对照，证明耦合 > 并排 | 消融 |

**S-Cache 新增说明**：文献扫描识别 S-Cache (EdgeSys 2023) 为 cost-aware unified 路线在 edge serverless 中的代表。将其加入 baseline 可直接回应"DTLM 与 cost-aware caching 有何差异"这一必然的 reviewer 问题。其优先级公式与 GDSF 同族但不同，提供了 eviction-only 阵营之外的另一个统一策略比较点。

### 6.3 主实验参数

与 v3.1 冻结版本完全一致：

| 参数 | 取值 |
|------|------|
| Memory budget M | working set 的 {10%, 20%, 30%, 50%, 70%, 100%}，即约 530MB–5.3GB |
| Cold-start 代价 c_i | ATC 2018 校准值 × {0.5, 1.0, 2.0} |
| p_deactivate | {0.85, 0.90, 0.95, 0.98}（已通过 Step 3 选出 0.95） |
| τ_cold / τ_warm / τ_hot | 基于 Step 3 参数扫描确定的离散档位值 |

注：连续 τ_base 分位数扫描和 decay 函数形状（linear / exponential / step）属于设计空间探索阶段的已否证试探，相关数据纳入第四节 design-space exploration，不作为主实验参数。

### 6.4 核心输出

**Cost vs M curve**：横轴 M/working_set_size，纵轴 total cold-start cost，每条线一个策略。

### 6.5 解释性指标（四组，论文级）

以下四组指标是 C3 的核心支撑，对所有策略统一采集。

#### 组 1：冷启动分解

- cold starts 分为 expiry-induced 和 eviction-induced，分别统计次数与 cost
- 对 eviction-only 策略，所有 cold start 均为 eviction-induced（对照基线）
- **核心作用**：证明 TTL 层是在帮忙还是在制造二次 miss

#### 组 2：与 GDSF 的分歧度

- 同一时刻，DTLM 缓存中有而 GDSF 没有的函数集合（及反向）
- 分歧状态发生的 M 区间和时间比例
- 分歧状态带来的 cost 变化（正贡献 vs 负贡献）
- **实现注意**：需要同时运行两个策略实例并逐时刻对比，或按固定时间间隔采样快照

#### 组 3：利用率与删除时机

- 平均内存利用率、P5/P50/P95 利用率
- 删除发生时的 pressure/利用率分布
- **核心作用**：展示"低压误删"与"高压释放"的分布差异

#### 组 4：函数级归因

- top harmful functions：DTLM 相对 GDSF 增加了 cost 的函数
- top beneficial functions：DTLM 相对 GDSF 减少了 cost 的函数
- 分析这些函数的特征（调用频率、内存占用、冷启动代价）
- **核心作用**：说明机制主要影响哪类函数，与数据特征闭合

---

## 七、前置数据验证摘要

全量 profiling 和子集抽样已完成，关键结论如下：

| 维度 | 全量 | 子集 (N=50) | 对 framework 的启示 |
|------|------|------------|-------------------|
| 内存异质性 | max/min = 54.7x | max/min = 14.1x (>10x ✓) | TTLmin,extnd 的 uniform-size 假设失效 |
| 调用频率 | top 1% 占 93% | top 1% 占 96.9% | 策略差异主要在低 M |
| IAT 分布 | 双峰：热 6% / 冷 47% | 热 2.6% / 冷 52% | TTL 分级有合理性 |
| Working set | ~1.7 TB | ~5.3 GB | 符合边缘场景 |

**已识别的注意事项**：

- **Zipf 极端性的双刃剑**：任何合理策略只要缓存住热函数就能达到很高命中率。**策略差异主要体现在 M=10%–30%**，此时连热函数都放不下。论文中应主动拿出数据特征说明这一点，将其转化为叙事优势而非弱点。
- **子集 IAT 冷尾巴较短**：117 个函数中极低频函数相对较少，TTL 层可能主要处理分钟级差异。如 TTL 层贡献不显著，可用 N=100 子集做补充。
- **冷启动代价需外部校准**：基于 Wang et al. (ATC 2018) 实测数据构造 c_i，并做 ×0.5 / ×2.0 敏感性分析。

---

## 八、当前状态与决策

### 8.1 已完成

- 前置数据验证（全量 profiling + 子集抽样 + 三项特征验证）
- DTLM v1→v2→v3 迭代与诊断
- v3 M=1.0 诊断（四个量），确认低压 TTL 过删
- v3.1 实现与验证（guardrail 修复）
- 连续 τ_base 受控试探（已终止，纳入 design-space exploration 负结果）
- 文献否证扫描（v3.3 修订纳入结果）
- 回归测试通过

### 8.2 Go/No-Go 标准对照

| 标准 | 状态 | 备注 |
|------|------|------|
| 对 GDSF 不稳定劣化 | ✓ 通过 | M=1.0 修复后 0.945x GDSF；已测 budget 点未出现明显劣化，但尚未覆盖所有 cost scaling 和随机种子 |
| 低到中预算持续收益 | 待验证 | 需补齐四组解释性指标 |
| TTL 层真在工作 | 待验证 | 三档离散 + cold-floor 饱和，需解释性数据 |
| 收益来源可解释 | 待验证 | 冷启动分解和函数级归因将回答 |

### 8.3 关键决策

**路径B 收口，冻结 v3.1 为论文主版本。** 不再改算法，下一步是补齐论文级实验和写稿。

**路径A（连续 τ_base）已终止。** 经受控试探，三条止损条件均未满足，已终止。此负结果纳入第四节 design-space exploration。

**叙事校准（v3.3）。** 基于文献否证扫描结果，论文卖点从"联合建模"转向"失效模式诊断 + pressure-gated 协调原则 + 系统实证"。C1 从"统一建模"降级为"显式双过程形式化"，明确承认已有 retention-aware/cost-aware 工作的覆盖。

---

## 九、接下来的执行计划

### 9.1 立即执行：S-Cache baseline 实现 + 四组解释性指标

1. 实现 S-Cache 优先级公式（Clock + Freq×ColdStartTime/Size）作为第 8 个 baseline
2. 在仿真框架中对所有策略（含 S-Cache）统一采集四组解释性指标（见第六节 6.5）

重点注意：
- 组 2（分歧度）可能需要并行运行两个策略实例，或采用快照采样近似方案
- 所有策略都要采集（eviction-only 策略的 cold start 全为 eviction-induced，作为对照）

### 9.2 然后：论文框架写作

建议的论文结构：

- **Introduction**：已有统一缓存抽象在 edge serverless 下是否足够？两类触发的交互是否会产生统一抽象未预见的失败模式？
- **Background & Related Work**：三类文献路线（TTL-only / Eviction-only / Retention-aware unified）+ TTLmin,extnd 理论锚点。明确承认第三类已覆盖联合优化层面，定位本文新意为 failure diagnosis + coordination principle
- **System Model**：形式化 + 显式双过程容器生命周期模型（C1）
- **Design Space Exploration**：v1/v3 反面案例 → low-pressure deletion pathology → pressure gating 原则 + 连续 τ_base 负结果（C2）
- **DTLM Algorithm**：完整算法描述（GDSF eviction + pressure-gated logical-expiry）
- **Evaluation**：Cost vs M + 四组解释性指标（C3）
- **Conclusion**

### 9.3 最后：投稿

**目标 venue**：B 类会议（GLOBECOM 2026 截止日 7/15，ICNP 2026 截止日 5/22）

时间评估：S-Cache 实现 + 四组解释性指标需 3–4 天，论文写作需 2–3 周。ICNP（5/22）较紧但可行，GLOBECOM（7/15）充裕。

---

## 十、剩余风险

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| Reviewer 认为这只是 cost-aware caching 的再包装 | 中高 | Design-space exploration 展示统一抽象的失效模式；S-Cache baseline 正面对比；C3 解释性指标证明跨过程协调带来的差异 |
| TTL 层贡献微弱，看起来只是 GDSF + 开关 | 中 | C3 四组解释性指标提供机制级证据；论文定位为系统实证而非新算法 |
| TTL 收益窗口窄（只在中 M） | 中 | 前置 profiling 已预测这一点；论文中主动拿出数据特征说明，转化为优势 |
| 连续 τ_base 试探失败后 TTL 层仍为三档离散 | 低（已降级） | 路径B 不以连续 τ 为贡献点；负结果已纳入 design-space exploration |
| 冷启动代价校准不确 | 低 | ×0.5 / ×2.0 敏感性分析覆盖 |
| 时间风险（ICNP 5/22） | 中 | S-Cache + 四组指标 3–4 天，写作 2–3 周；紧但可行，GLOBECOM 为备选 |

---

## 十一、文档索引

| 文档 | 内容 | 状态 |
|------|------|------|
| 本思路文件 v3.3 | 完整框架（问题/贡献/算法/实验/决策），文献扫描后修订 | 当前版本 |
| 文献扫描报告（初版） | FaasCache / SitW / Pan et al. / Elsayed et al. 精读 | 完成 |
| 文献否证扫描报告 | INFOCOM 2022 / S-Cache / OnCoLa / KiSS / CIDR 2025 覆盖度评估 | 完成（v3.3 已纳入） |
| Trace Profiling 结果 | 全量统计 + 子集抽样 + 三项验证 | 完成 |
| v3 诊断报告 | results/dtlm_v3_diagnosis_m1/ | 完成 |
| v3.1 实验报告 | results/dtlm_v3_1/ | 完成 |
| 连续 τ_base 试探报告 | 受控试探结果，三条止损条件均未满足 | 完成（已终止） |
| 四组解释性指标 | 待实现 | 下一步 |
| S-Cache baseline 实现 | 待实现 | 下一步 |
| 论文草稿 | 待写 | 下下步 |

---

## 十二、文献参考锚点

### TTL-only 路线
- Shahrad et al., "Serverless in the Wild" (ATC 2020)
- 平台默认：AWS Lambda / OpenWhisk ~10min TTL

### Eviction-only 路线
- Fuerst & Sharma, "FaasCache" (ASPLOS 2021)
- Hu et al., "CIDRE" (ASPLOS 2025)
- FaasCamp (2024)

### Retention-aware / Cost-aware 统一路线
- Pan et al., "Retention-Aware Container Caching for Serverless Edge Computing" C2RD (INFOCOM 2022)
- S-Cache (EdgeSys 2023)
- OnCoLa (TPDS 2025)
- KiSS (arXiv 2025)
- CIDR 2025, "Linear Elastic Caching via Ski Rental"（一般缓存理论中的 TTL+eviction 组合抽象）

### 理论锚点
- Elsayed, Geyer & Rizk, "Utility-driven Optimization of TTL Cache Hierarchies" (IFIP Networking 2024)

### 冷启动数据源
- Wang et al., "Peeking Behind the Curtains of Serverless Platforms" (ATC 2018)

### 数据集
- Azure Functions Trace 2019
