# StrategyBundle：可插拔、不可变、可追溯的评分与 Agent 策略

## 1. 一个 Bundle 管什么

每个版本目录是一个自包含 Module：

```text
strategies/movie60/v2/
├── bundle.yaml       版本、父版本、状态和四个引用
├── scoring.yaml      Quality 公式权重、A/B/C/D 范围、OCR 与形变门槛
├── selection.yaml    Rule 完整排名顺序和 Agent 触发阈值
├── override.yaml     Rule Top1 被 Challenger 覆盖的高清证据门禁
└── agent-skill.yaml  大模型阅读图片、排序和说明理由的冻结 Skill
```

调用方只学习一个 Interface：`--strategy <bundle.yaml>`。加载、字段校验、安全路径、文件哈希
和 Run 内快照都隐藏在 `retarget_agent.strategy` 的实现中。

## 2. A/B/C/D 分数范围可配置

`scoring.yaml` 中：

```yaml
proxy_a_threshold: 90.0
proxy_b_threshold: 72.0
proxy_c_threshold: 60.0
```

解释为：

```text
A: score >= 90
B: 72 <= score < 90
C: 60 <= score < 72
D: score < 60
```

例如下一版本经人工校准后希望改成 `A>=80、B>=70、C>=60、D<60`，只修改新版本目录的三个
阈值，不修改 Python。硬失败、关键文字丢失、显著人脸消失等门禁仍可把高分候选降级；连续
分数和风险门禁是两个不同概念。

## 3. 正确创建 v3

严禁直接编辑已经进入 Run 的 v1/v2。复制当前版本为新目录：

```powershell
Copy-Item strategies\movie60\v2 strategies\movie60\v3 -Recurse
```

然后只在 v3 中：

1. 把 `bundle.yaml` 的 `version` 改为 `3.0.0`；
2. 把 `parent_strategy` 设为 `movie60@2.0.0`；
3. 修改 `scoring.yaml`、`selection.yaml`、`override.yaml` 或 `agent-skill.yaml`；
4. 各子策略同步使用新的 `policy_id/version`；
5. 执行 `strategy show` 和 `strategy diff`；
6. 在 `strategies/registry.yaml` 追加 v3 与实际 SHA-256，绝不改写 v1/v2 的哈希；
7. 先跑 Calibration 20，冻结后再对 Validation 40 只跑一次。

```powershell
.\.venv\Scripts\retarget-engine.exe strategy show strategies\movie60\v3\bundle.yaml
.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v2\bundle.yaml strategies\movie60\v3\bundle.yaml
```

## 4. Run 内快照

使用 StrategyBundle 后，派生产物保存：

```text
evaluations/<evaluation_id>/strategy/
agent-runs/<agent_run_id>/strategy/
strict-reviews/<review_run_id>/strategy/
```

目录包含五个原始 YAML 和 `snapshot.json`。Manifest 同时保存：

- `strategy_id`；
- `strategy_version`；
- 整体 `strategy_sha256`；
- Run 内快照相对路径。

因此仓库发展到 v5 后，打开旧 v1 Run 仍能直接看到当时的完整规则，不依赖记忆，也不需要从
聊天记录恢复 Prompt。

## 5. 允许的增量修复

| 修改 | 是否新建策略版本 | 是否重新生成七候选 |
|---|---:|---:|
| 调整 A/B/C/D 范围 | 是 | 否 |
| 调整 Quality 权重 | 是 | 否 |
| 改 Rule 排名优先级 | 是 | 否 |
| 改 Agent Skill 或中文理由规范 | 是 | 否 |
| 改覆盖门禁 | 是 | 否 |
| 修复 Seam/Mesh 算法 | 需要新方法版本和新 Run | 是 |
| 更换 OCR/YOLO 模型 | 需要新分析版本和新 Run | 是 |

评分和选择修复是 Evaluation/Agent 层增量；像素算法或保护分析改变才需要新 Generation Run。

## 6. 兼容与测试纪律

- `strategies/registry.yaml` 固定历史 Bundle SHA-256；修改旧目录会让测试失败。
- 新策略必须通过 Pydantic 严格字段校验，未知字段直接失败。
- A/B/C 阈值必须满足 `A >= B >= C`。
- 每个 Artifact ID 默认拒绝覆盖。
- v1 Golden Test 与历史 Artifact loader 必须长期保留。
- 策略差异必须进入 Changelog，不能只写“优化评分”。
