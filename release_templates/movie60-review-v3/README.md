# Movie60 Review v3

这是当前唯一推荐的 Movie60 人工评审交付包。

第一次使用请按顺序操作：

1. 双击 `START_HERE.html` 阅读版本和入口说明；
2. 阅读 `01_CONFIGURE_PIP_MIRROR_FIRST.md`，由项目负责人填写公司镜像；
3. 双击 `INSTALL_WINDOWS.bat`，只在本数据包创建 `.review-venv`；
4. 双击 `START_REVIEW.bat` 并保持命令窗口打开；健康检查成功后浏览器才会打开；
5. 关闭该窗口或双击 `STOP_REVIEW.bat` 结束服务。

无需安装环境也可双击 `OPEN_RESULTS.bat`，只读浏览当前 60 张 Rule Top1。

## 当前版本

- Dataset：`movie-visual-60-v1@1.0.0`；
- Evaluation：`movie60-human-aligned-v3-2-2-20260821`；
- Strategy：`movie60@3.2.2`；
- 路由：Rule 主选，Agent advisory-only；
- 机器标签：人工粗审过的大模型代理标签，不是完整独立人工金标。

## 目录

- `all60/`：当前 60×7 候选、v3.2.2 评分、Agent 建议、现有人工记录；
- `focus20/`：已经实际产生的 AIGC 图和复评表，未重新调用付费 API；
- `strategy/`：当前唯一运行策略 v3.2.2；
- `documentation/`：安装、运行、算法和评审文档；
- `legacy/`：旧版本问题及查找入口，不复制旧中间资产；
- `_runtime/`：本包本地 UI 所需最小 Python 源码。

商业素材只限内部评测，不得公开再分发或未经授权上传第三方 API。
