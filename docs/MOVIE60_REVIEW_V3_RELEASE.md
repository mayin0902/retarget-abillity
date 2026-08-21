# Movie60 Review v3 构建、下载与 Windows 使用

## 1. 固定版本

根目录 `CURRENT_RELEASE.json` 是机器可读事实源。当前固定：

- Dataset：`movie-visual-60-v1@1.0.0`；
- Run：`movie60-square-v1-20260818`；
- Evaluation：`movie60-human-aligned-v3-3-20260821`；
- Strategy：`movie60@3.3.0`；
- 路由：Rule 主选，Agent advisory-only。

## 2. 从冻结本地证据构建工作区

本命令不覆盖旧工作区；`--output-dir` 必须不存在：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_review_v3.py `
  --repository . `
  --base-workspace "G:\Projects\retarget-release\movie60-review-v3" `
  --run "G:\Projects\retarget-engine\runs\movie60-square-v1-20260818" `
  --output-dir "G:\Projects\retarget-release\movie60-review-v3-next"
```

先校验 `movie60-review-v3-next`，再由发布负责人把旧工作区移入 `legacy/` 并将新工作区改为规范名；不要让构建器原地覆盖正在使用的人工评审目录。

构建器读取旧包中的图片、AIGC 实际回图和已有人工记录，但会重新生成 `all60` 的
v3.3.0 Rule 排名、Agent 建议、当前证据目录、Top1 和版本字段。旧机器证据不会进入新的
`all60/tasks/<task_id>/evidence/current-v3.3.0/`。已有人工等级、理由、问题码、评审者和
时间戳逐行迁移，并用 SHA-256 锁定。当前门禁值为 18 个 Task、126 个候选；少一条或改动
任一人工字段都会让构建失败。已确认记录另存为 `all60/human-review-current.csv`，其数量、
待评数量与哈希见 `all60/human-review-status.json`，便于开发交接和后续增量评审。

校验现有工作区：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_review_v3.py `
  --validate-only "G:\Projects\retarget-release\movie60-review-v3"
```

## 3. 生成 Release 压缩包

```powershell
.\.venv\Scripts\python.exe scripts\package_movie60_review_v3.py `
  --workspace "G:\Projects\retarget-release\movie60-review-v3" `
  --output-dir "G:\Projects\retarget-release\movie60-review-v3-assets"
```

输出恰好三个文件：

- `movie60-review-v3-core.zip`；
- `movie60-review-v3-evidence.zip`；
- `SHA256SUMS.txt`。

两个 ZIP 解压到同一个父目录，合并成唯一的 `movie60-review-v3/`。文件路径互不重叠，
解压前后都进行 SHA-256、ZIP CRC 和路径穿越检查。

## 4. 从 GitHub 下载并物化

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --repo mayin0902/retarget-abillity `
  --tag movie60-review-v3 `
  --release-version v3 `
  --output-dir local_data\movie60-review-v3
```

如果资产由同事手工下载：

```powershell
.\.venv\Scripts\python.exe scripts\materialize_movie60_release.py `
  --asset-dir D:\Downloads\movie60-review-v3-assets `
  --release-version v3 `
  --output-dir local_data\movie60-review-v3
```

## 5. Windows 数据包直接使用

1. 双击 `START_HERE.html`；
2. 阅读 `01_CONFIGURE_PIP_MIRROR_FIRST.md`；
3. 负责人填写 `PIP_MIRROR.ini`，否则安装器会停止且不访问公网；
4. 双击 `INSTALL_WINDOWS.bat`；
5. 双击 `START_REVIEW.bat` 并保持命令窗口打开；
6. 服务健康后自动打开 `http://127.0.0.1:8766/`；
7. 结束时关闭命令窗口，或双击 `STOP_REVIEW.bat`。

只浏览结果、不写评分时，直接双击 `OPEN_RESULTS.bat`，不需要 Python。

`.review-venv` 只属于当前电脑和解压路径，不包含在 Release，也不能复制到另一台电脑。

## 6. 旧版本

旧版本和中间目录不进入 v3 数据包。用途、问题和查找方式见
[版本与遗留资产索引](legacy/MOVIE60_RELEASE_HISTORY.md)。
