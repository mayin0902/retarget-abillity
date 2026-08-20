# 单张与批量完整运行

先完成 [WINDOWS_INSTALL.md](WINDOWS_INSTALL.md)。以下命令都在仓库根目录执行。

## 1. 一张原图生成七候选并评分

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_one_image.ps1 `
  -InputImage "D:\images\poster.jpg" `
  -CaseId "poster-001"
```

输出：

```text
runs/poster-001-square-v1/
├── analysis/                      # 原图 OCR/人脸/人物/商品/Logo 与保护图
├── candidates/.../candidate.png   # 七候选
├── candidates/.../transform.json  # 每算法风险
└── evaluations/poster-001-rule-v2/# 候选重检、分数、策略快照
```

## 2. 单独评分一张已有候选（有原图）

```powershell
.\.venv\Scripts\retarget-engine.exe score reference `
  "D:\images\source.jpg" `
  "D:\images\candidate.jpg" `
  --output-dir "local_data\scores\poster-001" `
  --strategy strategies\movie60\v2_1\bundle.yaml
```

输出 `report.json`、`report.md`、`overlay.png`、输入副本和策略快照。

## 3. 只有候选图、无原图

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "D:\images\candidate.jpg" `
  --output-dir "local_data\scores\candidate-only" `
  --strategy strategies\movie60\v2_1\bundle.yaml
```

此模式不计算内容保留和 A/B/C/D。

可选大模型视觉预审：

```powershell
.\.venv\Scripts\retarget-engine.exe score standalone `
  "D:\images\candidate.jpg" `
  --output-dir "local_data\scores\candidate-agent" `
  --agent-backend-url "http://127.0.0.1:8000/v1" `
  --agent-model "<内部模型ID>"
```

## 4. 批量数据集

准备 `dataset.yaml`、`sources.csv`、`targets.csv`、`tasks.csv` 和 `images/`，再运行：

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate local_data\datasets\my-batch
.\.venv\Scripts\retarget-engine.exe run generate local_data\datasets\my-batch\run.yaml
.\.venv\Scripts\retarget-engine.exe evaluate runs\my-batch-run `
  --evaluation-id my-batch-rule-v2-1 `
  --strategy strategies\movie60\v2_1\bundle.yaml
```

每个 Task、每个候选都有独立 JSON；部分失败不会缩小分母或覆盖成功候选。

## 5. Agent 与人工 UI

Agent 需要内部 OpenAI-compatible 视觉端点：

```powershell
.\.venv\Scripts\retarget-engine.exe agent replay runs\my-batch-run `
  --evaluation-id my-batch-rule-v2-1 `
  --agent-run-id my-batch-agent-v1 `
  --mode always_on_agent `
  --backend-url "http://127.0.0.1:8000/v1" `
  --model "<内部模型ID>" `
  --strategy strategies\movie60\v2_1\bundle.yaml
```

```powershell
.\.venv\Scripts\retarget-engine.exe review web runs\my-batch-run --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。人工结果是追加记录；改策略后使用新的 Evaluation/Agent Run ID。
