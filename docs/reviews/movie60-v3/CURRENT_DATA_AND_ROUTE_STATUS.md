# Movie60 当前数据、评分与 Agent 路线状态

更新时间：2026-08-21。

本页专门回答三个容易混淆的问题：当前图片数据集是哪一版、打开 Release 时看到的是哪一版评分、为什么 Agent 在最终代理留出集上不如 Rule。

## 1. 结论先行

1. 当前 Movie60 图片集合仍是 `movie-visual-60-v1@1.0.0`。它包含 60 个 Source、60 个 1536×1536 Task，四种场景各 15 张；图片像素和传统七候选没有新增 v2 数据集。
2. 当前最新私有交付包是 GitHub Pre-release `movie60-review-v2`，目标提交为 `e605eb4`。这里的“v2”是交付包版本，不是 Dataset 版本，也不是 Rule 版本。
3. 当前部署策略是 `movie60@3.2.2`，策略 SHA-256 为 `49a74b7132b0efe8cf4b014644db7a56d77b8820df1d21b4388d0a33e81ecd73`。
4. Release 中 `all60/summary.csv`、`all60/candidate-review.csv`、`all60/review.csv` 的机器列和 `rules-v1/` 仍属于旧 Movie60 评分/评审工作区口径。它们没有被 v3.2.2 结果覆盖。
5. 最新 v3.2.2 Rule 和 Agent 证据在仓库 `docs/reviews/movie60-v3/`；Release 内的高清 Agent 证据在 `v3-agent-evidence/movie60-v3-2-2-release-evidence/`。读取最新结论时应使用这些文件，而不是把 `all60` 的旧 `machine_grade` 当成 v3.2.2。
6. v3.2.2 最终采用“Rule 主决策、Agent 仅建议”。原因不是 Agent 在所有指标、所有版本都更差，而是它在冻结的 15 Task 代理留出集上显著退化，未证明能安全覆盖 Rule。

## 2. 四种版本号分别代表什么

| 层 | 当前标识 | 含义 | 是否改变图片像素 |
|---|---|---|---:|
| Dataset | `movie-visual-60-v1@1.0.0` | 60 张原图、场景、split、目标尺寸 | 是，只有换 Dataset 才改变原图集合 |
| Generation Run | Movie60 七方法冻结 Run | 60×7=420 张传统候选 | 是，换算法/参数才改变候选 |
| Strategy | `movie60@3.2.2` | Rule 权重、阈值、门禁、Prompt、Skill、插件 | 否，可在冻结候选上重放评分 |
| Release | `movie60-review-v2` | 面向内部评审的打包版本 | 否，只决定包里收录什么 |

因此“Release v2”不能推导出“Dataset v2”或“Rule v2”。判断某个数字属于哪版，必须检查：

- Dataset：`dataset/dataset.yaml`；
- Rule：Evaluation 的 `strategy/` 快照、`strategy_version` 和 `strategy_sha256`；
- Agent：`agent-run-id`、`strategy_version`、Prompt/Skill hash；
- Release：GitHub tag 和 `SHA256SUMS.txt`。

## 3. 当前数据分母与完整性

| 项目 | 当前值 |
|---|---:|
| Source | 60 |
| Task | 60 |
| 每 Task 目标 | 1 个，1536×1536 |
| 每 Task 传统方法 | 7 |
| Candidate | 420 |
| Calibration | 20 Task |
| Validation | 40 Task |
| 人物图 | 15 |
| 电影海报 | 15 |
| 影视剧照 | 15 |
| 视频封面 | 15 |

当前真实人工记录不是 420 条全部完成：Release v2 打包时的进度为逐候选 126/420、Top1 兼容表 10/60、Focus20 路线 0/20。未填写行表示待评，不表示失败。

v3 开发使用的 420 条标签是“人工粗审认可的大模型代理建议”：294 条盲评建议、56 条同集校准建议、70 条回顾性 UI 补全建议。它们按 `task_id + method + image_sha256` 对齐，但不是独立人工金标。

## 4. 看到不同文件时应如何解释

| 文件/目录 | 评分口径 | 是否最新部署评分 | 用途 |
|---|---|---:|---|
| Release `all60/summary.csv` | 旧最终工作区机器选择 | 否 | 浏览旧交付结果与人工入口 |
| Release `all60/candidate-review.csv` | 旧 Rule 排名、旧 Agent/回顾性建议和人工列的组合 | 否 | 继续人工逐候选评审 |
| Release `rules-v1/` | 产生旧 60 张主表的冻结规则 | 否 | 历史追溯 |
| `rule-development-results.json` | v3/v3.1/v3.2 对 315 候选的代理开发结果 | 是，开发证据 |
| `rule-proxy-holdout-results.json` | 冻结 v3.2 对 105 候选的一次代理留出结果 | 是，候选级留出证据 |
| `agent-v3-2-2-development/report.json` | v3.2.2 的 45 Task Rule/Agent/Combined 对比 | 是，开发证据 |
| `agent-v3-2-2-proxy-holdout/report.json` | v3.2.2 的 15 Task 部署选择证据 | 是，最终代理留出证据 |
| `deployment-freeze.json` | 当前部署路线和策略哈希 | 是 | 机器可解析的最终决策 |

高风险提醒：当前 Release 把旧 `all60` 主评分与新 v3.2.2 策略/证据同时放入一个包，如果只看目录名，容易误把旧 `machine_grade` 当成当前策略输出。数据没有损坏，但展示口径存在版本歧义。

下一版 Release 应二选一：

1. 用 v3.2.2 对 420 候选生成新的 UI 主表，并在每行写入 `strategy_version` 与 `strategy_sha256`；或
2. 明确把现有目录改名为 `legacy-v1-review/`，新增 `current-v3.2.2/`，不再使用无版本的 `all60/machine_grade`。

在完成该迁移前，人工仍可继续使用 `all60` 查看图片和填写评分，但不能用其中的机器列回答“v3.2.2 准确率是多少”。

## 5. Agent 是否每个版本都不如 Rule

不是所有指标、所有版本都更差，但 Agent 的等级判断一直明显弱于 Rule；开发集上出现过轻微选图收益，最终留出集没有复现。

### 5.1 45 Task 代理开发集

| 版本/路线 | 精确等级一致 | A+B/C+D 一致 | 所选候选为代理 A/B | 最佳方法命中 |
|---|---:|---:|---:|---:|
| v3 Rule | 84.44% | 93.33% | 91.11% | 91.11% |
| v3 Agent | 46.67% | 64.44% | 93.33% | 88.89% |
| v3.1 Rule | 77.78% | 88.89% | 91.11% | 91.11% |
| v3.1 Agent | 44.44% | 71.11% | 95.56% | 91.11% |
| v3.2 Rule | 77.78% | 91.11% | 93.33% | 93.33% |
| v3.2 Agent | 44.44% | 66.67% | 97.78% | 93.33% |
| v3.2.1 Combined | 77.78% | 91.11% | 97.78% | 95.56% |

开发集上，Agent 在“挑到一张代理 A/B”方面曾比 Rule 高 2.22～4.45 个百分点，但 Agent 自己给 A/B/C/D 的精确度明显更差。v3.2.1 因而把“选哪张”和“判什么等级”拆开：Agent 可提议候选，最终等级取候选的 Rule metric。

### 5.2 15 Task 冻结代理留出集

| 路线 | 精确等级一致 | A+B/C+D 一致 | 所选候选为代理 A/B | 最佳方法命中 |
|---|---:|---:|---:|---:|
| Rule-only | 60.00% | 86.67% | 100.00%（15/15） | 86.67% |
| Agent-only | 33.33% | 73.33% | 80.00%（12/15） | 60.00% |
| v3.2.1 Combined | 53.33% | 86.67% | 86.67%（13/15） | 66.67% |
| v3.2.2 部署 | 60.00% | 86.67% | 100.00%（15/15） | 86.67% |

这组结果推翻了开发集上的 Agent 选图收益：Agent-only 相比 Rule 少保留 3 张代理 A/B，Combined 少保留 2 张。因此 v3.2.2 将 Agent 改为 `advisory_only`，最终选图和等级均保留 Rule。

注意：这里的留出集用于三路线选型后，已经不再是未来版本的独立验证集；且标签是代理标签，不可写成真实人工准确率。

## 6. Agent 不如 Rule 的具体原因

### 6.1 Agent 擅长的任务和本轮评价任务并不完全一致

视觉模型擅长描述显著画面差异，但本项目要求在七张非常相似的候选中发现细微文字、人数、Logo、局部网格或 seam 形变。Rule 可以直接读取 OCR 字符、检测计数和 Transform 风险的精确数值；Agent 必须从缩略总览或有限高清局部重新推断。

### 6.2 总览阶段容易提出“没有明确增益”的 challenger

候选经常像素相同或差异很小，Agent 仍会为满足排序任务换成另一个方法。例如代理留出中的：

- `person_008`：Rule 选 crop，代理标签 A；Agent 换 mesh_full，代理标签 B，但理由仍写“所有关键维度无缺陷，与源图完全一致”。
- `poster_005`：Rule seam 与 Agent mesh 都是代理 B；Agent 说“无任何缺陷”并给 A，没有证明换图带来收益。
- `video_cover_010`：总览 Schema 无效，Agent 仍从 seam_scale 换到 mesh_full；两者代理标签都为 B，增加决策风险但没有收益。

这说明“必须排出一个更好候选”的任务形式会诱导模型过度区分近似图。

### 6.3 严格等级偏保守，且精确率不足

v3.2 开发集 Agent 的 C/D 召回达到 100%，但 C/D 精确率仅 6.25%。也就是说它几乎不漏掉代理 C/D，却把很多代理 A/B 误报为 C/D。

典型例子：

- `poster_010`：Agent 选 seam，候选代理 B，却因 OCR 低把自身等级判 C；
- `still_014`：Agent 选 crop，候选代理 B，却声称主标题和 Logo 部分缺失并判 C；
- `still_006`：Agent 选 direct_warp，候选代理 B，却根据 stretch 先验判 D；
- `poster_011`：Agent 理由称“无任何语义缺失、局部形变或不可读文字”，但所选 mesh 的代理标签是 C，出现理由、候选质量和置信度不一致。

Agent 既看像素又看 Rule/Transform 证据时，有时会把“风险指标”直接翻译成“肉眼已发生严重缺陷”，导致过度降级。

### 6.4 Rule 已经吸收了同集代理偏好

v3/v3.1/v3.2 的权重和门禁是在 45 Task 代理开发集上针对相同标签体系迭代出来的。Rule 因此拥有更强的同分布先验；Agent 是通用 4B 视觉模型，即使 Skill 有案例，也没有经过等价规模的参数训练。

这不证明 Rule 更接近真实用户，只证明它更贴合当前代理标签。必须等待新的独立人工 Validation 才能比较真实业务表现。

### 6.5 小样本和标签来源限制了结论

- 开发集只有 45 Task；
- 留出集只有 15 Task；
- 420 条候选标签本身来自大模型建议，经人工粗审认可，不是逐候选独立人工金标；
- 同一数据集里有大量近似候选，Task 间有效差异小于 420 这个表面分母。

因此开发集的 2～4 个百分点选图增益很容易在 15 Task 上反转。

### 6.6 结构化输出和时延不是完全稳定

- v3 总览 Schema 有效率 100%；
- v3.1 为 97.78%；
- v3.2 开发集为 91.11%；
- v3.2 代理留出为 93.33%。

留出集高清复核平均 wall time 约 196.47 秒/Task，高于策略软目标 120 秒。Schema 失败会触发安全回退，但也说明当前链路不适合无门禁地掌握最终选择权。

## 7. 当前处理方案为什么合理

v3.2.2 没有删除 Agent，而是把能力放在更合适的位置：

1. Rule 生成可回放的七候选完整排名和最终 Top1；
2. Agent 读取原图、完整排名、Top1 和最多两个 challenger；
3. 高清图和文字/人物/商品局部用于发现语义变化与不物理缺陷；
4. Agent 输出中文建议、证据和置信度；
5. 最终自动选图和等级仍由 Rule 给出；
6. 人工纠错进入下一版 Skill、Prompt、Detector 或 Rule，而不是覆盖旧记录。

这使 Agent 的视觉理解仍能帮助人工发现 Rule 盲区，同时避免把已观察到的不稳定选图直接用于生产。

## 8. 下一步验证建议

1. 完成剩余逐候选人工评分，形成真正的人工标签表；
2. 从未参与代理开发的新图片中冻结一个独立 Validation；
3. 预先声明三路线和指标，再运行 Rule-only、Agent advisory、受门禁 Combined；
4. Agent 不应被强迫在近似候选中必选不同 Top1，应允许 `KEEP_RULE`；
5. 只有出现可定位的像素证据，才允许 challenger；
6. 视觉缺陷等级与候选选择分开输出；
7. 对文字框、人体关键点、Logo 主体性和 rigid-object 形变增加确定性中间证据；
8. 下一版 Release 把每条展示分数与 `strategy_version`、`strategy_sha256` 绑定。

## 9. 事实源

- Dataset 合同：`datasets/movie_visual_60_v1/README.md`；
- Release：`movie60-review-v2`；
- 标签边界：`EVIDENCE_AUDIT.md`；
- Rule 开发：`RULE_DEVELOPMENT_REPORT.md`；
- 候选级代理留出：`RULE_PROXY_HOLDOUT_REPORT.md`；
- Agent/Combined：`AGENT_COMBINED_REPORT.md`；
- 部署冻结：`deployment-freeze.json`。
