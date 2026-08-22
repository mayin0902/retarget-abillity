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
- Python 3.11～3.13，推荐 3.12，且 `py -3.12` 可运行；
- 私有 GitHub 仓库读取权限；
- 如需下载 Movie60 私有数据 Release：安装 GitHub CLI，并完成 `gh auth login`。

检查：

```powershell
git --version
py -3.12 --version
gh auth status
```

## 2. Clone 并检查仓库

```powershell
git clone <private-repository-url> retarget-abillity
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

脚本从 `CURRENT_RELEASE.json` 指向的私有 GitHub Release 下载当前资产，按随包
`SHA256SUMS.txt` 校验 SHA-256，再解压到
Git 忽略目录 `local_data\movie60-review-current`。已有且校验通过时脚本会直接复用，不重复
下载。`v0.7.1` 起，Wheel、Movie60 core、完整 evidence 和校验文件位于同一个 Release；
原 `movie60-review-v3` Pre-release 只保留作历史追溯。

### Movie60 无法在线下载怎么办

如果公司网络不能执行 `gh release download`：

1. 在浏览器打开 `CURRENT_RELEASE.json` 中 `github_release_tag` 对应的私有 Release；
2. 下载 `release_asset_names` 列出的三个文件。当前是：
   - `movie60-review-v3-core.zip`
   - `movie60-review-v3-evidence.zip`
   - `SHA256SUMS.txt`
3. **不要解压、不要改名**，放入：

   ```text
   local_data\release_assets\v0.7.1\
   ```

4. 再执行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\materialize_review.ps1
   ```

脚本会优先发现完整的本地三件套，不访问 GitHub；文件不齐才回到在线下载。只有两个 ZIP
不够，必须同时有 `SHA256SUMS.txt`。

```text
local_data\release_assets\v0.7.1\
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
`unspecified`。没传时会明确警告：新图没有场景类型，3.3 的场景化门禁不会触发。不要用
`unspecified` 冒充已执行海报/人物专用 Rule。

`--target` 是实际输出像素，格式固定为 `WIDTHxHEIGHT`。长期测试覆盖：

```text
1536x1536  1:1
1920x1080  16:9
1080x1920  9:16
1200x900   4:3
900x1200   3:4
```

这些 Smoke 证明 Generation、Rule、结果落盘和 UI 的工程链路支持多种尺寸；当前
`movie60@3.3.0` 的人工阈值证据仍主要来自 Movie60 1:1。不要把“代码能跑 16:9/9:16”解释为
这些比例的 A/B/C/D 已完成人工校准。

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

## 10. 显式启用 Agent

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

## 11. 常见错误

- `py -3.12` 不存在：先安装 Python 3.12，重新打开 PowerShell；
- `.venv exists but has no Windows Python`：把损坏目录改名保留，再重新 Bootstrap；
- `generation_with_company_models=false`：运行完整 Bootstrap；
- UI 端口占用：传 `--port 8766`；
- 私有 Release 下载失败：检查 `gh auth status` 和仓库 Release 权限，或按第 5 节把完整三件套
  放入 `local_data\release_assets\<github_release_tag>\`；
- 新 Run 没有 Evaluation：用 `run image/batch` 完整入口，或按 `ADVANCED.md` 手工 evaluate。
