# Movie60 v3 评测索引

按以下顺序阅读：

1. `EVIDENCE_AUDIT.md`：标签到底是什么、能得出什么结论；
2. `proxy-split-45dev-15holdout.json`：标签无关的 45/15 切分和 5 个开发 fold；
3. `RULE_DEVELOPMENT_REPORT.md`：v3、v3.1、v3.2 的 Rule 开发集对比；
4. `winner-freeze.json`：读取保留集前冻结的胜出策略及哈希；
5. `RULE_PROXY_HOLDOUT_REPORT.md`：v3.2 Rule 的一次代理保留集结果；
6. `rule-development-results.json`、`rule-proxy-holdout-results.json`：机器可解析明细；
7. `AGENT_COMBINED_REPORT.md`：固定 4B 视觉服务实际回放后的 Rule-only、Agent-only、
   Combined 对比和最终部署结论；
8. `winner-freeze-v2.json`：打开代理留出集前冻结的实验策略；
9. `deployment-freeze.json`：代理留出结果打开后，从预声明路线中冻结的部署策略。

本目录所有“一致率”都以人工粗审认可的大模型建议为代理标签，不是独立人工准确率。
