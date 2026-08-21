# Retarget Ability 开发操作手册

本文面向第一次接手项目的 Windows/Python 开发同学。目标是只依赖仓库、受控模型源和授权素材，从零完成安装、单图运行、批量运行、Rule-only 评分、结果检查和策略迭代。

当前活动策略为 `movie60@3.2.2`，文件入口是
`strategies/movie60/v3_2_2/bundle.yaml`。除非正在做历史回放，新运行都应显式指定这个 Bundle。

## 1. 先理解四种运行方式

| 需求 | 推荐入口 | 是否生成候选 | 是否比较原图 | 是否调用 Agent/AIGC |
|---|---|---:|---:|---:|
| 一张原图跑完整流程 | `scripts/run_one_image.ps1` | 是，七种 | 是 | 否 |
| 已有原图与候选，只做 Rule 评分 | `score reference` | 否 | 是 | 否 |
| 只有候选，做技术检查 | `score standalone` | 否 | 否 | 默认否 |
| 标准批量 Run | `dataset validate` → `run generate` → `evaluate` | 是 | 是 | 否，除非另跑 `agent replay` |

“只跑 Rule”的判定很简单：命令中不传任何 `--agent-*` 参数，不执行
`agent replay`，也不执行 `generation`/SeedDream 脚本。

## 2. 新电脑准备

### 2.1 系统与工具

- Windows 10/11 x64；
- Python 3.12 推荐，3.11/3.13 支持；
- Git；
- 访问私有 GitHub 仓库的账号或 Token；
- 首次安装时能访问公司 PyPI 镜像和经批准的模型制品源。

在 PowerShell 中检查：

```powershell
git --version
py -3.12 --version
```

Python 不存在时，由开发同学或 IT 按公司规范安装。可用的公开安装命令是：

```powershell
winget install Python.Python.3.12
```

安装后重新打开 PowerShell。

### 2.2 公司 pip 镜像

由项目负责人填写，不要把真实内部地址写进公开文档或代码：

```text
公司 PyPI index-url：________________________________
公司 trusted-host（如需要）：________________________
公司 Paddle/PyTorch/模型制品源：_____________________
```

只对当前 PowerShell 会话生效的配置示例：

```powershell
$env:PIP_INDEX_URL = "<公司 PyPI 地址>"
$env:PIP_TRUSTED_HOST = "<公司 trusted-host；不需要则不设置>"
```

如果公司镜像不代理模型文件，需要由负责人提供离线模型缓存。不要通过修改代码跳过模型校验。

## 3. Clone 仓库

二选一：

```powershell
git clone https://github.com/mayin0902/retarget-abillity.git retarget-abillity
cd retarget-abillity
```

或已经安装并登录 GitHub CLI 时：

```powershell
gh auth login
gh repo clone mayin0902/retarget-abillity retarget-abillity
cd retarget-abillity
```

确认分支和工作区：

```powershell
git status --short --branch
git log -1 --oneline
```

## 4. 创建本机环境并安装依赖

### 4.1 推荐：一键安装

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonVersion 3.12
```

脚本会按顺序执行：

1. 在仓库根目录创建 `.venv`；
2. 安装冻结的构建工具、项目和开发依赖；
3. 安装公司 CPU 检测栈运行时；
4. 物化并校验 OCR、目标检测、人脸检测等模型；
5. 校验 v1、v2 与当前 v3.2.2 策略；
6. 运行安装 Smoke。

`.venv` 是当前机器和当前路径的虚拟环境，不可复制到另一台电脑。换机器后要重新建
`.venv` 并安装；可按公司规范迁移模型缓存，但必须保留模型版本和审计文件。

### 4.2 手工等价步骤

排查镜像或某一步安装失败时使用：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade `
  pip==25.2 setuptools==80.9.0 wheel==0.45.1
.\.venv\Scripts\python.exe -m pip install `
  -c requirements\constraints-py311-313.txt -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install `
  -r requirements\company-models-windows.txt
.\.venv\Scripts\python.exe scripts\materialize_analyzer_models.py
.\.venv\Scripts\python.exe scripts\materialize_company_models.py
```

模型缓存已经由公司离线分发时，只校验、不再次下载 D-FINE：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_company_models.py --check-only
```

### 4.3 安装验收

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\retarget-engine.exe version
.\.venv\Scripts\retarget-engine.exe plugins list
.\.venv\Scripts\retarget-engine.exe strategy show `
  strategies\movie60\v3_2_2\bundle.yaml
.\.venv\Scripts\python.exe -m pytest -q
```

`strategy show` 应显示策略哈希和 A/B/C/D 阈值。模型缺失、哈希不匹配或测试失败时，不应继续形成正式结果。

## 5. 可选：下载 Movie60 私有 Release

Release 用于查看 60 张原图、420 张传统候选、机器证据、代表性对比和人工评审进度。它不是 Python 环境。

先登录 GitHub CLI，然后执行：

```powershell
gh auth status
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --repo mayin0902/retarget-abillity `
  --tag movie60-review-v2 `
  --release-version v2 `
  --output-dir local_data\movie60-review-v2
```

脚本会下载两个 ZIP 和 `SHA256SUMS.txt`，校验 SHA-256、ZIP CRC、路径穿越和符号链接后再解压，并拒绝覆盖已有目录。

离线下载资产后：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --asset-dir D:\downloads\movie60-review-v2 `
  --release-version v2 `
  --output-dir local_data\movie60-review-v2
```

## 6. 一张原图完成“重定向 → Rule 评分”

输入支持 JPEG/PNG。`CaseId` 只能使用小写字母、数字、下划线和连字符；每次运行必须使用新的 ID。

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "D:\images\poster.jpg" `
  -CaseId "poster-001" `
  -Strategy "strategies\movie60\v3_2_2\bundle.yaml"
```

这个脚本实际执行四步：

1. 把原图冻结成一个单图 Dataset；
2. 校验 Dataset 的尺寸、哈希和路径；
3. 对同一原图生成七种 1536×1536 候选；
4. 对每张候选重新检测，并用 v3.2.2 Rule 比较原图后评分。

它不会调用 Agent，也不会调用 AIGC。

输出位置：

```text
local_data/datasets/poster-001/          # 冻结的单图 Dataset 和 run.yaml
runs/poster-001-square-v1/
├── run.json                             # Run 状态与分母
├── analysis/                            # 原图检测、保护区和共享分析
├── candidates/<task>/<method>/
│   ├── candidate.png                    # 候选图
│   └── transform.json                   # 算法参数、风险、耗时
└── evaluations/poster-001-rule-v2/
    ├── metrics/                         # 每候选 Rule 指标和等级
    ├── summary.json                     # 聚合结果
    └── strategy/                        # 本次实际策略完整快照
```

快速检查：

```powershell
Get-Content runs\poster-001-square-v1\run.json
Get-ChildItem runs\poster-001-square-v1\candidates -Recurse -Filter candidate.png
Get-ChildItem runs\poster-001-square-v1\evaluations\poster-001-rule-v2\metrics
```

若要重复同一原图，不要删除旧 Run；改用 `poster-001-v2` 这样的新 CaseId。

## 7. 已有原图和候选：只选择 Rule 评分

这是最短、最明确的 Rule-only 流程：

```powershell
.\.venv\Scripts\retarget-engine.exe score reference `
  "D:\images\source.jpg" `
  "D:\images\candidate.jpg" `
  --output-dir "local_data\scores\poster-001-rule-v1" `
  --strategy "strategies\movie60\v3_2_2\bundle.yaml"
```

不要添加 `--agent-backend-url`、`--agent-model` 或 `--agent-api-key-env`。

此命令会：

- 分别对原图和候选运行同一检测栈；
- 比较 OCR、人物、人脸、商品、Logo 候选、局部特征、结构和构图；
- 应用 v3.2.2 的动态权重、软调整和 C/D 门禁；
- 输出 `report.json`、`report.md`、`overlay.png`、输入副本和策略快照；
- 在 `report.json` 中记录 `agent_review_status: not_requested`。

判断结果时优先看：

1. `metrics.proxy_grade`：机器 A/B/C/D；
2. `metrics.quality_score`：连续分；
3. `metrics.human_alignment_matched_gates`：命中的 C/D 门禁；
4. `metrics.critical_regressions` 和 `hard_failures`；
5. `overlay.png`：原图与候选的检测框是否合理；
6. `strategy/`：本次到底用了哪版规则。

输出目录必须不存在。重评时使用新目录，例如 `poster-001-rule-v2`。

## 8. 只有一张候选图：技术检查

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "D:\images\candidate.jpg" `
  --output-dir "local_data\scores\candidate-only-v1" `
  --strategy "strategies\movie60\v3_2_2\bundle.yaml"
```

没有原图就无法判断“文字是否丢失”“人物数量是否减少”或“语义是否变化”，因此该模式只输出清晰度、边缘、亮度、对比度、空白检查和检测框，不生成内容保留结论，也不生成 Rule A/B/C/D。不要把它和 `score reference` 混用。

## 9. 小批量：每张图独立形成完整 Run

适合 2～20 张临时图片，优点是无需手工维护 CSV；缺点是每张图是独立 Run，不适合统一 benchmark 聚合。

```powershell
$inputRoot = "D:\images\batch01"
$index = 1
Get-ChildItem -LiteralPath $inputRoot -File |
  Where-Object { $_.Extension.ToLower() -in '.jpg', '.jpeg', '.png' } |
  Sort-Object Name |
  ForEach-Object {
    $caseId = "batch01-{0:d3}" -f $index
    PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
      -InputImage $_.FullName `
      -CaseId $caseId `
      -Strategy "strategies\movie60\v3_2_2\bundle.yaml"
    if ($LASTEXITCODE -ne 0) { throw "Failed: $($_.FullName)" }
    $index += 1
  }
```

每张图都有自己的失败边界和策略快照。批处理发生错误时，已完成的 Run 不会被覆盖。

## 10. 标准批量：一个 Dataset、一个 Run

适合需要统一分母、统一汇总和人工评审的正式批量。

目录合同：

```text
local_data/datasets/my-batch/
├── dataset.yaml
├── sources.csv
├── targets.csv
├── tasks.csv
├── run.yaml
└── images/
    ├── poster-001.jpg
    └── poster-002.png
```

`dataset.yaml` 最小示例：

```yaml
schema_version: "1.0"
dataset_id: my-batch
version: "1.0.0"
description: Authorized local batch
expected_source_count: 2
expected_scene_counts: {movie_poster: 2}
evaluation_canvas: 1536x1536
generation_originals_may_be_retained_at_2k: true
silent_upsampling_forbidden: true
```

`sources.csv` 每张原图一行。`sha256` 必须是冻结文件的实际小写 SHA-256，宽高必须是 EXIF 旋转后的实际尺寸：

```csv
source_id,image_path,width,height,sha256,split,scene_profile,enabled,source_kind,license_status,scene_category,fixture_type,test_purpose
poster-001,images/poster-001.jpg,1080,1920,<64位小写sha256>,calibration,balanced,true,user_authorized_local_real,local_research_not_publicly_redistributable,movie_poster,,
poster-002,images/poster-002.png,1280,720,<64位小写sha256>,validation,balanced,true,user_authorized_local_real,local_research_not_publicly_redistributable,movie_poster,,
```

取哈希：

```powershell
(Get-FileHash -Algorithm SHA256 `
  local_data\datasets\my-batch\images\poster-001.jpg).Hash.ToLower()
```

`targets.csv`：

```csv
target_id,width,height,format
square-1536,1536,1536,png
```

`tasks.csv`：

```csv
task_id,source_id,target_id,enabled
poster-001__square-1536,poster-001,square-1536,true
poster-002__square-1536,poster-002,square-1536,true
```

生成 `run.yaml` 时，最稳妥的方式是先让单图脚本生成一份模板，复制其中的
`run.yaml`，只修改：

```yaml
dataset_root: local_data/datasets/my-batch
output_root: runs
run_id: my-batch-square-v1
```

保留 `methods`、`analysis` 和 `method_parameters`，不要删除必需模型或把
`detector_mode` 改成跳过。然后执行：

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate `
  local_data\datasets\my-batch
.\.venv\Scripts\retarget-engine.exe run generate `
  local_data\datasets\my-batch\run.yaml
.\.venv\Scripts\retarget-engine.exe evaluate `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v1 `
  --strategy strategies\movie60\v3_2_2\bundle.yaml
.\.venv\Scripts\retarget-engine.exe audit runs\my-batch-square-v1
```

正式评估不要使用 `--no-detectors`。该参数只用于开发期检查流程连通性，会跳过候选 OCR/人脸/物体/Logo 重检，不能形成业务可比的 Rule 结论。

## 11. 只对一个现有 Run 重跑 Rule

候选已经冻结后，修改 Rule 不需要重新生成图片。用新的 Evaluation ID 即可：

```powershell
.\.venv\Scripts\retarget-engine.exe evaluate `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v3-2-2 `
  --strategy strategies\movie60\v3_2_2\bundle.yaml
```

旧 Evaluation 保留。新 Evaluation 会写入自己的策略快照，可直接比较两版 Rule。

## 12. 可选 Agent 与人工 UI

Rule 完成后，只有在已部署内部 OpenAI-compatible 视觉端点时才运行 Agent：

```powershell
.\.venv\Scripts\retarget-engine.exe agent replay `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v3-2-2 `
  --agent-run-id my-batch-agent-v1 `
  --mode always_on_agent `
  --backend-url "http://127.0.0.1:8000/v1" `
  --model "<内部模型ID>" `
  --strategy strategies\movie60\v3_2_2\bundle.yaml
```

当前 v3.2.2 中 `agent_selection_mode` 是 `advisory_only`：Agent 生成中文视觉建议和挑战证据，但生产最终选择仍由 Rule 给出。

启动人工评审 UI：

```powershell
.\.venv\Scripts\retarget-engine.exe review web `
  runs\my-batch-square-v1 --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。UI 只追加人工事件，不覆盖机器指标。

## 13. 策略迭代，不覆盖旧版本

1. 冻结本轮人工评分；
2. 明确误差来自检测、分数权重、C/D 门禁、Rule 排序、Prompt 还是 Skill；
3. 复制当前策略目录为新的不可变版本；
4. 修改 Bundle 的 `version`、`parent_strategy` 和对应 YAML；
5. 参数变化只改 YAML；逻辑变化新增插件 Adapter 并在白名单注册；
6. 对比策略：

```powershell
.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v3_2_2\bundle.yaml `
  strategies\movie60\v3_3_0\bundle.yaml
```

7. 用新 Evaluation ID 重跑 Calibration；
8. 冻结后再对 Validation 跑一次；
9. 保留旧策略目录、旧 Run、旧 Evaluation 和人工事件。

## 14. 常见问题

### `.venv already exists`

一键脚本拒绝覆盖环境。已有环境正常时直接用；确需重建时先由开发同学确认路径，再手工移走旧 `.venv`，不要让脚本猜测删除。

### `output already exists`

Run、Evaluation、评分目录都采用不可覆盖设计。换一个版本化 ID，不要删除证据。

### 模型缺失或校验失败

重新执行模型 materialize/check-only，检查公司制品源和 `models/`。不要把
`detector_mode` 改成可选来伪装成功。

### OCR 或人物框明显漏检

先看 `overlay.png`/候选 evidence，确认是检测漏报还是图片确实丢失。检测漏报属于模型证据问题，不能单独当作图片 C/D 的事实；记录后进入下一版 Detector/Rule 校准。

### 为什么同一图不能覆盖旧结果

项目用不可变 Run/Evaluation/Strategy 保证回放。新版本用新 ID；比较时通过策略哈希、输入哈希和人工事件建立链路。

## 15. 开发交付验收清单

- [ ] 私有仓库可 Clone；
- [ ] `.venv` 在本机新建，没有从其他电脑复制；
- [ ] `pip check`、插件列表、策略校验和测试通过；
- [ ] 模型物化审计通过；
- [ ] 单图完整流程产生七候选和 Rule Evaluation；
- [ ] `score reference` 产生 `report.json`、`report.md`、`overlay.png`；
- [ ] Rule-only 结果中 Agent 为 `not_requested`；
- [ ] 批量 Dataset 校验、Run、Evaluation 和 audit 通过；
- [ ] 每个 Evaluation 保存策略快照；
- [ ] 新规则使用新 Strategy 版本与新 Evaluation ID；
- [ ] 密钥、模型权重、受保护素材、`.venv` 和 `runs/` 未误提交 Git。
