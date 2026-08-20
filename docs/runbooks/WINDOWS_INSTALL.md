# Windows 公司电脑从零安装

## 1. 前提

- Windows 10/11 x64；
- Python 3.12 推荐，3.11/3.13 支持；
- 不要求管理员权限；
- 模型首次物化需要访问官方模型源；GitHub 无法访问时请先由公司网络管理员处理代理或离线缓存。

## 2. 公司 pip 镜像（由负责人填写）

```text
公司 PyPI index-url：________________________________
公司 trusted-host（如需要）：________________________
公司 Paddle wheel 镜像/制品库（如有）：_______________
```

仓库不会猜测或写入公司镜像。负责人确认后，可在安装命令临时追加 `--index-url ...`。

## 3. Python 未安装

先在 PowerShell 检查：

```powershell
py -3.12 --version
```

若不存在，手工安装；脚本不会修改系统：

```powershell
winget install Python.Python.3.12
```

安装后关闭并重新打开 PowerShell。

## 4. Clone 与一键安装

```powershell
git clone <公司提供的私有仓库URL> retarget-abillity
cd retarget-abillity
PowerShell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1 -PythonVersion 3.12
```

脚本依次创建 `.venv`、安装工程与开发依赖、安装冻结的 `company_cpu_v2` 模型运行时、物化模型、验证 v1/v2/v2.1 策略并运行 Smoke。

`.venv` 只属于当前电脑和路径，不应复制给同事，也不提交 Git。新电脑需要重新安装依赖；模型缓存可以按公司制品规范离线分发，但必须保留模型名、revision 和审计文件。

## 5. 手工等价命令

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e ".[dev]"
.\.venv\Scripts\python.exe -m pip install -r requirements\company-models-windows.txt
.\.venv\Scripts\python.exe scripts\materialize_analyzer_models.py
.\.venv\Scripts\python.exe scripts\materialize_company_models.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\retarget-engine.exe plugins list
.\.venv\Scripts\python.exe -m pytest -q
```

模型已下载时可用 `scripts/materialize_company_models.py --check-only` 禁止 D-FINE 再下载并校验缓存。

## 6. 已实测环境

2026-08-20 在 Windows x64 / Python 3.13 完成安装和真实 CPU Smoke：PP-OCRv6 small 使用 ONNX Runtime，D-FINE nano 使用 CPU Torch，YuNet 使用 OpenCV。完整版本在 `requirements/company-models-windows.txt`。
