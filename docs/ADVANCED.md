# 高级使用与版本追溯

## 不可变对象

```text
Dataset：来源、目标和 Task 分母
Run：共享分析、候选、性能与配置快照
Evaluation：某个 Strategy 对冻结候选的指标与 Rule 排名
Agent Run：某模型/Profile 对一个 Evaluation 的建议
Review sidecar：人工当前结论与追加式历史
```

新规则不覆盖旧 Evaluation；新 Agent 配置不覆盖旧 Agent Run。每次 Evaluation 都保存
Strategy 快照和 SHA，因而 v1～v3.3 可以并存并 Replay。

## Strategy 与插件

```powershell
# 查看唯一 current Strategy、文件哈希和 A/B/C/D 阈值
.\.venv\Scripts\retarget-engine.exe strategy show

# 比较两个不可变 Bundle
.\.venv\Scripts\retarget-engine.exe strategy diff `
  strategies\movie60\v3_2_2\bundle.yaml strategies\movie60\v3_3\bundle.yaml

# 查看允许执行的插件
.\.venv\Scripts\retarget-engine.exe plugins list
```

新增版本流程：复制父 Bundle 到新目录、修改通用规则、更新版本/parent、补测试、开发集校准、
冻结 SHA、验证集只跑一次，最后才在 `strategies/registry.yaml` 切换唯一 active 项。绝不原地
修改已发布目录。

## Agent Profile

`configs/agent-profile.private.example.yaml` 只描述接口；真实文件以 `.private.yaml` 结尾，已被
Git 忽略。Token 只通过 Profile 中的环境变量名称读取，不写入 YAML、Run 或日志。允许 HTTPS
或本机 loopback HTTP。普通 `run image/batch` 默认 Rule-only；传 `--agent-profile` 才运行。

当前 `movie60@3.3.0` 的中文 Skill 和案例知识被冻结在 Strategy 快照中。需要迭代时创建新
Skill/Knowledge 版本和新 Strategy，不改 3.3。Agent 输出仍是建议，不是人工标签。

## 手工 Dataset / Run

标准 Dataset 是 `dataset.yaml + sources.csv + targets.csv + tasks.csv`；Run 配置是 `run.yaml`。
日常使用不必手写，`run image/batch` 会自动生成到 `local_data/datasets/`。需要复现实验时：

```powershell
.\.venv\Scripts\retarget-engine.exe dataset validate <dataset-dir>
.\.venv\Scripts\retarget-engine.exe run generate <dataset-dir>\run.yaml
.\.venv\Scripts\retarget-engine.exe evaluate runs\<run-id> `
  --evaluation-id <new-id> --strategy strategies\movie60\v3_3\bundle.yaml
```

## 普通外部候选目录

```text
D:\review-case\
├── source.jpg
└── candidates\
    ├── crop.png
    └── generated.png
```

```powershell
.\.venv\Scripts\retarget-engine.exe review import D:\review-case
.\.venv\Scripts\retarget-engine.exe review open local_data\reviews\review-case
```

导入只冻结图片和哈希，不凭空生成 Rule/Agent 分数。

## Release 与数据边界

代码、策略、Manifest、脚本和文档进入 Git；商业素材、模型权重、密钥、Run 与 Review 像素
默认不进 Git。Movie60 像素通过私有 GitHub Release 分发，`materialize_review.ps1` 下载后
校验 SHA-256。`CURRENT_RELEASE.json` 是当前 Release 的机器可读索引。

## 常见问题

- `.venv` 来自另一台电脑：不能迁移；移走后在本机重新 Bootstrap。
- UI 打不开：先运行 `retarget-engine doctor`，再确认端口 8765 未占用。
- 新 Run 没分数：必须先有 completed Evaluation；执行 `evaluate` 或重新用 `run image/batch`。
- Company models 未就绪：重新运行 Bootstrap（不要带 `-SkipCompanyModels`）。
- Agent 未运行：这是默认；检查是否显式传了私有 Profile 及对应环境变量。
- AIGC 未运行：普通工作流故意关闭；外部生成应走单独的预算与授权流程。
