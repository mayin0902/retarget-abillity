# 扩展开发指南

这份文档面向要替换公司能力、增加实现或迭代策略的开发者。原则是：调用入口保持稳定，变化
放进 Adapter 或新的不可变 Strategy；禁止按图片文件名、Task ID 或单张答案硬编码。

## 1. 先判断扩展属于哪一类

| 需求 | 扩展位置 | 是否新建 Strategy |
|---|---|---:|
| 换 AIGC HTTP API | `providers/` Adapter + `generation_providers` | 否 |
| 换 OCR/目标/人脸检测组合 | Detector Suite Adapter | 是 |
| 新增自动指标实现 | Reference/Standalone Scorer Adapter | 是 |
| 改权重、阈值或门禁 | 新 Strategy 版本目录 | 是 |
| 改 Rule 排名实现 | Selector Adapter | 是 |
| 改 Agent 的视觉判断规则 | 新 Skill/Knowledge/Prompt + Strategy | 是 |
| 新增重定向算法 | `methods/` Adapter +方法 Profile | 视默认启用情况 |
| 只改 UI 文案 | `web/` | 否 |

实现代码通过 `plugin_catalog.py` 的固定 ID 注册。Strategy 只能引用白名单 ID，不能从 YAML
执行任意模块路径。

## 2. 替换或新增 AIGC API

### 2.1 稳定接口

新 Adapter 实现：

```python
class AIGCProvider(Protocol):
    provider_id: str
    provider_version: str

    def capabilities(self) -> ProviderCapability: ...
    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult: ...
```

通用请求只包含：

- `task_id/run_id/request_id`：审计身份；
- `source_path` 或 `source_url`：二选一；
- `source_sha256`：调用方预期的源像素；
- `target_width/target_height/target_format`；
- `prompt/prompt_version`。

`target_width/target_height` 表达调用方希望的构图尺寸。若厂商只支持 `1K/2K` 档位，Adapter
必须如实返回厂商图片的实际 `width/height`；需要精确像素时可以在 Adapter 内保留原件后再
生成归一化副本，或交给本地重定向流程处理，不能伪报尺寸。

不要把 Token、Cookie、私有 Endpoint 或公司素材审批结果放入 Request。密钥和 Endpoint 只从
环境变量或公司运行时配置读取。

### 2.2 Adapter 最小模板

新建 `src/retarget_agent/providers/company_api.py`：

```python
from pathlib import Path

from retarget_agent.models import ProviderCapability
from retarget_agent.providers.base import (
    AIGCGenerationRequest,
    AIGCProviderError,
    AIGCProviderResult,
    AIGCProviderRuntime,
)


class CompanyImageAdapter:
    provider_id = "company_image_api"
    provider_version = "1.0.0"

    def __init__(self, runtime: AIGCProviderRuntime) -> None:
        self._runtime = runtime
        self._endpoint = runtime.environ.get("COMPANY_AIGC_ENDPOINT", "")
        self._token = runtime.environ.get("COMPANY_AIGC_TOKEN", "")
        if not self._endpoint or not self._token:
            raise ValueError("company AIGC endpoint and token are required")

    def capabilities(self) -> ProviderCapability:
        return ProviderCapability(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            supports_async=False,
            supports_cancel=False,
            supports_seed=False,
            supports_mask=False,
            max_outputs=1,
        )

    def generate(self, request: AIGCGenerationRequest) -> AIGCProviderResult:
        # 1. 读取/编码 source；验证公司接口自己的尺寸和格式限制
        # 2. 使用 self._runtime.timeout_seconds 调用接口
        # 3. 厂商异步任务、轮询、状态码与 JSON 都封装在这里
        # 4. 把一张最终图片写到 self._runtime.output_root 内
        # 5. 返回实际路径、SHA-256、尺寸和可选成本
        # 失败时抛 AIGCProviderError(code, sanitized_message)
        raise NotImplementedError


def create_company_image_adapter(runtime: AIGCProviderRuntime) -> CompanyImageAdapter:
    return CompanyImageAdapter(runtime)
```

不要直接复制 SeedDream 的请求 JSON。不同厂商的图片字段、鉴权、异步轮询、费用和尺寸规则
应全部留在各自 Adapter。

### 2.3 注册

在 `plugin_catalog.py::built_in_plugin_catalog()` 增加：

```python
from .providers.company_api import create_company_image_adapter

catalog.generation_providers.register(
    "company_image_api",
    create_company_image_adapter,
)
```

注册 ID、Adapter 的 `provider_id` 和运行命令的 `--provider` 必须完全一致。重复 ID 会被
`Registry` 拒绝。

### 2.4 通用执行层会做什么

`generation_execution.execute_generation()` 负责：

1. 根据 Provider ID 查注册表；
2. Dry-run 时只返回 `planned`，不读取密钥、不写文件；
3. Execute 时校验本地源图 SHA-256；
4. 构造 `AIGCProviderRuntime`；
5. 调用 Adapter；
6. 验证输出位于受控目录、哈希一致、图片可解码、尺寸元数据一致；
7. 保存无密钥的 `executions/<request-id>.json`。

通用层不判断素材是否允许发送给外部接口。`--execute` 表示调用方已经按公司流程完成授权。
如果公司需要审批，应在调用此模块之前完成，或由上层业务系统实现自己的 Policy。

预算、调用者幂等标签和成本字段都是可选的：

- 不传 `--budget-cny`：通用层不按金额阻塞；Provider 仍可执行自身限制；
- 不传 `--idempotency-key`：通用层不要求厂商支持幂等；
- API 不返回实际费用：`actual_cost_cny=null`；
- Provider 自带内容哈希缓存时，可以返回自己的 `idempotency_key/cache_hit`。

### 2.5 测试顺序

第一层必须完全离线：

```powershell
python -m pytest -q tests\test_generation_providers.py
python -m pytest -q tests\test_seedream_provider.py
```

Fake Adapter 至少覆盖：成功、厂商错误、超时、无效图片、Hash 不符、未配置密钥、Dry-run
零调用。断言只跨 `AIGCProvider.generate()` 和 `execute_generation()` 接口，不读取 Adapter
内部状态。

第二层才是一次受控真实 Smoke：

```powershell
# 先执行不付费的 preflight
retarget-engine generation run source.jpg `
  --provider company_image_api `
  --output-root local_data\company-api-smoke `
  --request-id company-smoke-v1 --task-id poster-001 `
  --prompt-file prompt.txt

# 经负责人确认后才增加 --execute
retarget-engine generation run source.jpg `
  --provider company_image_api `
  --output-root local_data\company-api-smoke `
  --request-id company-smoke-v1-paid --task-id poster-001 `
  --prompt-file prompt.txt --execute --timeout-seconds 300
```

真实 Smoke 只证明接口、下载和审计链路可用；一张图不能证明质量提升。

## 3. 新增 Detector Suite

Detector Suite 输入 RGB，输出 `tuple[RegionRecord, ...]`。步骤：

1. 固定模型名称、revision、权重 Hash、运行后端和阈值；
2. 为 OCR、人脸、人物、商品、Logo 分配稳定 `semantic_type`；
3. 缺模型时按 Profile 约定明确失败或降级，不返回伪检测；
4. 在 `plugin_catalog.detector_suites` 注册新 ID；
5. 新建 Strategy 引用新 ID；
6. 对原图和候选分别测试，不把原图检测结果复制给候选；
7. 用真实图片核对召回、误检与耗时。

检测器变化会改变内容保留证据，通常需要新 Evaluation；如果生成算法也使用新保护区域，还需
重跑 Generation。

## 4. 新增 Scorer 或指标

Reference Scorer 同时读取原图和候选证据；Standalone Scorer 只能看单张候选。新增指标时：

1. 先定义字段含义、范围、缺测值和方向；
2. 缺测使用 `None`，不能用 0 冒充失败；
3. 指标计算与等级门禁分开；
4. 在 Scorer Adapter 输出结构化值；
5. 新 Strategy 再决定权重、阈值、惩罚和门禁；
6. 用同一冻结 Run Replay，避免候选像素变化干扰比较。

## 5. 迭代 Rule

只改权重、A/B/C/D 阈值或门禁时，不改 Python：

1. 复制当前不可变 Strategy 到新版本目录；
2. 设置 `parent_strategy`；
3. 修改 `scoring.yaml/selection.yaml/override.yaml`；
4. 在 Registry 登记新版本 Hash，但先保持非 active；
5. Calibration 多轮迭代；
6. 冻结后在 Validation 只运行一次；
7. 确认人工指标、严重 C/D 召回和回归案例；
8. 最后才切唯一 active。

旧 Strategy、旧 Evaluation、旧人工事件和 Run 内快照都不能覆盖。

## 6. 迭代 Agent Skill、Knowledge 与 Prompt

- Skill：行为原则、视觉优先级、允许覆盖 Rule 的条件、理由代码；
- Knowledge：可泛化正反例，不包含 Task ID 和逐图答案；
- Prompt：严格声明输入字段与 JSON 输出 Schema；
- Backend Adapter：模型服务协议、图片编码、结构化输出和有限重试。

修改任一项都创建新版本和新 Agent Run ID。Agent 必须接收完整 Rule 排名和 Rule Top1；模型
输出 Schema 失败、视觉证据矛盾或覆盖条件不成立时回退 Rule。机器建议不是人工金标。

## 7. 新增重定向算法

算法实现 `CandidateMethod.generate(...) -> MethodOutput`：

1. 只使用共享 Analysis/importance/tolerance；
2. 成功输出必须是准确目标尺寸 RGB；
3. 失败返回结构化状态，不从分母删除；
4. 保存 `TransformRecord`，包含裁剪、缩放或形变风险；
5. 在 `built_in_methods()` 注册稳定 ID；
6. 为多个目标比例、确定性和失败隔离写测试；
7. 默认启用前使用真实图片评测。

## 8. 提交前检查

```powershell
ruff check src tests scripts
python -m pytest -q
retarget-engine plugins list
retarget-engine strategy show
```

还应完成：干净 Clone、公司镜像下 Bootstrap、`doctor`、一张真实图片 Generation/Rule、UI
载入，以及本次新增 Adapter 的 Fake E2E。真实外部 API Smoke 必须由负责人明确授权，次数、
预算、结果和失败都要记录。
