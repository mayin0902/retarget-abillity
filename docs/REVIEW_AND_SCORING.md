# 人工评审与自动评分

## 统一评审入口

```text
Movie60 Release ─┐
标准 Run ────────┼─> ReviewWorkspaceAdapter -> FastAPI -> 当前评审页面
外部候选目录 ────┘
```

Movie60 保留“完整 60 张 / 重点 20 张”；标准 Run 显示“全部候选 / 路线对比”。
页面仍包含原图、候选大图、Rule 分数与理由、Agent 建议（如有）、A/B/C/D、问题类型、
人工理由、保存与断点继续。缺少 Agent 或 AIGC 时只显示“未运行”，不会伪造结果。

## 打开与保存

```powershell
# Movie60/current 或最近 Run
START_REVIEW.bat

# 指定标准 Run
.\.venv\Scripts\retarget-engine.exe review open runs\<run-id>
```

标准 Run 的人工结果只写到：

```text
runs/<run-id>/reviews/
├── human-review-current.csv       # 每个 mode/task/route 的当前结论
├── human-review-events.jsonl      # 追加式历史，保留每次修改
└── review-status.json             # 完成数、等级分布和当前 CSV 哈希
```

候选图片、`run.json`、Evaluation 指标和 Agent Run 不被改写。Movie60 Release 继续使用其
既有兼容保存格式，因此原来的人评记录不会丢。

## A/B/C/D 含义

- A：可直接使用；主体、关键文字/Logo 和视觉关系自然完整。
- B：可使用；有轻微拉伸、紧裁或次要信息损失，但不影响核心传播。
- C：需要修复；核心主体/关系/标题/Logo 有明显损失，或存在显著非物理形变。
- D：不可使用；主语义改变、严重残缺、重大文字/Logo 错误或大面积破坏。

目标比例与原图不同本身不是降级理由。干净裁掉次要群众、装饰文字或边缘信息可以是
A/B；主要人物从两人变一人、主 Logo 消失、肢体/刚性物品断裂弯折则进入 C/D。

## Rule 与当前证据

Rule 对每个候选重新检测并比较原图证据，给出连续 `quality_score`、代理等级和门禁。
页面理由会展示内容保真、视觉完整、构图、OCR/人物/人脸/商品/Logo 保留及关键回归。
它是可回放机器证据，不是人工金标。

当前有人评标签的基线为 18 个 Task、126 个候选：Rule 的 A/B/C/D 完全一致率 62.70%，
A+B/C+D 二分类一致率 78.57%。Agent 当前只作为建议：在这 18 个 Top1 上二分类一致率
44.44%，不具备自动覆盖 Rule 的证据。完整 60 张机器结果不能冒充人工准确率。

## 单独评分已有图片

有原图时：

```powershell
.\.venv\Scripts\retarget-engine.exe score reference source.jpg candidate.jpg
```

默认输出到 `workspace\scores\reference-<timestamp>`，包含 `report.json`、`report.md` 和
`overlay.png`。查看顺序：Quality、代理等级、内容保真、视觉完整、构图、门禁/关键回归、
最后查看 Overlay 和原图/候选大图。

只有候选、没有原图时：

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone candidate.jpg
```

Standalone 只能报告技术风险，不能声称“内容完整”或给出可靠的原图保真结论。

## 人工反馈如何进入下一版

不要直接修改旧 Strategy 或旧 Run。先冻结人评 CSV/JSONL，再计算误判类型，创建新的
StrategyBundle 目录和版本：调整阈值/权重、门禁、Prompt/Skill 或插件 ID；跑开发集，
冻结后只在验证集运行一次。旧 Bundle、旧策略 SHA、Evaluation 快照和人工事件仍可追溯。
