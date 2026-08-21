# 从零开始

这份文档面向第一次接手项目的 Windows 开发同学。完成后可以：安装隔离环境、检查检测
模型、运行单张或批量重定向、取得正式 Rule 结果，并打开统一人工评审页面。

## 0. 先确认公司 pip 镜像

安装前请向负责人取得公司镜像信息。这里故意留空，不要把内网地址、账号或 Token 提交到
Git：

```text
公司 pip index-url：________________________________________
公司 trusted-host（如需要）：_______________________________
负责人/日期：_______________________________________________
```

先检查当前用户是否已有公司配置：

```powershell
py -3.12 -m pip config list
```

没有时由负责人指导写入用户级配置，例如：

```powershell
py -3.12 -m pip config set global.index-url <公司镜像地址>
# 只有公司明确要求时才设置 trusted-host。
```

这样项目内没有私有源，`pip`、Bootstrap 和本地 Code Agent 都会继承同一配置。

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
4. 物化并审计模型文件；
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

## 5. 下载并打开 Movie60 评审数据

首次物化：

```powershell
gh auth login
powershell -ExecutionPolicy Bypass -File scripts\materialize_review.ps1
```

脚本从私有 GitHub Release 下载当前资产，按 `CURRENT_RELEASE.json` 校验 SHA-256，再解压到
Git 忽略目录 `local_data\movie60-review-current`。已有且校验通过时脚本会直接复用，不重复
下载。

显式打开 Movie60：

```powershell
.\.venv\Scripts\retarget-engine.exe review open `
  "local_data\movie60-review-current"
```

## 6. 完整运行一张图片

```powershell
.\.venv\Scripts\retarget-engine.exe run image `
  "D:\images\poster.jpg" --target 1536x1536
```

`--target` 是实际输出像素，格式固定为 `WIDTHxHEIGHT`。长期测试覆盖：

```text
1536x1536  1:1
1920x1080  16:9
1080x1920  9:16
1200x900   4:3
900x1200   3:4
```

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
  -InputImage "D:\images\poster.jpg" -Target "1536x1536"
```

## 7. 批量运行

输入目录只放本批 JPEG/PNG：

```powershell
.\.venv\Scripts\retarget-engine.exe run batch `
  "D:\images\batch01" --target 1080x1920
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
  "D:\images\source.jpg" "D:\images\candidate.png"
```

只有候选图：

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "D:\images\candidate.png"
```

Reference 模式能比较内容保留；Standalone 只能报告清晰度、尺寸等无参考风险，不能证明
语义完整。详见 `docs/REVIEW_AND_SCORING.md`。

## 10. 显式启用 Agent

普通命令严格 Rule-only。需要 Agent 时复制私有 Profile：

```powershell
Copy-Item configs\agent-profile.private.example.yaml `
  configs\agent-profile.private.yaml
$env:RETARGET_AGENT_API_KEY = "<本次会话Token>"
.\.venv\Scripts\retarget-engine.exe run image "poster.jpg" `
  --target 1536x1536 --agent-profile configs\agent-profile.private.yaml
```

Profile 与 Token 都不应提交。未传 `--agent-profile` 时 Agent 调用次数为 0；普通工作流也
不会自动调用付费 AIGC。

## 11. 常见错误

- `py -3.12` 不存在：先安装 Python 3.12，重新打开 PowerShell；
- `.venv exists but has no Windows Python`：把损坏目录改名保留，再重新 Bootstrap；
- `generation_with_company_models=false`：运行完整 Bootstrap；
- UI 端口占用：传 `--port 8766`；
- 私有 Release 下载失败：检查 `gh auth status` 和仓库 Release 权限；
- 新 Run 没有 Evaluation：用 `run image/batch` 完整入口，或按 `ADVANCED.md` 手工 evaluate。
