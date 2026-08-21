# Movie60 当前数据、评分与路线状态

## 当前唯一口径

- Release：`movie60-review-v3`；
- Dataset：`movie-visual-60-v1@1.0.0`；
- Run：`movie60-square-v1-20260818`；
- Evaluation：`movie60-human-aligned-v3-3-20260821`；
- Strategy：`movie60@3.3.0`；
- Strategy SHA-256：`b09353f51bd65fd376269bbbe3196269f0276a2445a7f01f3ea71915d9fa8792`；
- 部署路线：Rule 主选，Agent 和大模型建议用于复核，不冒充人工标签。

## 数据分母

| 内容 | 数量 | 当前状态 |
|---|---:|---|
| 原图 / Task | 60 | 冻结 |
| 传统候选 | 420（60×7） | 冻结 |
| 当前 v3.3 Rule 指标 | 420 | 完整 |
| 大模型逐候选建议 | 420 | 辅助证据，不是人工金标 |
| 已有人工评审 | 126 候选 / 18 Task | 完整保留并以 SHA-256 门禁 |
| 尚未人工评审 | 294 候选 / 42 Task | 等待后续评审 |

人工等级分布为 A 47、B 32、C 33、D 14。每条已有人工记录都保留理由、问题码、确认标记、评审者和时间戳。构建器禁止机器列覆盖人工列。

## v3.3 的变化

v3.2.2 的人物/方法奖励造成 68 个候选被截断为 100 分。v3.3 删除所有场景和方法分数奖励，保留检测回归罚分与明确 C/D 门禁，等级阈值变为 89/52/42。同一冻结 Run 重放后精确 100 分从 68 降为 0，420 个候选有 392 个不同连续分数。

详细校准、人工对齐指标与分场景结果见
[`RULE_V3_3_HUMAN_THRESHOLD_REPORT.md`](RULE_V3_3_HUMAN_THRESHOLD_REPORT.md)。
当前固定 4B 视觉 Agent 的 45/15 高清复核、机器代理指标和 18 个真实人工 Task 对比见
[`AGENT_V3_3_CURRENT_REPORT.md`](AGENT_V3_3_CURRENT_REPORT.md)。

## 如何读取 Release

1. `all60/summary.csv`：每个 Task 的当前 Rule Top1 和摘要；
2. `all60/candidate-review.csv`：420 个候选的当前 Rule 分数、机器建议和人工列；
3. `all60/human-review-current.csv`：已确认的 126 条真实人工记录；
4. `all60/human-review-status.json`：人工/待评数量和防篡改哈希；
5. `all60/tasks/<task_id>/candidates/`：七张完整候选；
6. `all60/tasks/<task_id>/evidence/current-v3.3.0/`：当前版本证据；
7. `VERSION.json`：版本、分母和人工评审摘要哈希。

旧 v1/v2/v3.2.2 只在 Git 历史、旧 Release 或本机遗留索引中查找，不混入当前 `all60` 结果目录。

## Agent 的边界

Rule 先依据检测和可回放指标给出完整排名。Agent 读取原图、候选图、Rule Top1 与完整 Rule 排名，主要补充判断语义关系、明显不物理变形、主体/Logo/文字是否真实可用。当前生产选择仍由 Rule 决定；Agent 或大模型建议只有在独立人工验证后才能成为下一不可变策略版本的校准证据。

## 下一轮迭代

1. 继续在 UI 中完成人工评分，新增记录追加到独立 Review 事件或新 Release 工作区；
2. 冻结新增人工标签后再切 development/holdout；
3. 新阈值、门禁、Prompt 或 Skill 写入 `v3_4`，不修改 `v3_3`；
4. 对保留组只读出一次，记录精确等级、A+B/C+D 一致率、C/D 召回和精确率；
5. 通过后生成新 Run 内策略快照和 Release。
