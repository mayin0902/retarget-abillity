# Movie Visual 60 v1

本数据合同引用用户提供的本地目录 `G:\Projects\movie-visual-dataset-60-20260818`，像素不进入
Git。materialize 后形成60个唯一1:1 Task：电影海报、影视剧照、视频封面、人物图各15张。

- Calibration：每类按稳定 ID 排序取前5张，共20张；
- Validation：每类剩余10张，共40张；
- 传统评测画布：1536×1536；
- 23张原图短边不足1024，必须记录 `source_low_resolution=true`；
- 原manifest的本地研究/不可公开分发声明保持不变；
- 本轮SeedDream出域依据是用户在2026-08-18对本实验的明确授权，不构成开放许可证声明；
- SeedDream每Task最多一个输出，全局最多20个唯一付费Task，硬预算12元。

物化命令：

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python scripts/materialize_movie_visual60.py
python -m retarget_agent.cli dataset validate local_data/datasets/movie_visual_60_v1
```
