# 仅安装 Python 依赖

适用场景：仓库已经 Clone 到本机，只需要建立或更新项目的 Python 环境，不执行数据集、
Generation、Evaluation、Agent 或人工 UI。

> 默认仓库路径为 `G:\Projects\retarget-abillity`。项目要求 Python `>=3.11,<3.14`，推荐
> Python 3.12。`.venv` 与机器和仓库绝对路径绑定，不要从旧机器复制。

## 1. 最短安装流程

```powershell
Set-Location G:\Projects\retarget-abillity

py -3.12 --version
py -3.12 -m venv .venv

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e ".[dev]"
```

这四步分别表示：

1. 确认存在受支持的 Python；
2. 在当前项目创建独立 `.venv`；
3. 更新该虚拟环境自己的 pip；
4. 从当前仓库 `pyproject.toml` 安装核心依赖和开发依赖。

`-e` 是 editable/development install：源码仍使用仓库中的 `src/`，修改 Python 代码后通常
不需要重新安装。`[dev]` 会额外安装 pytest、Ruff、httpx 和覆盖率工具。

如果只运行程序、不开发和测试，可以使用：

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e "."
```

项目交接和 Code Agent 开发环境推荐始终使用 `.[dev]`。

## 2. `.venv` 已存在时

不要再次执行 `python -m venv .venv` 覆盖它。先确认解释器属于当前仓库且版本正确：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable); print(sys.version)"
```

确认无误后，更新依赖即可：

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements\constraints-py311-313.txt -e ".[dev]"
```

pip 会复用已满足的包，而不是无条件重新下载所有内容。

## 3. 安装后验证

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -c "import cv2, numpy, PIL, pydantic; print('imports OK')"
.\.venv\Scripts\retarget-engine.exe --help
.\.venv\Scripts\ruff.exe --version
.\.venv\Scripts\python.exe -m pytest -q tests\test_single_image_workflow_tools.py
```

成功标准：

- `pip check` 输出 `No broken requirements found`；
- imports 输出 `imports OK`；
- `retarget-engine --help` 能展示 CLI；
- 定向测试通过。

## 4. Python 包装好了，但还不能完整运行？

OCR、YOLOX、YuNet 的 ONNX 文件不是 pip 包，也不在 Git 中。若目标是“可以真正跑一张图”，
还必须执行：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_analyzer_models.py
```

该脚本根据 `datasets/analyzer_models_v1/model_manifest.csv` 下载并校验模型。完成后：

```powershell
Get-ChildItem models\analyzers -File | Select-Object Name, Length
```

因此两个完成口径是：

| 目标 | 必须完成 |
|---|---|
| 仅安装 Python 包 | 第 1～3 节 |
| 达到 CPU 引擎可运行状态 | 第 1～4 节 |

视觉 Agent 的 vLLM、CUDA、模型权重属于另一套 Linux GPU 环境，不包含在本手册中。

## 5. 无网络机器的依赖安装

不要迁移 `.venv`。在一台相同操作系统、CPU 架构和 Python 版本的联网机器上，从仓库根目录
准备 wheelhouse：

```powershell
py -3.12 -m venv .wheel-builder
.\.wheel-builder\Scripts\python.exe -m pip install --upgrade pip
.\.wheel-builder\Scripts\python.exe -m pip wheel --wheel-dir offline_packages ".[dev]"
```

将仓库和 `offline_packages\` 通过安全渠道复制到新机器，然后：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install `
  --no-index `
  --find-links offline_packages `
  -e ".[dev]"
```

分析模型也应复制 `models/analyzers/`，并按 model manifest 重新核验 SHA-256。wheelhouse 只能
减少联网下载，不能免去在新机器执行 pip install。

## 6. 常见错误

| 错误 | 原因 | 处理 |
|---|---|---|
| `Requested Python version not installed` | 没有 Python 3.12 | 安装 3.11/3.12/3.13 后重开终端 |
| `requires-python` 不满足 | 使用了 3.10 或 3.14 | 创建受支持版本的新 `.venv` |
| `retarget-engine.exe` 不存在 | 尚未安装当前项目 | 执行 `pip install -e ".[dev]"` |
| `missing analyzer model assets` | pip 已完成但 ONNX 未物化 | 执行 model materializer |
| OpenCV/NumPy import 失败 | wheel 与 Python/架构不匹配 | 用当前机器重新创建 `.venv` 和安装 |
| PowerShell 禁止 Activate | 执行策略限制 | 不必 Activate，显式调用 `.venv\Scripts\python.exe` |

## 7. 给本地 Code Agent 的最短指令

```text
请完整读取并执行 docs/runbooks/PYTHON_DEPENDENCIES.md。

目标是安装 retarget-engine 的 Windows Python 开发依赖，并达到 CPU 引擎可运行状态，
所以执行第1～4节。

不得删除或覆盖已有 .venv；存在时先检查解释器路径和版本。
不得关闭 detector 绕过模型缺失。
不得 Commit、Push、上传图片或调用付费 API。

最终汇报 Python 版本、sys.executable、pip check、核心 import、模型物化与 hash 校验、
retarget-engine --help、Ruff 和定向 pytest 的结果。
```
