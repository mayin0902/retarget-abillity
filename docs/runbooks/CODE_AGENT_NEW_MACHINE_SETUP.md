# 本地 Code Agent：从 GitHub Clone 到单图完整运行

这是一份给本地 Code Agent 直接执行的操作合同。适用目标：在一台新的 Windows 机器上，从
私有 GitHub 仓库取得 `retarget-engine`，建立全新的 Python 环境，下载并校验本地分析模型，
再用一张本地图片跑通七种重定向、严格 Rule 评分、可选视觉 Agent 和人工评审网页。

详细算法、OCR/YOLO 输入输出、Quality 公式和 Artifact 解释见
[单图全流程操作手册](SINGLE_IMAGE_END_TO_END.md)。本文只负责从零安装和实际执行。

## 0. Code Agent 的执行边界

开始前必须遵守：

1. 不删除、覆盖已有仓库、`.venv`、Run 或数据集；目标路径存在时先停止并报告。
2. 不把 GitHub Token、SSH 私钥、API Key 写进命令、文件、日志或 Git remote URL。
3. 不上传本地图片，不调用付费 AIGC API，不执行 Git Push/Commit，除非用户另行明确授权。
4. 商业图片默认 `local only`；Agent 视觉预审使用自建 GPU 服务时，也应确认素材允许发送到
   该服务所在机器。
5. 任一步失败都要保留原始错误并停止，不得通过关闭 detector、跳过 hash 或伪造结果继续。
6. `.venv` 必须在新机器重新创建，不从旧机器复制。
7. 每个 Run、Evaluation、Agent Run、Review Run 使用新 ID，不覆盖历史证据。

## 1. 最终应该得到什么

不启用 GPU Agent 时也必须得到：

```text
GitHub Clone
  → Python .venv
  → OCR/YOLOX/YuNet 模型
  → 单图数据集合同
  → 1 个 Task
  → 7 个 1536×1536 候选
  → 7 份严格自动指标
  → Rule 完整排名
  → 本地人工评审网页
```

有可用 GPU Agent 服务时，再得到：

```text
Rule-aware 七候选总览
  → Agent Challenger
  → Rule Top1 与 Challenger 高清复核
  → fail-closed 最终机器选择
```

## 2. 检查或安装机器级前置软件

先打开普通 PowerShell：

```powershell
git --version
gh --version
py -0p
```

需要：

- Git；
- GitHub CLI（推荐用于私有仓库登录）；
- 64 位 Python 3.11 或 3.12；
- 可访问 GitHub、PyPI 和模型 manifest 中的 OpenCV Zoo 固定地址。

如果缺失，而且用户已经授权 Code Agent 安装机器级软件，可以运行：

```powershell
winget install --exact --id Git.Git
winget install --exact --id GitHub.cli
winget install --exact --id Python.Python.3.12
```

安装后关闭并重新打开 PowerShell，再检查：

```powershell
git --version
gh --version
py -3.12 --version
```

预期 Python 为 `3.12.x`。项目接受 `>=3.11,<3.14`，不要使用 3.10 或 3.14。

如果没有 `winget` 或没有机器级安装权限，停止并让用户从 Git、GitHub CLI、Python 官方页面
安装，不要使用来源不明的安装包。

## 3. 登录 GitHub 并 Clone 私有仓库

### 3.1 安全登录

```powershell
gh auth status --hostname github.com
```

如果尚未登录：

```powershell
gh auth login --hostname github.com --git-protocol https --web
```

浏览器中完成授权，然后再次执行 `gh auth status`。不要把 Personal Access Token 拼进 URL。

### 3.2 Clone

选择一个不存在的目标路径：

```powershell
$RetargetRepoRoot = 'G:\Projects\retarget-abillity'

if (Test-Path -LiteralPath $RetargetRepoRoot) {
  throw "目标路径已经存在，停止以避免覆盖：$RetargetRepoRoot"
}

gh repo clone mayin0902/retarget-abillity $RetargetRepoRoot
Set-Location $RetargetRepoRoot
```

核对仓库事实：

```powershell
git remote -v
git branch --show-current
git status --short --branch
git rev-parse HEAD
```

预期 remote 为：

```text
https://github.com/mayin0902/retarget-abillity.git
```

### 3.3 防止使用未发布的旧版本

GitHub Clone 只能获得已 Commit 且已 Push 的内容。必须检查本手册和单图工具确实存在：

```powershell
$required = @(
  'docs\runbooks\CODE_AGENT_NEW_MACHINE_SETUP.md',
  'docs\runbooks\SINGLE_IMAGE_END_TO_END.md',
  'scripts\prepare_single_image_dataset.py',
  'scripts\evaluate_movie60_strict.py',
  'scripts\build_rule_aware_agent_overviews.py',
  'scripts\run_movie60_rule_anchored_agent.py',
  'agent_skills\qwen4-selector\v7\skill.yaml',
  'strategies\movie60\v2\bundle.yaml',
  'requirements\constraints-py311-313.txt'
)

$missing = $required | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) {
  $missing
  throw 'Clone 缺少当前交接文件；停止，等待仓库所有者发布最新版本。'
}
```

## 4. 创建新的 Python `.venv`

`.venv` 是当前机器、当前仓库路径绑定的解释器和依赖目录，不是迁移包。
如果仓库和 Python 已经准备好、只想安装依赖，可改用更短的
[Python 依赖安装手册](PYTHON_DEPENDENCIES.md)。

```powershell
Set-Location $RetargetRepoRoot

if (Test-Path -LiteralPath '.venv') {
  throw '.venv 已存在；不要覆盖。请先确认它是否属于当前机器。'
}

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip --version
```

预期解释器路径位于：

```text
G:\Projects\retarget-abillity\.venv\Scripts\python.exe
```

不要求执行 `Activate.ps1`。所有命令显式使用 `.venv\Scripts\python.exe`，可避免误用系统
Python，也更适合 Code Agent 自动执行。

## 5. 安装 Python 项目依赖

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e ".[dev]"
```

这条命令的含义：

- `-e`：开发模式安装当前源码；修改 `src/` 后不必重新安装包；
- `.`：读取仓库根目录 `pyproject.toml`；
- `[dev]`：额外安装 pytest、Ruff、httpx 等开发依赖；
- 所有包安装进当前仓库的 `.venv`，不会安装进系统 Python。

核对安装：

```powershell
.\.venv\Scripts\python.exe -c "import cv2, numpy, PIL, pydantic; print('python dependencies OK')"
.\.venv\Scripts\retarget-engine.exe --help
```

如果新机器不能联网，可以从相同 Windows/Python 架构的机器准备 wheelhouse，但仍应在新机器
创建 `.venv` 并执行一次本地 pip 安装。不要直接复制旧 `.venv`。

## 6. 下载并校验 OCR、YOLOX、人脸模型

```powershell
.\.venv\Scripts\python.exe scripts\materialize_analyzer_models.py
```

该脚本只允许 manifest 中固定的 HTTPS host，且同时校验字节数与 SHA-256。之后检查：

```powershell
Get-ChildItem models\analyzers -File |
  Select-Object Name, Length

Get-ChildItem models\analyzers -File |
  Get-FileHash -Algorithm SHA256 |
  Select-Object Path, Hash
```

模型合同在：

```text
datasets/analyzer_models_v1/model_manifest.csv
```

禁止为了绕过模型下载失败把运行配置改为 `detector_mode: optional` 或 `disabled`。

## 7. 安装后自检

先跑轻量自检，再决定是否跑全仓：

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q tests\test_single_image_workflow_tools.py
```

交接验收建议跑完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

任一测试失败都要记录命令、退出码和错误；不要声称环境已完成。

## 8. 准备一张本地输入图

输入支持 JPEG 或 PNG。建议：

- 短边至少 1024 像素；
- 使用简体中文海报、人物或业务图；
- 确认有本地分析授权；
- 不把文件放入 Git 跟踪目录；
- 小图可以运行，但放大到 1536 不会凭空产生真实高清细节。

以下假设输入为：

```text
D:\retarget-input\poster.jpg
```

先确认图片存在且能解码：

```powershell
$RetargetInputImage = 'D:\retarget-input\poster.jpg'

if (-not (Test-Path -LiteralPath $RetargetInputImage -PathType Leaf)) {
  throw "输入图片不存在：$RetargetInputImage"
}

.\.venv\Scripts\python.exe -c "from PIL import Image; p=r'$RetargetInputImage'; im=Image.open(p); print(im.format, im.size, im.mode)"
```

## 9. 把单图冻结成数据集合同

`source-id`、`run-id` 只能使用小写英文字母、数字、`_`、`-`。

```powershell
.\.venv\Scripts\python.exe scripts\prepare_single_image_dataset.py `
  $RetargetInputImage `
  --output-dir local_data\datasets\single_image_demo `
  --source-id demo_poster `
  --run-id single-image-square-v1 `
  --scene-category movie_poster `
  --split calibration
```

预期输出：

```text
local_data/datasets/single_image_demo/
├── dataset.yaml
├── sources.csv
├── targets.csv
├── tasks.csv
├── run.yaml
└── images/demo_poster.jpg
```

脚本按原始字节复制图片、读取 EXIF 方向后的尺寸、计算 SHA-256，并拒绝覆盖既有目录。

## 10. 校验数据集

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate `
  local_data\datasets\single_image_demo
```

成功标准：

```text
valid: true
task_count: 1
errors: []
```

如果 hash、尺寸、路径或 Task ID 不一致，停止修复数据合同，不要进入生成。

## 11. 生成七种 1:1 候选

```powershell
.\.venv\Scripts\retarget-engine.exe run generate `
  local_data\datasets\single_image_demo\run.yaml
```

成功后检查：

```powershell
$RetargetRun = 'runs\single-image-square-v1'
$manifest = Get-Content -Raw -Encoding UTF8 "$RetargetRun\run.json" | ConvertFrom-Json

$manifest | Select-Object run_id, status, methods, task_ids,
  candidate_ids, failed_candidate_ids
```

成功标准：

- `status=COMPLETED`；
- 1 个 Task；
- 7 个 candidate ID；
- `failed_candidate_ids` 为空。

七张图位于：

```text
runs/single-image-square-v1/candidates/demo_poster__square-1536/
├── direct_warp/candidate.png
├── crop/candidate.png
├── seam/candidate.png
├── seam_full/candidate.png
├── mesh/candidate.png
├── mesh_full/candidate.png
└── seam_scale/candidate.png
```

保护分析位于：

```text
analysis/demo_poster__square-1536/analysis.json
analysis/demo_poster__square-1536/importance.png
analysis/demo_poster__square-1536/tolerance.png
```

## 12. 严格自动评分

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_movie60_strict.py `
  $RetargetRun `
  --evaluation-id strict-auto-v2 `
  --strategy strategies\movie60\v2\bundle.yaml
```

打印每个方法的核心指标：

```powershell
$RetargetTask = 'demo_poster__square-1536'

Get-ChildItem "$RetargetRun\evaluations\strict-auto-v2\metrics\$RetargetTask--*.json" |
  ForEach-Object {
    $metric = Get-Content -Raw -Encoding UTF8 $_ | ConvertFrom-Json
    [pscustomobject]@{
      method = ($metric.candidate_id -split '--')[-2]
      quality = [math]::Round([double]$metric.metrics.quality_score, 2)
      proxy_grade = $metric.metrics.proxy_grade
      ocr_recall = $metric.metrics.ocr_character_recall
      person = $metric.metrics.person_count_preservation
      face = $metric.metrics.face_count_preservation
      product = $metric.metrics.product_count_preservation
      logo = $metric.metrics.logo_count_preservation
      hard_failures = $metric.metrics.hard_failures
      regressions = $metric.metrics.critical_regressions
    }
  } | Sort-Object quality -Descending | Format-Table -AutoSize
```

当前 v2 的 proxy 范围是 A≥90、B≥72、C≥60、其余 D；范围来自策略快照，仍只是未校准
机器代理，不是人工等级。

## 13. 生成 Rule-aware Agent 总览输入

即使暂时没有 GPU，也可以先生成 Agent 将要看到的总览：

```powershell
.\.venv\Scripts\python.exe scripts\build_rule_aware_agent_overviews.py `
  $RetargetRun `
  --evaluation-id strict-auto-v2 `
  --input-id rule-aware-v7
```

预期文件：

```text
runs/single-image-square-v1/agent-inputs/rule-aware-v7/
└── demo_poster__square-1536.png
```

它包含 SOURCE、七候选、完整 Rule 排名、Rule Top1 和核心自动指标。

## 14. 可选：运行 GPU 视觉 Agent

没有 GPU endpoint 时跳到第 15 节，明确报告“Rule 已完成，Agent 未执行”，不要伪造 Agent
结果。GPU 部署和 SSH tunnel 详见
[单图全流程手册第 2.3 节](SINGLE_IMAGE_END_TO_END.md#23-linux-gpu视觉-agent-环境与-cpu-引擎分开)。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:18101/v1/models
```

### 14.1 Agent 七候选总览

```powershell
.\.venv\Scripts\python.exe scripts\run_movie60_rule_anchored_agent.py overview `
  $RetargetRun `
  --evaluation-id strict-auto-v2 `
  --phase calibration `
  --backend-url http://127.0.0.1:18101/v1 `
  --model qwen3vl-4b `
  --strategy strategies\movie60\v2\bundle.yaml `
  --timeout-seconds 120 `
  --agent-run-id single-agent-overview-v1 `
  --comparison-dir "$RetargetRun\agent-inputs\rule-aware-v7"
```

### 14.2 Rule Top1 与 Agent Challenger 高清复核

```powershell
.\.venv\Scripts\python.exe scripts\run_movie60_rule_anchored_agent.py review `
  $RetargetRun `
  --evaluation-id strict-auto-v2 `
  --phase calibration `
  --backend-url http://127.0.0.1:18101/v1 `
  --model qwen3vl-4b `
  --strategy strategies\movie60\v2\bundle.yaml `
  --timeout-seconds 120 `
  --overview-agent-run-id single-agent-overview-v1 `
  --review-run-id single-rule-anchor-v1
```

最终机器选择：

```text
runs/single-image-square-v1/strict-reviews/single-rule-anchor-v1/
├── candidate-sheets/
├── candidate-reviews/
├── pair-sheets/
├── pair-reviews/
├── decisions/demo_poster__square-1536.json
└── summary.json
```

失败、超时、Schema 无效或证据矛盾时必须保留 Rule Top1。

## 15. 启动人工评审网页

```powershell
.\.venv\Scripts\retarget-engine.exe review web `
  $RetargetRun `
  --host 127.0.0.1 `
  --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

保持当前 PowerShell 窗口运行。结束时在该窗口按 `Ctrl+C`。不要把 host 改成 `0.0.0.0`，
除非用户明确要求局域网访问并已经处理鉴权和防火墙。

## 16. Code Agent 最终必须汇报的内容

使用以下格式，不要只回复“安装成功”：

```text
仓库
- clone path:
- remote:
- branch:
- commit:
- working tree:

Python
- Python version:
- venv interpreter:
- dependency install result:

分析模型
- materialize result:
- 5 个文件 hash 是否匹配:

测试
- Ruff:
- focused pytest:
- full pytest（如执行）:

单图数据
- source path:
- source sha256:
- source dimensions:
- dataset fingerprint:
- task_id:

Generation
- run_id:
- run status:
- candidate count:
- failed count:
- 各方法 wall time:

Evaluation
- evaluation_id:
- 七方法 Quality / proxy grade:
- hard failures / critical regressions:
- Rule Top1 与完整排名:

Agent
- endpoint health:
- Agent 是否执行:
- agent/review run IDs:
- Rule Top1:
- challenger:
- final selection:
- override block reasons:

人工评审
- UI URL:
- 未完成事项:
```

## 17. Code Agent 的完成标准

只有同时满足以下条件才可声称“本地 CPU 全流程完成”：

- Clone 的确来自目标 private repository；
- Python 版本满足约束，解释器位于新建 `.venv`；
- 依赖 import 成功；
- OCR/YOLOX/YuNet 模型全部通过 pin 校验；
- 数据集 `valid=true`；
- Generation Run 为 `COMPLETED`，7 个候选、0 失败；
- Evaluation 有且只有 7 个 metric；
- Rule-aware 总览图存在；
- 人工网页能够从 `127.0.0.1` 打开；
- 所有未执行项，尤其 GPU Agent，已明确报告。

只有额外完成 endpoint 健康检查、Agent 总览、高清 pair gate，并生成完整 decision/summary，
才可声称“Agent 全流程完成”。
