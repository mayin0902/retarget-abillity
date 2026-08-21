# Retarget Ability 从零操作手册（工程详解版）

这是 [开发操作手册](DEVELOPER_OPERATION_MANUAL.md) 的逐步展开版。简版适合日常查命令；本页适合新电脑首次安装、开发交接和故障排查。

每个阶段都按“为什么做 → 怎么做 → 成功应看到什么 → 失败如何处理”说明。所有命令默认在 Windows PowerShell、仓库根目录执行。

## 0. 开始前先确认目标

先判断你属于哪一种任务：

| 任务 | 需要原图 | 需要已有候选 | 产生七候选 | 输出 A/B/C/D | Agent/AIGC |
|---|---:|---:|---:|---:|---:|
| 完整单图 | 是 | 否 | 是 | 是 | 默认不调用 |
| 原图+候选 Rule-only | 是 | 是 | 否 | 是 | 不调用 |
| 无原图技术检查 | 否 | 是 | 否 | 否 | 默认不调用 |
| 标准批量 | 是 | 否 | 是 | 是 | 默认不调用 |
| Agent Replay | 是 | 是 | 否 | Rule 已先完成 | 需要内部视觉端点 |

Rule-only 的边界：

- 不传任何 `--agent-*` 参数；
- 不运行 `agent replay`；
- 不运行 `generation`、SeedDream 或其他外部 Provider 脚本；
- 正式评分不使用 `--no-detectors`。

## 1. 当前版本应该使用什么

### 1.1 代码和 Release

```text
GitHub main: 以 clone 后 `git log -1 --oneline` 的当前提交为准
私有 Pre-release: movie60-review-v3
当前部署 Strategy: movie60@3.3.0
Strategy 入口: strategies/movie60/v3_3/bundle.yaml
```

### 1.2 数据和评分不是同一个版本号

当前图片数据集仍叫 `movie-visual-60-v1@1.0.0`。Release v3 是交付版本，其中 `all60` 主表使用 v3.3.0 Rule，并完整保留已有人工评分。详细矩阵见
[当前数据、评分与 Agent 路线状态](reviews/movie60-v3/CURRENT_DATA_AND_ROUTE_STATUS.md)。

原因：Dataset 决定输入像素，Run 决定候选像素，Strategy 决定评分，Release 决定交付内容。四层可独立升级，不能只看一个“v2”。

## 2. 新电脑检查

### 2.1 磁盘和路径

建议：

- 仓库路径使用短英文目录，例如 `D:\work\retarget-abillity`；
- 预留至少 20 GB 给依赖、模型、临时 Run；大量素材另行规划；
- 避免把仓库放进自动同步盘，防止模型和 Run 被占用；
- 不要使用管理员 PowerShell，除非公司 IT 明确要求。

查看磁盘：

```powershell
Get-PSDrive -PSProvider FileSystem
```

为什么：模型、`.venv` 和 1536×1536 七候选会占用明显磁盘；运行中磁盘不足可能留下 PARTIAL/FAILED 证据，但不能形成完整结果。

### 2.2 Git、Python、GitHub CLI

```powershell
git --version
py -0p
py -3.12 --version
gh --version
```

只要求 Git 和 Python；GitHub CLI 用于私有 Release 下载。推荐 Python 3.12，3.11/3.13 受支持。

Python 缺失时按公司软件分发流程安装，或：

```powershell
winget install Python.Python.3.12
```

安装后关闭所有旧 PowerShell，再开新窗口。`py -0p` 应显示 3.12 的真实路径。

## 3. 配置公司 Python 镜像

负责人填写：

```text
PyPI index-url：____________________________________
trusted-host：______________________________________
Paddle/PyTorch wheel 源：___________________________
模型制品或离线缓存：_______________________________
```

### 3.1 临时环境变量方式

```powershell
$env:PIP_INDEX_URL = "<公司 PyPI 地址>"
$env:PIP_TRUSTED_HOST = "<需要时填写；否则不要设置>"
```

检查当前值：

```powershell
python -m pip config debug
Get-ChildItem Env:PIP_*
```

为什么建议先用当前会话变量：不会永久修改个人/全局 pip 配置，也不会把内部地址写进仓库。

### 3.2 离线环境

需要负责人提供：

- 项目 wheel/依赖 wheelhouse；
- `company_cpu_v2` 对应 PaddleOCR、Transformers、Torch 运行时；
- PP-OCRv6 small、D-FINE、YuNet 的已审计缓存；
- 模型目录审计文件和 revision。

不要只复制另一台机器的 `.venv`。虚拟环境包含绝对路径、解释器和编译扩展，跨电脑不可作为可靠交付物。

## 4. Clone 私有仓库

### 4.1 HTTPS

```powershell
New-Item -ItemType Directory -Path D:\work -Force
Set-Location D:\work
git clone https://github.com/mayin0902/retarget-abillity.git retarget-abillity
Set-Location retarget-abillity
```

如 GitHub 要求认证，使用公司批准的凭据管理器或 Token，不要把 Token 写进命令历史、`.env` 模板或 Git 文件。

### 4.2 GitHub CLI

```powershell
gh auth login
gh auth status
gh repo clone mayin0902/retarget-abillity retarget-abillity
Set-Location retarget-abillity
```

### 4.3 Clone 验收

```powershell
git status --short --branch
git remote -v
git log -1 --oneline
Test-Path pyproject.toml
Test-Path scripts\bootstrap_windows.ps1
Test-Path strategies\movie60\v3_3\bundle.yaml
```

预期：工作区无修改，remote 指向私有仓库，三个 `Test-Path` 均为 `True`。

为什么先检查：避免在错误仓库、旧压缩包或不完整目录里安装。

## 5. 创建 `.venv` 和安装依赖

### 5.1 一键安装

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonVersion 3.12
```

`-ExecutionPolicy Bypass` 只作用于本次 PowerShell 子进程，不永久降低系统策略。

脚本为何分这些步骤：

1. `python -m venv .venv`：隔离项目包，避免污染系统 Python；
2. 固定 pip/setuptools/wheel：减少构建工具漂移；
3. `-e ".[dev]"`：源码可编辑安装，CLI 直接使用当前代码；
4. constraints：限制 Python 3.11～3.13 的关键依赖组合；
5. company models requirements：安装 OCR/D-FINE 所需额外运行时；
6. materialize scripts：下载或检查固定模型；
7. strategy show：确认不可变策略引用完整；
8. pytest smoke：确认安装后主接口可运行。

预期末尾：

```text
Bootstrap completed.
Python: <repo>\.venv\Scripts\python.exe
Next: docs\README.md
```

### 5.2 为什么脚本拒绝已有 `.venv`

防止安装脚本覆盖同事已有环境或把半套依赖混入新环境。存在 `.venv` 时先检查：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

确实要重建时，由开发同学先将旧环境改名备份。不要在自动脚本中递归删除未知路径。

### 5.3 手工安装与每一步验收

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
```

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade `
  pip==25.2 setuptools==80.9.0 wheel==0.45.1
```

```powershell
.\.venv\Scripts\python.exe -m pip install `
  -c requirements\constraints-py311-313.txt `
  -e ".[dev]"
```

```powershell
.\.venv\Scripts\python.exe -m pip install `
  -r requirements\company-models-windows.txt
```

每一步返回码必须为 0。发生镜像 401/403/证书错误时停在依赖层解决，不要改业务代码。

## 6. 物化检测模型

```powershell
.\.venv\Scripts\python.exe scripts\materialize_analyzer_models.py
.\.venv\Scripts\python.exe scripts\materialize_company_models.py
```

分别负责：

- YuNet 和兼容基础模型审计；
- PP-OCRv6 small 与 D-FINE 固定缓存/审计。

已经有离线缓存时：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_company_models.py --check-only
```

为什么不能直接关 Detector：Rule 的文字、人脸、人物、商品、Logo 保留率依赖候选重检。关掉后只能检查流程是否通，不能形成当前业务评分。

## 7. 安装完成验收

按顺序执行：

```powershell
.\.venv\Scripts\python.exe -m pip check
```

确认没有依赖冲突。

```powershell
.\.venv\Scripts\retarget-engine.exe version
.\.venv\Scripts\retarget-engine.exe plugins list
```

确认 CLI 和白名单插件可发现。

```powershell
.\.venv\Scripts\retarget-engine.exe strategy show `
  strategies\movie60\v3_3\bundle.yaml
```

确认：

- version=`3.3.0`；
- A_min=90、B_min=65、C_min=50；
- detector=`company_cpu_v2`；
- scorer=`human_aligned_proxy_v3`；
- 有稳定 `strategy_sha256`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

所有测试通过后才开始正式图片运行。测试只验证实现合同，不证明图片质量已经符合人工偏好。

## 8. Movie60 Release 下载与版本识别

### 8.1 在线下载

```powershell
gh auth status
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --repo mayin0902/retarget-abillity `
  --tag movie60-review-v2 `
  --release-version v2 `
  --output-dir local_data\movie60-review-v2
```

脚本先下载临时资产，再校验：

- `SHA256SUMS.txt`；
- ZIP CRC；
- 无绝对路径/`..` 路径穿越；
- 无符号链接；
- 两个 ZIP 合并到唯一 `movie60-review` 根。

输出目录已存在时会在网络调用前失败，避免覆盖人工记录。

### 8.2 下载后看哪里

```text
local_data/movie60-review-v3/
├── all60/                 # 60 张、420 候选、当前评分和人工表
├── focus20/               # 20 张困难/AIGC专项
├── strategy/              # 当前 v3.3.0 不可变策略快照
├── documentation/         # 安装、算法、评审和证据报告
└── VERSION.json           # 当前版本、分母和人工证据哈希
```

请先读根 `README.md` 和 `VERSION.json`。`all60/candidate-review.csv` 的 Rule 列属于 v3.3.0，人工列是从既有评审逐行迁移并校验的真实人工记录。

### 8.3 素材边界

Movie60 是内部研究素材，默认不可公开再分发。不要把 Release 解压内容、原图、候选、人物图或人工 Reviewer ID 提交到普通 Git 分支。

## 9. 单图完整运行

### 9.1 准备输入

支持 JPEG/PNG。先检查：

```powershell
$input = "D:\images\poster.jpg"
Test-Path -LiteralPath $input -PathType Leaf
Get-Item -LiteralPath $input | Select-Object FullName,Length,LastWriteTime
(Get-FileHash -Algorithm SHA256 -LiteralPath $input).Hash.ToLower()
```

原因：Run 会冻结输入哈希，后续同名文件被替换也能被识别。

### 9.2 选择 CaseId

只能使用：

```text
^[a-z0-9][a-z0-9_-]{0,79}$
```

推荐：`业务批次-序号-策略用途`，例如 `movie-demo-001`。CaseId 同时决定 Dataset、Run 和 Evaluation 路径；存在旧路径时拒绝覆盖。

### 9.3 执行

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "D:\images\poster.jpg" `
  -CaseId "movie-demo-001" `
  -Strategy "strategies\movie60\v3_3\bundle.yaml"
```

### 9.4 脚本内部的四阶段

#### 阶段 A：冻结单图 Dataset

`prepare_single_image_dataset.py`：

- 原图按字节复制到 `local_data/datasets/<case>/images/`；
- 读取 EXIF 方向后的宽高；
- 计算 SHA-256；
- 写 `dataset.yaml`、三个 CSV 和 `run.yaml`；
- 默认场景为 `movie_poster`、目标为 1536×1536。

为什么冻结副本：运行中原路径被改动不会污染已声明输入。

#### 阶段 B：Dataset Validate

验证：

- 文件存在且可解码；
- 宽高/哈希与 CSV 一致；
- 相对路径不能逃离 Dataset；
- Source/Target/Task 外键完整；
- ID、split、scene 和 expected count 合法；
- 重复像素/路径不会造成隐性分母错误。

#### 阶段 C：Generation

原图检测一次并构建共享保护图，然后七方法逐一生成。每种方法输出候选图、Transform、状态、耗时和风险字段。

#### 阶段 D：Evaluation

每张候选重新执行检测，并与原图检测/像素/Transform 比较，应用 v3.3.0 Rule。策略完整复制进 Evaluation。

### 9.5 成功检查

```powershell
$run = "runs\movie-demo-001-square-v1"
Get-Content -Raw "$run\run.json"
Get-ChildItem "$run\candidates" -Recurse -Filter candidate.png
Get-ChildItem "$run\evaluations\movie-demo-001-rule-v2\metrics" -File
Get-ChildItem "$run\evaluations\movie-demo-001-rule-v2\strategy" -Recurse -File
.\.venv\Scripts\retarget-engine.exe audit $run
```

预期：七候选、七份候选指标、策略快照存在，audit 为 PASS。若某方法失败，Run 可能为 PARTIAL_COMPLETED；分母仍保留，不能只统计成功图片。

## 10. 已有原图和候选：Rule-only

### 10.1 命令

```powershell
.\.venv\Scripts\retarget-engine.exe score reference `
  "D:\images\source.jpg" `
  "D:\images\candidate.png" `
  --output-dir "local_data\scores\case-001-rule-v3-3" `
  --strategy "strategies\movie60\v3_3\bundle.yaml"
```

### 10.2 为什么这是完整 Rule，而不是简单相似度

命令分别运行：

- Source OCR/人脸/人物/商品/Logo 检测；
- Candidate 同套检测；
- OCR 字符召回和序列相似；
- 人脸/人物/商品/Logo 数量保留；
- ORB、清晰度、边缘、色彩、结构线和构图；
- v3.3.0 权重、证据罚分、等级阈值和声明式门禁。

因为候选不是由当前 Run 生成，`transform=None`，所以没有该算法的 seam/mesh/warp 过程风险。若必须评价 Transform，需要使用完整 Run，而不是只给两张图。

### 10.3 输出逐项阅读

```text
local_data/scores/case-001-rule-v3-3/
├── report.json
├── report.md
├── overlay.png
├── inputs/source.<ext>
├── inputs/candidate.<ext>
└── strategy/
```

`report.json` 先看：

```text
mode
source.sha256 / candidate.sha256
strategy_version / strategy_sha256
detector_suite_plugin / scorer_plugin / analyzer_ids
elapsed_seconds
metrics.quality_score
metrics.proxy_grade
metrics.proxy_business_success
metrics.hard_failures
metrics.critical_regressions
metrics.human_alignment_matched_gates
agent_review_status
```

Rule-only 应为：

```text
agent_review_status = not_requested
```

`overlay.png` 用来检查检测证据是否合理，不是最终美学拼图。

## 11. 无原图候选检查

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "D:\images\candidate.png" `
  --output-dir "local_data\scores\case-001-standalone" `
  --strategy "strategies\movie60\v3_3\bundle.yaml"
```

可得到：尺寸、空白、Laplacian 清晰度、边缘密度、亮度、对比度和候选检测框。

不可得到：文字保留、人数下降、语义变化、相对构图质量和 Rule A/B/C/D。原因是这些概念需要 Source 作为参照。

## 12. 2～20 张临时批处理

不想手工维护 CSV 时，让每张图形成独立 Run：

```powershell
$inputRoot = "D:\images\batch01"
$files = Get-ChildItem -LiteralPath $inputRoot -File |
  Where-Object { $_.Extension.ToLower() -in '.jpg', '.jpeg', '.png' } |
  Sort-Object Name

$index = 1
foreach ($file in $files) {
  $caseId = "batch01-{0:d3}" -f $index
  PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
    -InputImage $file.FullName `
    -CaseId $caseId `
    -Strategy "strategies\movie60\v3_3\bundle.yaml"
  if ($LASTEXITCODE -ne 0) {
    throw "Batch stopped at $($file.FullName)"
  }
  $index += 1
}
```

原因：这种方式最大化隔离性，一张失败不污染其他 Run；但不能直接得到一个统一 Task 分母的 benchmark。

## 13. 正式批量 Dataset

### 13.1 目录

```text
local_data/datasets/my-batch/
├── dataset.yaml
├── sources.csv
├── targets.csv
├── tasks.csv
├── run.yaml
└── images/
```

所有 `image_path` 必须是 Dataset 根下的相对 POSIX 路径，例如
`images/poster-001.jpg`，不能写 `D:\...` 或 `../...`。

### 13.2 `dataset.yaml`

```yaml
schema_version: "1.0"
dataset_id: my-batch
version: "1.0.0"
description: Authorized local batch
sources_file: sources.csv
targets_file: targets.csv
tasks_file: tasks.csv
expected_source_count: 2
expected_scene_counts:
  movie_poster: 2
evaluation_canvas: 1536x1536
generation_originals_may_be_retained_at_2k: true
silent_upsampling_forbidden: true
```

原因：expected count 是数据质量门禁，防止 CSV 截断后仍被当完整批次运行。

### 13.3 `sources.csv`

```csv
source_id,image_path,width,height,sha256,split,scene_profile,enabled,source_kind,license_status,scene_category,fixture_type,test_purpose
poster-001,images/poster-001.jpg,1080,1920,<sha256>,calibration,balanced,true,user_authorized_local_real,local_research_not_publicly_redistributable,movie_poster,,
poster-002,images/poster-002.png,1280,720,<sha256>,validation,balanced,true,user_authorized_local_real,local_research_not_publicly_redistributable,movie_poster,,
```

字段原因：

- `source_id`：稳定主键，不使用会变的文件名推断；
- `width/height/sha256`：锁定实际像素；
- `split`：防止 Calibration 与 Validation 混用；
- `scene_category`：场景权重/门禁输入；
- `source_kind/license_status`：决定 Git/Release/API 外发边界；
- fixture 字段：程序图必须显式说明测试目的，不能冒充真实场景。

计算哈希：

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath `
  local_data\datasets\my-batch\images\poster-001.jpg).Hash.ToLower()
```

### 13.4 `targets.csv`

```csv
target_id,width,height,format
square-1536,1536,1536,png
```

### 13.5 `tasks.csv`

```csv
task_id,source_id,target_id,enabled
poster-001__square-1536,poster-001,square-1536,true
poster-002__square-1536,poster-002,square-1536,true
```

Task 是 Source×Target；同一 Source 可在将来配多个目标，但本轮只关注 1:1。

### 13.6 `run.yaml`

推荐先运行一张测试图，让脚本生成模板，再复制其 `run.yaml`。修改：

```yaml
dataset_root: local_data/datasets/my-batch
output_root: runs
run_id: my-batch-square-v1
```

保留：

```yaml
device: cpu
method_profile: cn_square_v2
methods: [direct_warp, crop, seam, seam_full, mesh, mesh_full, seam_scale]
analysis:
  detector_mode: required
  detector_suite_plugin: company_cpu_v2
```

为什么显式声明方法 profile：防止代码升级后默认方法集合变化，旧 Run 无法复现。

### 13.7 执行顺序

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate `
  local_data\datasets\my-batch
```

失败就修 Dataset，不要继续 Generation。

```powershell
.\.venv\Scripts\retarget-engine.exe run generate `
  local_data\datasets\my-batch\run.yaml
```

候选冻结后再评分。

```powershell
.\.venv\Scripts\retarget-engine.exe evaluate `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v3-3 `
  --strategy strategies\movie60\v3_3\bundle.yaml
```

```powershell
.\.venv\Scripts\retarget-engine.exe audit runs\my-batch-square-v1
```

audit 检查候选方法集合、状态和 Run 合同，不负责人工视觉验收。

## 14. 已有 Run 重放新 Rule

候选像素不变时，只新增 Evaluation：

```powershell
.\.venv\Scripts\retarget-engine.exe evaluate `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v3-3-0 `
  --strategy strategies\movie60\v3_3_0\bundle.yaml
```

原因：把 Generation 与 Evaluation 分开后，调权重/门禁无需重新跑七种算法，也不会覆盖旧评分。

比较策略本身：

```powershell
.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v3_3\bundle.yaml `
  strategies\movie60\v3_3_0\bundle.yaml
```

## 15. Agent Replay

前提：内部 OpenAI-compatible 视觉服务已启动，端点只能按公司网络规范访问。

```powershell
.\.venv\Scripts\retarget-engine.exe agent replay `
  runs\my-batch-square-v1 `
  --evaluation-id my-batch-rule-v3-3 `
  --agent-run-id my-batch-agent-v3-2-2-v1 `
  --mode always_on_agent `
  --backend-url "http://127.0.0.1:8000/v1" `
  --model "<内部模型ID>" `
  --strategy strategies\movie60\v3_3\bundle.yaml
```

为什么 Agent 必须在 Rule 后：它需要完整 Rule 排名、Top1 和每候选指标作为结构化证据。

当前 v3.3.0 是 `advisory_only`。Agent 结果写入独立 `agent-runs/`，不改变 Rule Evaluation。

## 16. 人工评审 UI

```powershell
.\.venv\Scripts\retarget-engine.exe review web `
  runs\my-batch-square-v1 `
  --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

评审者应逐候选给 A/B/C/D、问题码和自由理由。人工事件追加保存；策略升级不会修改旧人工意见。

不要用机器建议自动填 `human_grade`。人工同级候选不要求机器同分；全同级 Task 不进入 Top1 命中率。

## 17. 新策略如何创建

### 17.1 只改参数

例如把 A 阈值从 90 改为 80：

1. 复制 `strategies/movie60/v3_3/` 为新目录 `v3_3_0/`；
2. 修改 `bundle.yaml` 的 version、parent、description；
3. 修改 `scoring.yaml` 的 policy/version 和 `proxy_a_threshold`；
4. 不修改 v3.3.0；
5. `strategy show` 与 `strategy diff`；
6. 新 Evaluation ID 跑 Calibration；
7. 冻结后再跑独立 Validation。

### 17.2 换算法实现

如果公式本身变化：

1. 在 `src/retarget_agent/` 新增遵循 Protocol 的 Adapter；
2. 在 `plugin_catalog.py` 注册新 ID；
3. 添加单元/集成测试；
4. 新 StrategyBundle 引用新 ID；
5. 旧插件 ID 继续保留，用于历史回放。

YAML 不能写任意 Python import path，这是安全和可审计边界。

## 18. 基于人工反馈定位应该改哪层

| 人工发现 | 优先检查 | 典型修复 |
|---|---|---|
| 肉眼文字完整，OCR 召回很低 | Detector/OCR 匹配 | 新 OCR Adapter、框级匹配、降低错误门禁 |
| 人物完整但人数下降 | D-FINE/匹配 | 阈值、跨图实例匹配、人工框证据 |
| seam 人脸扭曲但计数都在 | 几何证据/Agent Skill | 人脸关键点、人体姿态、不物理案例 |
| crop 裁背景却被重罚 | composition/color 权重 | 场景权重或门禁修正 |
| Agent 无证据换掉 Rule | Agent Prompt/Skill/override | 允许 KEEP_RULE、提高证据门槛 |
| 所有 A 机器分差很大 | 连续分稳定性 | 调权重，不能当 Top1 错误处理 |

每次修复先写成可泛化规则，不写 task ID 或文件名特例。

## 19. 常见错误与处理

### 19.1 `py -3.12` 找不到

重新安装 Python，确认勾选 launcher，重开 PowerShell。不要让脚本静默改用未知系统 Python。

### 19.2 pip 401/403/SSL

检查公司镜像授权、证书、代理和 trusted-host。不要禁用 TLS 校验或把凭据提交 Git。

### 19.3 模型下载超时

使用公司制品或离线缓存，随后 `--check-only`。不要把 Detector 改成 disabled 继续正式评分。

### 19.4 `output already exists`

不可变设计正常生效。使用新的 CaseId、Run ID、Evaluation ID 或 score output 目录。

### 19.5 Run 是 FAILED/PARTIAL_COMPLETED

先读 `run.json` 和候选错误 JSON。修复原因后创建新 Run ID；不要覆盖失败证据，也不要从统计分母删除失败 Task。

### 19.6 OCR/人物/Logo 框与肉眼不符

记录为检测证据问题。Rule 数值不是事实裁决；回看高清像素，必要时由 Agent advisory 和人工纠正，并在下一 Strategy/Detector 版本修复。

### 19.7 PowerShell 脚本不能执行

使用本次进程参数：

```powershell
PowerShell -ExecutionPolicy Bypass -File <script.ps1>
```

不建议永久修改系统执行策略。

## 20. Git 提交边界

通常可以提交：

- `src/`、`tests/`、`scripts/`；
- `strategies/` 不可变版本；
- 数据合同、manifest、说明和审计表；
- 不含受限像素的文档。

默认不提交：

- `.venv/`；
- `models/`、模型缓存和权重；
- `runs/`；
- `local_data/`；
- API key、Token、绝对 SSH 信息；
- 未获再分发许可的原图/候选；
- Reviewer ID 等个人信息。

Movie60 大图通过私有 Release 受控分享，不进入普通 Git tree。

## 21. 新开发同学验收任务

### 必做

- [ ] 在空目录 Clone；
- [ ] 新建本机 `.venv`；
- [ ] 安装依赖并物化模型；
- [ ] `pip check`、plugins、strategy show、pytest 通过；
- [ ] 新图跑出七候选；
- [ ] Evaluation 有七份指标与策略快照；
- [ ] 用 `score reference` 完成 Rule-only；
- [ ] 能解释 `agent_review_status=not_requested`；
- [ ] 两张图片完成批量演示；
- [ ] 能找到 OCR Source/Candidate 文本与框；
- [ ] 能解释一个 gate 为什么命中；
- [ ] 能用 `strategy diff` 比较版本。

### 不应发生

- [ ] 没有把 `.venv` 从别的电脑复制过来；
- [ ] 没有用 `--no-detectors` 形成正式结论；
- [ ] 没有把历史 Release 的机器列说成 v3.3.0；
- [ ] 没有覆盖旧 Run/Evaluation/Strategy；
- [ ] 没有调用未经授权的 Agent/AIGC/API；
- [ ] 没有把受限图片、模型或密钥提交 Git。
