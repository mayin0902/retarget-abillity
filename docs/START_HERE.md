# 从零开始：安装、素材、单图、Rule、Agent、人工评审

这是 `retarget-abillity` 的首要交接入口。新机器只需要私有仓库权限、本地合法图片，以及在
执行视觉 Agent 时可用的 GPU 服务。历史聊天、开发提示词和中间实验目录都不是运行依赖。

## 1. Clone 与一键安装

在 Windows PowerShell 中执行：

```powershell
gh auth login --hostname github.com --git-protocol https --web
gh repo clone mayin0902/retarget-abillity G:\Projects\retarget-abillity
Set-Location G:\Projects\retarget-abillity
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
```

安装脚本会：

1. 创建不可迁移、不可覆盖的 `.venv`；
2. 按 `requirements/constraints-py311-313.txt` 安装冻结依赖；
3. 下载 OCR、YOLOX、YuNet 模型并校验字节数和 SHA-256；
4. 校验 Strategy v1、v2；
5. 运行安装 Smoke。

需要同时下载内部 Movie60 评审资产时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 `
  -WithMovie60Release
```

Release 下载脚本会核对 SHA-256、ZIP CRC 和解压路径，不会覆盖已有目录。素材仅供获授权的
私有仓库协作者内部评审，不代表获得公开再分发权。

## 2. 一条命令跑完单图 Rule 流程

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage D:\retarget-input\poster.jpg `
  -CaseId poster-demo
```

固定结果：

```text
local_data/datasets/poster-demo/                 输入数据合同和原图副本
runs/poster-demo-square-v1/analysis/             OCR/人脸/YOLOX/Logo/保护图
runs/poster-demo-square-v1/candidates/           七种方法的高清图片和变换证据
runs/poster-demo-square-v1/evaluations/
  poster-demo-rule-v2/metrics/                   每候选 Quality、等级和原因指标
  poster-demo-rule-v2/strategy/                  本次实际规则、Skill 与哈希快照
```

脚本拒绝覆盖同名数据集和 Run。再次执行必须使用新的 `CaseId`。

## 3. 查看、切换和比较规则版本

```powershell
.\.venv\Scripts\retarget-engine.exe strategy show `
  strategies\movie60\v2\bundle.yaml

.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v1\bundle.yaml `
  strategies\movie60\v2\bundle.yaml
```

重跑旧版 Rule 不需要重新生成候选：

```powershell
.\.venv\Scripts\retarget-engine.exe evaluate `
  runs\poster-demo-square-v1 `
  --evaluation-id poster-demo-rule-v1 `
  --strategy strategies\movie60\v1\bundle.yaml
```

此时 v1、v2 分别在两个 Evaluation 目录中并存，各自带完整策略快照。

## 4. 启动人工评审网页

```powershell
.\.venv\Scripts\retarget-engine.exe review web `
  runs\poster-demo-square-v1 `
  --host 127.0.0.1 `
  --port 8766
```

浏览器打开 `http://127.0.0.1:8766/`。网页只绑定本机，人工等级和理由写入 Run，不覆盖机器
评分。Movie60 Release 自带已有机器理由、人工表格和候选图片，可直接作为扩充素材库的示例。

## 5. 视觉 Agent

Rule 不需要 GPU。Agent 需要一个只在本机或 SSH tunnel 上可访问的 OpenAI-compatible 视觉
模型端点。部署细节见 [单图完整手册](runbooks/SINGLE_IMAGE_END_TO_END.md)。端点就绪后：

```powershell
.\.venv\Scripts\retarget-engine.exe agent replay `
  runs\poster-demo-square-v1 `
  --evaluation-id poster-demo-rule-v2 `
  --agent-run-id poster-demo-agent-v2 `
  --mode always_on_agent `
  --backend-url http://127.0.0.1:18101/v1 `
  --model <内部模型部署名> `
  --strategy strategies\movie60\v2\bundle.yaml `
  --comparison-dir runs\poster-demo-square-v1\agent-inputs\rule-aware-v2
```

Agent 只能提出 Challenger；最终覆盖还必须经过 Rule Top1 与 Challenger 的高清配对复核和
`override.yaml` 门禁。无明确证据、主体/文字/数量下降或证据冲突时保留 Rule。

## 6. 交接验收

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe -m pytest -q
```

以下任一情况都不能声称“完整复现”：模型下载或哈希失败、数据集校验失败、Run 非
`COMPLETED`、Evaluation 分母不足、策略快照缺失、Agent 实际未调用却写成已调用。

规则版本化细节见 [StrategyBundle 版本指南](STRATEGY_BUNDLES.md)。目录和 Artifact 解释见
[交接手册](HANDOFF.md) 与 [单图完整手册](runbooks/SINGLE_IMAGE_END_TO_END.md)。
