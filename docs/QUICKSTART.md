# 快速开始

## 0. 填写公司 pip 镜像

在运行安装脚本之前，请向负责人取得公司镜像地址。这里故意留空：

```text
公司 pip index-url：________________________________________
公司 trusted-host（如需要）：_______________________________
负责人/日期：_______________________________________________
```

建议由公司统一写入用户级 pip 配置，而不是把内网地址、账号或 Token 提交到 Git：

```powershell
py -3.12 -m pip config set global.index-url <公司镜像地址>
# 只有公司明确要求时才配置 trusted-host。
```

## 1. Clone 与一次性安装

需要 Windows 10/11、Git、Python 3.11～3.13（推荐 3.12）和可访问私有仓库的权限。

```powershell
git clone <private-repository-url> retarget-abillity
cd retarget-abillity
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 -PythonVersion 3.12
```

Bootstrap 会创建或复用仓库内 `.venv`，安装项目和公司检测模型依赖，校验模型、当前
Strategy，并运行最小 Smoke。`.venv` 是本机环境，不能复制到另一台电脑；每台新电脑都
应在 Clone 后运行一次 Bootstrap。以后无需重复安装。

如果暂时只想打开已有评审数据，不跑保护检测，可先执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -PythonVersion 3.12 -SkipCompanyModels
```

这时 UI 和已有结果可用，但新图片完整生成尚未就绪。

## 2. 检查环境

```powershell
.\.venv\Scripts\retarget-engine.exe doctor
```

`ready=true` 表示核心 CLI、Rule、Strategy 与评审 UI 可用；
`generation_with_company_models=true` 才表示 PP-OCR、目标/人脸/Logo 保护分析也已就绪。
Agent/AIGC 默认显示未配置，这是安全默认值，不是安装失败。

## 3. 跑一张图片

```powershell
.\.venv\Scripts\retarget-engine.exe run image `
  "D:\images\poster.jpg" --target 1536x1536
```

`--target` 必须是实际像素 `WIDTHxHEIGHT`，例如 `1920x1080`、`1080x1920`、
`1200x900`。程序会自动冻结输入、保护分析、生成七候选、执行 current Rule，并打印：

- `run_dir`：完整可追溯 Run；
- `evaluation_id`：本次 Rule 结果；
- `candidate_count`：候选数量；
- `review_command`：打开同一套人工评审 UI 的命令。

PowerShell 兼容入口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "D:\images\poster.jpg" -Target "1536x1536"
```

## 4. 跑一批图片

输入目录只放要处理的 JPEG/PNG：

```powershell
.\.venv\Scripts\retarget-engine.exe run batch `
  "D:\images\batch01" --target 1080x1920
```

每张图片形成一个 Task，每个 Task 默认形成七个 Candidate；任何失败均记录在 Run，
不会用别的图片替代。

## 5. 打开评审页面

安装完成后可双击仓库根目录的 `START_REVIEW.bat`。也可以显式打开：

```powershell
.\.venv\Scripts\retarget-engine.exe review open "runs\<run-id>"
.\.venv\Scripts\retarget-engine.exe review latest
```

第一次使用 Movie60 数据：

```powershell
gh auth login
powershell -ExecutionPolicy Bypass -File scripts\materialize_review.ps1
```

脚本从私有 Release 下载、校验 SHA-256 并解压到 Git 忽略目录
`local_data\movie60-review-current`。之后双击 `START_REVIEW.bat`。

## 6. Agent（只有明确需要时）

复制示例但不要提交私有文件：

```powershell
Copy-Item configs\agent-profile.private.example.yaml `
  configs\agent-profile.private.yaml
$env:RETARGET_AGENT_API_KEY = "<本次会话的Token>"
.\.venv\Scripts\retarget-engine.exe run image poster.jpg --target 1536x1536 `
  --agent-profile configs\agent-profile.private.yaml
```

未传 `--agent-profile` 时，Agent 调用次数严格为 0，AIGC 也始终关闭。
