# 从零开始

这份文档面向第一次接手项目的 Windows 开发同学。完成后可以：安装隔离环境、检查检测
模型、运行单张或批量重定向、取得正式 Rule 结果，并打开统一人工评审页面。

## 0. 先确认公司 pip 镜像

安装前请让公司开发同学完成**用户级** pip 镜像配置。本项目不保存、不读取镜像地址、账号或
Token。开发者只需要检查当前用户是否已经能看到公司配置：

```powershell
py -3.12 -m pip config list
```

如果看不到公司源，先停止安装并联系公司环境负责人。本仓库不提供、猜测或写入公司镜像
命令。配置完成后，`pip`、Bootstrap 和本地 Code Agent 会继承同一用户级配置。

## 1. 新电脑前置条件

- Windows 10/11；
- Git；
- Python 3.11～3.13，推荐 3.12；优先使用 `py -3.12`，不可用时 Bootstrap 会校验 PATH
  中的 `python`，并识别 PATH 中 Conda 的 base Python；也可显式传 `-PythonExecutable`；
- GitHub 仓库读取权限；
- 如需下载受控的 Movie60 数据 Release：安装 GitHub CLI，并完成 `gh auth login`。

检查：

```powershell
git --version
py -3.12 --version
python --version
gh auth status
```

### 1.1 电脑没有 Python，或版本不在 3.11～3.13

推荐安装 64 位 Python 3.12。二选一：

```powershell
# Windows 10/11 有 winget 时，在普通 PowerShell 执行
winget install --exact --id Python.Python.3.12
```

也可以从 Python 官方 Windows 下载页安装 3.12 x64。图形安装器第一页勾选
`Add python.exe to PATH`，完成后**关闭并重新打开 PowerShell**，再验证：

```powershell
py -3.12 --version
python --version
```

任一命令显示 3.11、3.12 或 3.13 即可继续。若公司已经提供固定 Python/Conda，但没有加入
Launcher，可以显式指定，不必重复安装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonExecutable "D:\公司批准的Python目录\python.exe"
```

Bootstrap 不会自动执行系统级安装，也不会修改 PATH；找不到受支持版本时会停止并打印上述
`winget` 建议，避免在公司电脑上静默变更系统软件。

## 2. Clone 并检查仓库

```powershell
git clone <repository-url> retarget-abillity
cd retarget-abillity
git status --short --branch
```

至少应看到以下入口；缺失说明下载的不是完整代码版本：

```text
pyproject.toml
START_REVIEW.bat
configs/default.yaml
strategies/registry.yaml
scripts/bootstrap_windows.ps1
src/retarget_agent/
docs/QUICKSTART.md
```

## 3. 一次性安装

完整安装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonVersion 3.12
```

Bootstrap 依次完成：

1. 在仓库内创建 `.venv`；
2. 安装固定版本的构建工具和本项目；
3. 安装 PP-OCRv6、D-FINE、YuNet 等公司 CPU 检测依赖；
4. 只从当前 manifest 物化 YuNet 固定资产，再由当前 detector profile 物化 PP-OCRv6 与
   固定 revision 的 D-FINE；旧 PPOCRv3/CRNN/YOLOX 不在普通 Bootstrap 主路径；
5. 校验历史与 current Strategy；
6. 跑最小测试；
7. 执行 `doctor`。

成功标志是最后打印 `Bootstrap completed.`。`.venv` 只是本机解释器和依赖目录，不是可
迁移发布物；换电脑或换 Python 后应重新 Bootstrap，而不是复制 `.venv`。

只需先打开已有评审数据、暂不处理新图时，可以安装轻量模式：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonVersion 3.12 -SkipCompanyModels
```

轻量模式不能代表 OCR/人物/商品保护检测已就绪。

## 4. 检查环境

```powershell
.\.venv\Scripts\retarget-engine.exe doctor
```

- `ready=true`：CLI、Strategy、Rule 与 UI 可用；
- `generation_with_company_models=true`：新图片的完整保护分析可用；
- Agent/AIGC 未配置：是默认安全状态，不是安装失败。

如公司模型不完整，重新运行完整 Bootstrap；不要手工把权重提交 Git。

### 公司网络下固定模型的 SSL 降级

模型下载默认始终执行 HTTPS 证书校验。仅当固定模型资源捕获到
`requests.exceptions.SSLError` 时，`materialize_analyzer_models.py` 才会打印明确 warning，
对同一 allowlist Host 使用 `verify=False` 重试，并在落盘前强制核对 manifest 中的
`expected_bytes` 与固定 SHA-256。重定向后的 Host 也必须在 allowlist；校验失败会删除 `.part`
并终止 Bootstrap。

这意味着降级请求关闭了 TLS 服务端身份验证，但下载产物仍通过固定 SHA-256 和字节数验证
完整性与预期内容。该例外只能用于有固定 pin 的模型资产，不能复制到 pip、普通 API、Agent
或 AIGC 请求。

## 5. 下载并打开 Movie60 评审数据

首次物化：

```powershell
gh auth login
powershell -ExecutionPolicy Bypass -File scripts\materialize_review.ps1
```

脚本从 `MOVIE60_RELEASE.json` 指向的 GitHub Release 下载当前资产，按随包
`SHA256SUMS.txt` 校验 SHA-256，再解压到
Git 忽略目录 `local_data\movie60-review-current`。已有且校验通过时脚本会直接复用，不重复
下载。`v0.8.0` Release 中的 Movie60 v4 core、完整 evidence 和校验文件仍是当前数据资产；
其中同时保存的 `0.8.0` Wheel 只用于重现该历史软件版本。Clone 当前 `main` 后应按第 3 节
Bootstrap 安装 `0.8.1` 源码，不要用旧 Wheel 覆盖当前环境。`v0.7.1`/Movie60 v3 和更早的
Pre-release 只保留作历史追溯。

Release 的可见性由 GitHub 仓库和 Release 状态决定。Movie60 含受控素材时，维护者应使用
私有仓库或 Draft Release；不要因为代码仓可公开就默认素材也允许公开再分发。

### Movie60 无法在线下载怎么办

如果公司网络不能执行 `gh release download`：

1. 在浏览器打开 `MOVIE60_RELEASE.json` 中 `github_release_tag` 对应的 Release；
2. 下载 `release_asset_names` 列出的三个文件。当前是：
   - `movie60-review-v4-core.zip`
   - `movie60-review-v4-evidence.zip`
   - `SHA256SUMS.txt`
3. **不要解压、不要改名**，放入：

   ```text
   local_data\release_assets\v0.8.0\
   ```

4. 再执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\materialize_review.ps1
   ```

脚本会优先发现完整的本地三件套，不访问 GitHub；文件不齐才回到在线下载。只有两个 ZIP
不够，必须同时有 `SHA256SUMS.txt`。

```text
local_data\release_assets\v0.8.0\
= 浏览器下载的原始压缩包

local_data\movie60-review-current\
= 经过 SHA-256、ZIP CRC 和安全路径校验后，真正供 UI/示例使用的数据
```

显式打开 Movie60：

```powershell
.\.venv\Scripts\retarget-engine.exe review open `
  "local_data\movie60-review-current"
```

## 6. 完整运行一张图片

先完成上一节的 Movie60 物化，再直接运行真实简体中文海报：

```powershell
.\.venv\Scripts\retarget-engine.exe run image `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  --target 1536x1536 `
  --scene movie_poster
```

可替换为以下真实场景源图：

```text
海报：all60\tasks\poster_001__square-1536\00_source.jpg
人物：all60\tasks\person_001__square-1536\00_source.jpg
剧照：all60\tasks\still_001__square-1536\00_source.jpg
视频封面：all60\tasks\video_cover_001__square-1536\00_source.jpg
```

`--scene` 支持 `movie_poster`、`film_still`、`video_cover`、`person`、`product` 和
`unspecified`。没传时会明确警告：新图没有场景类型，当前 Strategy 的场景化门禁不会触发。
不要用 `unspecified` 冒充已执行海报/人物专用 Rule。

`--target` 是实际输出像素，格式固定为 `WIDTHxHEIGHT`。长期测试覆盖：

```text
1536x1536  1:1
1920x1080  16:9
1080x1920  9:16
1200x900   4:3
900x1200   3:4
```

这些 Smoke 证明 Generation、Rule、结果落盘和 UI 的工程链路支持多种尺寸；当前
`retarget@1.0.0` 继承的 `movie60@3.3.0` 人工阈值证据仍主要来自 Movie60 1:1。不要把
“代码能跑 16:9/9:16”解释为这些比例的 A/B/C/D 已完成人工校准。

不传 `--target` 时读取 `configs/default.yaml` 的 `default_target`。方法 profile、检测 profile、
Run 根目录和本地 UI host/port 也从同一文件读取；唯一 active Strategy 仍只由
`strategies/registry.yaml` 决定。

一条命令内部完成：冻结输入 → 原图保护分析 → 七方法生成 → 每个成功候选重新检测 →
current Rule 评分 → 冻结完整 Rule 排名 → 导出最终 Rule Top1。

终端会输出 `run_dir`、`evaluation_id`、七方法分母和评审命令。单图 Run 还会直接生成：

```text
runs/<run-id>/
├── result.png                         # 正式 Rule Top1 大图
├── result.json                        # 方法、分数、等级、完整排名和证据路径
├── evaluations/<evaluation-id>/
│   ├── metrics/                       # 每个候选的 Rule 指标
│   └── rule-decisions/<task-id>.json  # 正式冻结的 Rule 排名与选择
└── candidates/                        # 七种方法的原始候选与失败记录
```

PowerShell 包装入口等价：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  -Target "1536x1536" -Scene movie_poster
```

## 7. 批量运行

批量入口只扫描输入目录顶层的 JPEG/PNG，并对整批应用同一个 `--scene`。真实 Smoke 可以先把
Movie60 的四张源图复制到一个忽略目录：

```powershell
New-Item -ItemType Directory -Force local_data\demo-batch | Out-Null
Copy-Item local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg `
  local_data\demo-batch\poster.jpg
Copy-Item local_data\movie60-review-current\all60\tasks\poster_002__square-1536\00_source.jpg `
  local_data\demo-batch\poster-2.jpg
.\.venv\Scripts\retarget-engine.exe run batch `
  "local_data\demo-batch" --target 1080x1920 --scene movie_poster
```

每张图对应一个 Task，每个 Task 的默认分母固定为七方法。结果位于：

```text
runs/<run-id>/results/<evaluation-id>/<task-id>/result.png
runs/<run-id>/results/<evaluation-id>/<task-id>/result.json
```

单个方法失败不会被隐藏或用其他图片补分母；评审页会显示 `N/A`、失败类型和摘要。

## 8. 打开最新 Run 或指定 Run

双击根目录 `START_REVIEW.bat`：优先打开最近一个已完成或部分完成的 Run；当前没有 Run
时才回退到 Movie60。

也可显式执行：

```powershell
.\.venv\Scripts\retarget-engine.exe review latest
.\.venv\Scripts\retarget-engine.exe review open "runs\<run-id>"
```

旧命令 `review web` 仅为兼容别名，内部同样进入 `review open` 的统一页面。

## 9. 只评分，不重新生成

有原图和候选图：

```powershell
.\.venv\Scripts\retarget-engine.exe score reference `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\candidates\crop.png"
```

只有候选图：

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\candidates\crop.png"
```

Reference 模式能比较内容保留；Standalone 只能报告清晰度、尺寸等无参考风险，不能证明
语义完整。详见 `docs/REVIEW_AND_SCORING.md`。

## 10. 在 Python 中直接调用

给其他 Python 服务集成时，优先使用内存 API。它接收 RGB `numpy.ndarray`，不要求先建立
Dataset，也不会自动写 Run：

```python
from PIL import Image
import numpy as np

from retarget_agent.api import generate_candidates, retarget_image, score_pair

source = np.asarray(Image.open("input.jpg").convert("RGB"))

# 单一方法
crop = retarget_image(source, target=(1536, 1536), method="crop", scene="movie_poster")
assert crop.succeeded

# 多方法；单个方法失败会保留失败记录，不会缩小分母
candidates = generate_candidates(
    source,
    target=(1536, 1536),
    methods=("crop", "seam", "mesh"),
    scene="movie_poster",
)

# 原图与候选图的正式 Rule 对比评分
score = score_pair(
    source,
    crop.image,
    scene="movie_poster",
    transform=crop.transform,
)
print(
    score.quality_score,
    score.grade,
    score.content_fidelity,
    score.visual_integrity,
    score.composition,
    score.gates,
)
```

批量服务应复用同一个 `ProtectionAnalyzerCore`，避免为每张候选重复加载 OCR、人物、商品和
Logo 模型。完整对象生命周期、输入输出与 Java 服务适配建议见 `docs/CODE_GUIDE.md`。

从仓库运行时，`score_pair()` 默认读取 `strategies/registry.yaml` 的唯一 active Strategy；
只安装 v0.8.0 Wheel、没有仓库目录时，则使用 Wheel 内同哈希的 `retarget@1.0.0` 快照。
需要固定历史口径时应显式传入 Strategy 路径或 `LoadedStrategyBundle`，不要依赖当前默认值。

## 11. 显式启用 Agent

普通命令严格 Rule-only。需要 Agent 时复制私有 Profile：

```powershell
Copy-Item configs\agent-profile.private.example.yaml `
  configs\agent-profile.private.yaml
$env:RETARGET_AGENT_API_KEY = "<本次会话Token>"
.\.venv\Scripts\retarget-engine.exe run image `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  --target 1536x1536 --scene movie_poster `
  --agent-profile configs\agent-profile.private.yaml
```

Profile 与 Token 都不应提交。未传 `--agent-profile` 时 Agent 调用次数为 0；普通工作流也
不会自动调用付费 AIGC。

## 12. 显式调用可替换 AIGC Provider

先确认 Provider 已注册：

```powershell
.\.venv\Scripts\retarget-engine.exe plugins list
```

第一次只做 Preflight。下面命令不访问网络、不读取 API Key、不创建输出目录：

```powershell
.\.venv\Scripts\retarget-engine.exe generation run `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  --provider seedream_api `
  --output-root "local_data\generation-provider-smoke" `
  --request-id "seedream-preflight-001" `
  --task-id "poster-001" `
  --target 1536x1536 `
  --prompt-file "strategies\retarget\v1\prompts\seedream-generation.txt"
```

输出 `status=planned` 表示命令和 Provider ID 有效，不代表 API 配置或生图质量已经验证。

真正调用时，在当前 PowerShell 临时配置该 Adapter 需要的环境变量，并显式增加 `--execute`：

```powershell
$env:SEEDREAM_BASE_URL = "<负责人提供的 HTTPS Endpoint>"
$env:SEEDREAM_API_KEY = "<本次会话 Token>"
$env:SEEDREAM_MODEL = "<负责人确认的模型标识>"

.\.venv\Scripts\retarget-engine.exe generation run `
  "local_data\movie60-review-current\all60\tasks\poster_001__square-1536\00_source.jpg" `
  --provider seedream_api `
  --output-root "local_data\generation-provider-smoke" `
  --request-id "seedream-paid-001" `
  --task-id "poster-001" `
  --target 1536x1536 `
  --prompt-file "strategies\retarget\v1\prompts\seedream-generation.txt" `
  --execute --budget-cny 0.60 --timeout-seconds 300
```

`--execute` 表示调用者已经按公司要求确认素材可以发送给所选 API；通用 Provider 不内置某一
套业务素材审批规则。`--budget-cny` 和 `--idempotency-key` 都是可选控制。成功或失败都会写入：

```text
local_data/generation-provider-smoke/
├── executions/<request-id>.json
├── provider-artifacts/<provider-id>/
└── provider-cache/
```

执行记录包含耗时、输出 Hash、图片尺寸、错误和可取得的成本，不保存 API Key。接入另一家
AIGC API 只新增 Adapter 并注册，详见 `EXTENSION_GUIDE.md`。部分厂商只接受 `1K/2K` 等尺寸
档位，此时审计记录保存厂商实际返回尺寸；若下游要求精确像素尺寸，由 Adapter 或后续本地
重定向步骤显式归一化，不能把请求尺寸冒充为实际尺寸。

`generation run` 到“生成图片 + `execution.json`”为止，不会修改已有 Generation Run、
Evaluation 或 Rule 决策。需要评价生成结果时，使用 `score reference` 或 Python
`score_pair()` 比较原图与 AIGC 图；需要参与业务最终选择时，由上层编排把该评分与传统
Rule Top1 比较后再形成路由结论。它不是一条隐式的“Agent + AIGC 自动路由”。

## 13. 常见错误

- `py -3.12` 不存在：先安装 Python 3.12，重新打开 PowerShell；
- `.venv exists but has no Windows Python`：把损坏目录改名保留，再重新 Bootstrap；
- `generation_with_company_models=false`：运行完整 Bootstrap；
- UI 端口占用：传 `--port 8766`；
- 旧版控制台不支持中文：CLI 会自动把终端 JSON 中的中文显示为 `\uXXXX`，评分文件仍以
  UTF-8 保存且内容不丢失；想在当前窗口直接显示中文，可先执行 `chcp 65001`；
- Release 下载失败：检查 `gh auth status` 和仓库 Release 权限，或按第 5 节把完整三件套
  放入 `local_data\release_assets\<github_release_tag>\`；
- 新 Run 没有 Evaluation：用 `run image/batch` 完整入口，或按 `ADVANCED.md` 手工 evaluate。
