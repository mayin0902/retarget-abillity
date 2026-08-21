# Retarget Engine

面向海报、人物和业务图片的本地可回放重定向引擎。一次输入会生成七种传统候选，
通过当前 Rule 排序，并可显式启用视觉 Agent。Movie60 和新 Run 使用同一个人工评审页面。

## 第一次使用

安装前先向项目负责人取得公司 pip 镜像地址，并按
[QUICKSTART](docs/QUICKSTART.md#0-先确认公司-pip-镜像)填写；仓库不会保存公司凭据。

```powershell
git clone <private-repository-url> retarget-abillity
cd retarget-abillity
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 -PythonVersion 3.12
.\.venv\Scripts\retarget-engine.exe doctor
```

安装只做一次。以后双击 `START_REVIEW.bat` 即可优先打开最近完成的 Run；没有 Run 时回退
到当前 Movie60。

## 最常用命令

```powershell
# 一张图：生成七候选并按 current Rule 评分
.\.venv\Scripts\retarget-engine.exe run image D:\images\poster.jpg --target 1536x1536

# 一批图
.\.venv\Scripts\retarget-engine.exe run batch D:\images\batch01 --target 1920x1080

# 打开指定 Run 或最近 Run
.\.venv\Scripts\retarget-engine.exe review open runs\<run-id>
.\.venv\Scripts\retarget-engine.exe review latest

# 只比较一张原图和一张候选图
.\.venv\Scripts\retarget-engine.exe score reference source.jpg candidate.jpg
```

默认只运行本地 Rule，不调用 Agent、AIGC 或付费 API。Agent 必须通过
`--agent-profile configs\agent-profile.private.yaml` 显式启用。

## 文档

- [QUICKSTART](docs/QUICKSTART.md)：从 Clone、公司镜像、安装到单图/批量运行。
- [REVIEW_AND_SCORING](docs/REVIEW_AND_SCORING.md)：打开 UI、自动评分、人工结果位置。
- [ARCHITECTURE](docs/ARCHITECTURE.md)：保护分析、七算法、Rule、Agent 和统一评审接口。
- [ADVANCED](docs/ADVANCED.md)：Strategy、插件、Replay、Agent Profile 与版本追溯。

当前唯一 active Strategy 由 `strategies/registry.yaml` 决定；当前交付事实见
`CURRENT_RELEASE.json`。自动分数和 Agent 建议不是人工金标准。
