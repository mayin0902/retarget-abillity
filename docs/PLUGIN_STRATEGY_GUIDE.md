# 插件与不可变策略版本指南

## 1. 为什么不是任意 Python 路径

配置只允许引用 `plugin_catalog.py` 注册的 ID。这样可以替换完整算法实现，同时阻止 YAML 加载任意代码。运行前未知 ID 会立即失败。

## 2. StrategyBundle 包含什么

当前活动的 `strategies/movie60/v3_2_2/bundle.yaml` 同时固定：

- Rule scoring、selection、override policy；
- Detector、参考/无参考 Scorer、两个 Selector；
- 总览、高清候选、Rule-vs-Agent、单图预审和 AIGC 生成 Prompt；
- Agent Skill；
- 各 Agent backend Adapter。

Evaluation 会复制全部文件和 SHA-256 到 `evaluations/<id>/strategy/`，包括嵌套 Prompt 目录。

## 3. 只改参数

新建版本目录，修改 `scoring.yaml` 的权重或阈值。例如把 A 改为 80 分以上：

```yaml
proxy_a_threshold: 80.0
```

不要原地编辑已发布目录。

## 4. 替换完整实现

1. 在独立模块实现与现有调用合同一致的 Adapter；
2. 在 `built_in_plugin_catalog()` 注册唯一 ID；
3. 添加隔离测试和端到端 Smoke；
4. 新 StrategyBundle 引用新 ID；
5. 运行 `plugins list`、`strategy show`、`strategy diff`；
6. 用新 Evaluation ID 重跑，不覆盖历史。

## 5. 私有 Example KB

Git 只提交 Example schema、公开/程序化例子与 manifest。真实商业图、用户素材和内部纠错例放 `local_data/` 或受控私有 Release；策略只记录 KB 版本和检索策略，不把私有像素提交 Git。
