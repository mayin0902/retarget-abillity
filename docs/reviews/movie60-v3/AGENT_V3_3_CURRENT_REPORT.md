# Movie60 v3.3 当前 Rule / Agent 报告

## 结论

当前发布路线继续采用 **Rule 主选、Agent 仅提供高清语义复核建议**。本轮确实在两张
RTX 3090 上运行固定版本的 4B 视觉模型，每个任务单独读取原图、七候选 Rule 完整排名，
再对 Rule Top1 和两个 Agent Challenger 做高清候选复核与配对比较。

45 个 development Task 与 15 个 proxy-holdout Task 均完整结束；共保存 60 次有效总览、
180 份高清候选复核和 120 次配对比较。有效总览 Schema 率为 100%，Agent 自动覆盖 Rule
次数为 0。旧的完整总览 v1 曾全部 Schema 失败并回退 Rule；它被保留为失败 Run，但当前
报告和 Release 只引用由有效分片无损合并出的 v2 总览及 v4/v2 高清证据。

## 机器代理标签口径（不是人工真值）

| 分区 / 路线 | Task | 精确等级 | A+B/C+D 一致 | C/D 召回 | C/D 精确 | 代理 A+B 选择率 | 代理最佳方法命中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| development · Rule | 45 | 88.89% | 95.56% | 50.00% | 50.00% | 95.56% | 93.33% |
| development · Agent | 45 | 42.22% | 60.00% | 50.00% | 5.56% | 95.56% | 93.33% |
| holdout · Rule | 15 | 60.00% | 93.33% | 100.00% | 50.00% | 93.33% | 80.00% |
| holdout · Agent | 15 | 20.00% | 46.67% | 0.00% | 0.00% | 93.33% | 73.33% |

这里“Agent 选择率”根据 Agent 选择的候选在代理标签中的等级计算；“Agent 精确等级”则是
视觉模型自己给该候选的 A/B/C/D。结果说明 Agent 经常选择可用候选，但自己的严格等级明显
偏保守，不能把“选对图”和“等级校准”混为一谈。

## 现有真实人工标签口径

现有人工评分覆盖 18 个 Task 的全部七候选，共 126 条。12 个位于 development，6 个位于
proxy-holdout；它们不是完整独立外部数据集，因此只作为当前产品偏好的内部证据。

| 分区 / 路线 | Task | 精确等级 | A+B/C+D 一致 | C/D 召回 | C/D 精确 | 所选图人工 A+B | 人工最佳方法命中 |
|---|---:|---:|---:|---:|---:|---:|---:|
| development · Rule | 12 | 75.00% | 83.33% | 50.00% | 50.00% | 83.33% | 75.00% |
| development · Agent | 12 | 41.67% | 41.67% | 50.00% | 14.29% | 83.33% | 66.67% |
| holdout · Rule | 6 | 66.67% | 100.00% | 100.00% | 100.00% | 83.33% | 66.67% |
| holdout · Agent | 6 | 33.33% | 50.00% | 不适用 | 0.00% | 100.00% | 83.33% |
| 全部 · Rule | 18 | 72.22% | 88.89% | 66.67% | 66.67% | 83.33% | 72.22% |
| 全部 · Agent | 18 | 38.89% | 44.44% | 50.00% | 10.00% | 88.89% | 72.22% |

18 个 Task 中，Agent 选图的人工 A+B 率比 Rule 高 1 个 Task，但最佳方法命中总数相同；
development 上更差、6 个 holdout 上更好，样本不足以支持自动覆盖。Agent 自己的等级仍明显
偏严。当前最合理的使用方式是：保留 Agent 的语义、不物理变形、人物关系、Logo/主文字
判断和中文理由，供人工发现 Rule 盲区；最终方法和等级仍由可回放 Rule 决定。

## 运行与证据 ID

- Evaluation：`movie60-human-aligned-v3-3-20260821`；
- development overview：`movie60-v3-3-agent-overview-dev45-v2-20260821`；
- development strict review：`movie60-v3-3-agent-strict-dev45-v4-20260821`；
- holdout overview：`movie60-v3-3-agent-overview-holdout15-v2-20260821`；
- holdout strict review：`movie60-v3-3-agent-strict-holdout15-v2-20260821`；
- 固定模型 revision：`ebb281ec70b05090aa6165b016eac8ec08e71b17`；
- 模型服务仅监听远端 `127.0.0.1`，完成后已停止并释放本任务 GPU 占用；
- 未调用付费 AIGC API，报告中的 AIGC request 只是路由建议。

可机读逐 Task 明细位于 `agent-v3-3-development/` 与
`agent-v3-3-proxy-holdout/`。126 条人工记录在 Release 的
`all60/human-review-current.csv`，其不可变摘要见 `human-review-status.json`。
