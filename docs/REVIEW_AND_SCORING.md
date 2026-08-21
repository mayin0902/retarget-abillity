# 人工评审与自动评分操作

## 1. 三种数据共用一个页面

```text
Movie60 私有 Release ─┐
标准 Generation Run ─┼─> ReviewWorkspaceAdapter -> FastAPI -> 浏览器页面
外部 source+candidates ┘
```

- Movie60：可切换“完整 60 张 / 重点 20 张”；
- 标准 Run：可切换“全部候选 / Rule-Agent-AIGC 路线对比”；
- 外部目录：只显示冻结图片，不伪造 Rule/Agent 分数。

## 2. 打开页面

最简单：双击根目录 `START_REVIEW.bat`。它优先打开最近的已完成/部分完成 Run；没有 Run
才打开物化好的 Movie60。

显式打开：

```powershell
# Movie60
.\.venv\Scripts\retarget-engine.exe review open `
  local_data\movie60-review-current

# 指定 Run
.\.venv\Scripts\retarget-engine.exe review open runs\<run-id>

# 最新 Run
.\.venv\Scripts\retarget-engine.exe review latest
```

浏览器默认访问 `http://127.0.0.1:8765/`。页面仅绑定本机，不提供登录鉴权；不要把监听地址
改成公司网络 IP。

## 3. 逐 Task 评审步骤

1. 先看原图：说清主人物、主标题/Logo、商品身份、关键数字和主要人物关系；
2. 在“全部候选”逐张打开高清图，不只看缩略图；
3. 看方法、Rule 排名/分数/理由；如有 Agent，再看其中文建议和置信度；
4. 只对 `available=true` 的候选给 A/B/C/D；失败候选是 `N/A`，无需评分；
5. 勾选问题类型，并写能独立理解的人工理由；
6. 保存当前 Task，看到成功提示后再切下一张；
7. 中断后重新打开，页面从 sidecar 恢复，不修改候选和机器结果。

建议理由格式：

```text
建议给 A/B/C/D，原因：主体……；关键文字/Logo……；几何与排版……；
即便存在……，但不影响/已经影响直接发布；不应仅因……而降级。
```

例子：

```text
建议给 A，原因：两位主人物身份、动作和关系完整，主 Logo 可读，脸和笛子自然，
构图均衡；即便边缘留白与原图不同，也不影响直接发布，不应因目标比例变化判 C。
```

## 4. A/B/C/D 标准

- A：可直接使用；核心传播完整，正常观看下自然美观；
- B：可使用；有轻微全局拉伸、紧裁、次要信息损失或小瑕疵，不需返修；
- C：需要修复；核心主体/人物关系/主标题/主 Logo/商品身份受损，或明显非物理形变；
- D：不可使用；主语义改变、重要人物大幅丢失、严重残缺或大面积发布事故。

宽松但不失控的边界：

- 干净裁掉次要群众、法务小字、装饰边缘，可为 A/B；
- 全局比例变化但人物与文字肉眼自然，可为 A/B；
- 一男一女互动变成单人、主 Logo 消失、肢体断裂、刚性商品弯折、文字条带复制，给 C/D；
- 检测器数字异常但高清图实际完整时，以可见证据为准，并在理由中记录冲突。

## 5. 页面上的 Rule 应怎样读

每个候选展示：

- `Rule 第 n/7 名`：来自冻结的正式完整排名，不是 UI 自己排序；
- `Quality`：0～100 连续软分；
- `proxy_grade`：按当前 Strategy 阈值和门禁得到的机器等级；
- 内容保真：OCR/人物/人脸/商品/Logo/ORB 等组合；
- 视觉完整：清晰度、边缘、颜色、结构线、变换安全；
- 构图：保护区域贴边安全与视觉中心；
- 门禁/回归：使等级封顶或触发人工检查的明确代码。

查看原始证据：

```text
runs/<run-id>/evaluations/<evaluation-id>/metrics/<candidate-id>.json
runs/<run-id>/evaluations/<evaluation-id>/rule-decisions/<task-id>.json
runs/<run-id>/candidates/<task-id>/<method>/candidate.json
```

`candidate.json` 还包含 `generation_status`、失败类型、警告和耗时。失败方法仍计入七方法
分母，但 UI 显示 N/A 且不要求人工打分。

## 6. Agent 应怎样读

Agent 只有显式配置才运行。它读取 Rule Top1 和完整排名，再比较原图/候选语义与高清视觉，
输出完整候选排序、建议 Top1、可见形变、核心内容是否保留、置信度和中文理由代码。

Agent 的优势目标是发现 Rule 难以量化的身份、动作、关系和非物理现象；它不应仅凭 OCR、
数量或连续分机械覆盖 Rule。当前为建议模式，人工结果仍是最后依据。

原始文件：

```text
runs/<run-id>/agent-runs/<agent-run-id>/agent-run.json
runs/<run-id>/agent-runs/<agent-run-id>/decisions/<task-id>.json
runs/<run-id>/agent-runs/<agent-run-id>/calls/
```

没有 Agent 或 AIGC 时页面显示“未运行”，不会把 Rule 理由冒充大模型结论。

## 7. 保存在哪里

标准 Run：

```text
runs/<run-id>/reviews/
├── human-review-current.csv   # 每个 mode/task/route 的当前结论
├── human-review-events.jsonl  # 每次修改追加一条，便于追溯
└── review-status.json         # 完成度、等级分布和当前 CSV 哈希
```

保存只写 sidecar；`run.json`、候选、Evaluation、Rule 决策和 Agent Run 保持不可变。Movie60
继续使用 Release 内兼容格式，已有人工结果会载入。

当前已冻结的人评统计口径是 18 个 Task、126 个候选：Rule 四等级完全一致率 62.70%，
A+B 对 C+D 二分类一致率 78.57%；Agent 在 18 个 Top1 上二分类一致率 44.44%。这些数字只
适用于已有人工标签，不代表完整 60 张准确率，也不允许把大模型建议当成人工金标。

## 8. 单独评分一张现成图片

有原图时：

```powershell
.\.venv\Scripts\retarget-engine.exe score reference source.jpg candidate.jpg
```

输出目录包含：

```text
workspace/scores/reference-<timestamp>/
├── report.json   # 可供程序读取的完整指标
├── report.md     # 开发者可读解释
└── overlay.png   # 检测框与关键区域辅助图
```

查看顺序：硬失败 → 门禁/回归 → Quality/等级 → 内容/视觉/构图 → OCR/数量 → Overlay →
原图与候选高清图。Overlay 是检测器证据，不是人工标注。

无原图时：

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone candidate.jpg
```

它只能检查解码、尺寸、清晰度、近空白等单图技术问题；不能计算内容丢失、OCR 召回或人物
关系是否保持。

## 9. 批量结果怎样校对

先运行：

```powershell
.\.venv\Scripts\retarget-engine.exe run batch D:\images\batch01 `
  --target 1536x1536
```

再打开终端返回的 `review_command`。必须确认：Task 数等于输入图数；每个 Task 的方法分母
为 7；失败项有记录；评审 CSV 的可用候选数与页面一致。不要只汇总成功候选后声称“7/7”。

## 10. 如何把人工反馈用于下一版

1. 冻结当前 `human-review-current.csv` 和事件日志哈希；
2. 统计错分是阈值、权重、门禁、检测器还是视觉语义判断问题；
3. 复制父 Strategy 到新不可变版本，不原地改旧版；
4. 参数问题改 Scoring/Selection YAML；语义问题改 Skill/Knowledge/Prompt；实现问题新增插件；
5. 在 Calibration 集迭代，记录每版差异；
6. 冻结后在 Validation 集只跑一次；
7. 新 Evaluation/Agent Run 使用新 ID，旧结果继续可读；
8. 只有独立验证改善后才切换 `strategies/registry.yaml` 的唯一 active 项。

更详细的版本、插件和重跑范围见 `ADVANCED.md`。
