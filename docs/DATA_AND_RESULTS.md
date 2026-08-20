# 数据、结果与中间证据

本项目把“可复现定义”和“受限像素”分开保存。Git 提供代码、配置、数据合同、评分规则和技术
报告；近期中文海报、生成结果、完整 Run、模型权重和人工评审表只保存在本地。

## 当前唯一数据集

| 内容 | 路径 | 是否进 Git |
| --- | --- | --- |
| 用户提供的 60 张原始素材 | `G:\Projects\movie-visual-dataset-60-20260818` | 否 |
| 数据合同 | `datasets/movie_visual_60_v1/README.md` | 是 |
| 物化后的本地 Dataset | `local_data/datasets/movie_visual_60_v1/` | 否 |
| 七方法配置 | `configs/movie_visual60_square_v1.yaml` | 是 |

60 张按人物、电影海报、影片画面、视频封面各 15 张分层；Calibration 20 用于规则和 Skill
迭代，Validation 40 在规则冻结后只运行一次。素材允许本地研究；不等于允许公开再分发。

## 当前唯一结果入口

打开 `deliverables/movie60-review/`：

| 目录 | 用途 |
| --- | --- |
| `all60/` | 60 个任务、7 个候选，共 420 张候选的完整人工校对工作区 |
| `focus20/` | 20 张困难样本的 Rule、Agent、AIGC 对比和 API 状态 |
| `rules-v1/` | 产生当前结果的评分协议、Agent 门禁和冻结源码快照 |
| `start-review.bat` | 启动本地人工评审 UI |

`deliverables/` 已被 `.gitignore` 整体忽略。这里是本地交接与人工评分工作区，不会被普通
`git add .` 上传。

### 完整 60 张怎么看

1. 打开 `deliverables/movie60-review/all60/index.html` 浏览最终选择；
2. 双击 `deliverables/movie60-review/start-review.bat`，在 UI 中逐候选打分；
3. 每个任务位于 `all60/tasks/<task_id>/`：
   - `00_source.*`：原图；
   - `candidates/`：七种传统候选；
   - `01_final.png`、`02_comparison.jpg`：当前最终选择及对照；
   - `evidence/machine/rule-ranking.json`：Rule 完整排名和指标；
   - `evidence/machine/01_rule_aware_overview.png`：七候选总览；
   - `evidence/machine/05_rule_vs_qwen_highres.png`：Rule 与 Agent challenger 高清对照；
   - `evidence/machine/reviews/`：候选级高清等级、原因和中文建议；
   - `evidence/route/`：存在 AIGC 计划时的 Rule/Agent/AIGC 对比和调用状态。

证据与对应图片保持在同一任务目录，不再另建大量“最终版 v1/v2/v3”目录。

### 重点 20 张怎么看

`focus20/tasks/<task_id>/` 每个任务保存原图、Rule、Agent、成功时的 AIGC、拼图、机器评分和
API 执行状态。`aigc-status.csv` 是20次调用的完整分母：8张成功回图、12张失败；失败不能按
图像质量记 C，应记为技术 N/A 并单独统计端到端成功率。

## 当前 Run 事实源

本地冻结 Run 为 `runs/movie60-square-v1-20260818/`，主要层次如下：

```text
sources/             规范化源图
analysis/            OCR、人脸、人物、商品/Logo、结构保护分析
candidates/          七方法候选、Transform、资源记录
evaluations/         420个候选的确定性指标
agent-inputs/        送给视觉Agent的总览与证据
agent-runs/          排名、challenger和调用记录
strict-reviews/      高清整图/局部复核
external-generation/ AIGC计划、回图、失败与成本状态
benchmarks/          路线级汇总
```

`runs/` 被 Git 忽略。需要复现时重新运行脚本；需要审计时使用本地冻结 Run，不能复制一部分后
宣称是完整分母。

## Git 与外部共享边界

### Git 应提交

- `src/`、`scripts/`、`tests/`、`configs/`；
- 数据合同、无像素 manifest、权利说明；
- 本目录列出的当前文档；
- 一份当前 Movie60 技术报告。

### Git 不应提交

- 原始中文海报、人物图和派生候选；
- `runs/`、`deliverables/`、`local_data/`；
- 模型权重、缓存、API 响应和密钥；
- 失败构建、旧 ZIP、重复版本目录和一次性模型预审笔记。

给私有仓库协作者传图片时，运行：

```powershell
.\.venv\Scripts\python.exe scripts\package_movie60_release.py
```

脚本严格校验60个任务和七方法集合，并生成同一个版本的两个Release资产：

- `movie60-handoff-v1-core.zip`：60原图、420候选、数据元数据、机器JSON理由和人工表；
- `movie60-handoff-v1-evidence.zip`：高清拼图、候选局部、Rule/Agent对照和AIGC图像证据；
- `SHA256SUMS.txt`：两个资产的SHA-256。

两包解压到同一父目录后合并为一个 `movie60-review/`。Release必须保持私有，只允许仓库协作者
访问；未经逐图权利确认，不得把资产转为公开Release、Hugging Face或其他公网数据集。

新机器不应手工解压，统一执行：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --repo mayin0902/retarget-abillity `
  --tag movie60-review-v1 `
  --output-dir local_data\movie60-review-v1
```

脚本会校验 SHA-256、ZIP CRC、路径穿越和唯一根目录，并拒绝覆盖已有结果。

## 本地历史归档

- 旧交付包和 ZIP：`local_data/deliverables_archive/`；
- 旧 CN60 文档和一次性审计：`local_data/docs_archive/`。

这些目录用于本机追溯，不是接手开发者的默认阅读入口，也不进入 Git。
