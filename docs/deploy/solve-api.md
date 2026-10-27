# 在线盲解接口（POST /api/i/solve）

桌面端（TTSkyAnnot、Nebula3D）共用的盲解接口：客户端本地提星，只上传星点坐标，服务端返回完整 WCS。

## 请求

```json
{"w": 3672, "h": 5648,
 "stars": [[1234.56, 789.01, 54321.0], "..."],
 "hint": {"ra": 274.7, "dec": -13.8, "pixscale": 1.23}}
```

- `stars`：按亮度降序，最多 200 个（服务端会截到 400），`[x, y, flux]`，原点左上、y 向下；
- `hint`：软先验，可缺省或部分缺省；错误提示仍能解出（解算器会走后备）。

## 成功响应

```json
{"ok": true, "ra": 274.7123, "dec": -13.8234,
 "cd": [[-5.95e-8, 2.1e-9], [1.9e-9, -5.94e-8]],
 "cd_deg": [[-3.41e-6, 1.2e-7], [1.1e-7, -3.40e-6]],
 "matched": 87, "nstars": 200, "rms_arcsec": 0.42,
 "parity": 1, "name": "HINT", "pixscale": 1.23, "rotation": 355.9,
 "elapsed_ms": 1830, "objects": ["..."],
 "north_angle": 1.2, "east_angle": 91.0, "mirrored": false}
```

| 字段 | 约定 |
| --- | --- |
| `ra` / `dec` | 度，ICRS，参考点为图像几何中心 |
| `cd` | 2×2，**像素→弧度**，`[Δra·cosδ, Δdec]ᵀ = cd × [Δx, Δy]ᵀ`，以中心为原点、x 向右、y 向下（Nebula3D 规格） |
| `cd_deg` | 同一矩阵的**度制**版本，FITS 客户端直接使用（向后兼容） |
| `parity` | 1 = 不镜像，0 = 镜像（图像坐标系下 det>0 即不镜像） |
| `matched` | 参与拟合的星数；`nstars` 为提交的星数 |
| `name` | 命中的候选名（HINT/天体名） |
| `pixscale` | 角秒/像素，`hypot(cd[0][0], cd[1][0]) × 206264.806` |
| `rms_arcsec` | 匹配残差中位数 |
| `elapsed_ms` | 服务端耗时 |

## 失败响应

```json
{"ok": false, "code": "no_match", "reason": "14 秒内没有找到匹配的星场…", "retry_after": 3}
```

| code | 含义 |
| --- | --- |
| `busy` | 解析槽被占；带 `retry_after`，指数退避重试 |
| `no_match` | 无匹配（含预算内未解出） |
| `too_few_stars` | 星点不足 8 个 |
| `bad_frame` | 尺寸/格式非法 |
| `bad_key` | 密钥无效（422/401 由服务层处理） |

## 时限与实测（2026-10-24）

- 盲解预算 14 秒：无匹配约 **14.6 秒**返回 `no_match`；
- 带中心+尺度提示：**1.6–1.8 秒**（LBN777 宽场实测）；
- 精度验收（对 Gaia 精修参考）：中心误差 **1.17″**、尺度误差 **0.275%**、旋转误差 **0.000°**、±0.3 px 抖动下稳定；镜像画幅（左右/上下翻转）均可解出，`parity=0`。

## 广域/星野接口（POST /api/i/solve_wide）

几十度的星野图（银河、全天、手机/相机广角）用整幅 gnomonic 匹配必然失败，这个接口改成分块盲解 +
亮星复核。客户端只上传星点，不上传图片。

```json
{"w": 5472, "h": 3648, "stars": [[1234.5, 789.0, 54321.0], "..."],
 "pixscale": 57.287, "total": 80, "per_tile": 20}
```

- `stars`：最多 3000 颗（要给足；一块 34° 的星野只有几十颗星时盲解成功率接近零）；
- `pixscale`：角秒/像素的先验，可缺省。客户端从照片 EXIF（焦距 + 焦平面分辨率）算出来，
  有它块解不用盲扫，二三十秒降到几秒，成功率也明显提高；
- `total` / `per_tile`：总预算与单块预算（秒），默认 80 / 20。

成功响应（`ra`/`dec` 是**画面中心**，不是某一块的中心）：

```json
{"ok": true, "mode": "wide", "solver": "wide-tiles",
 "ra": 271.773, "dec": -24.648, "cd": [[-0.00931, 0.01183], [-0.01186, -0.00932]],
 "pixscale": 56.558, "rotation": 232.2, "mirrored": true,
 "rms_px": 2.14, "rms_deg": 0.034, "accuracy_deg": 0.3, "center_sigma_deg": 0.005,
 "sky_coverage": 0.54,
 "bright_stars": 398, "control": 90.2, "pairs": 418, "verified": true,
 "blocks": [{"rect": [0, 0, 0, 0, 2280, 1520], "pairs": 43, "control": 11.6, "pixscale": 53.0}],
 "elapsed_s": 28.5}
```

判定与精度：

- 34° 的块里 G<8.5 的星表星有上千颗，**一个错误 WCS 也能在 3 像素内撞上几十颗**。所以块内命中数
  必须高出「星表整体平移」的对照 5σ，才算解出；
- 块解只当种子：把切点从块中心迭代到画面中心，每轮按残差剔误配（不剔会把切点拉到 25° 外、尺度差 16%）；
- 最后按整幅拟合投一遍亮星，命中数仍要高出对照 5σ 才返回 `verified`；
- `center_sigma_deg` 是最小二乘在画面中心处的 1σ 外推误差（配对星只覆盖半幅时它会自动变大）；
  `accuracy_deg` 取 `2×center_sigma_deg`、拟合残差、0.15°，以及「配对星覆盖不足 60% 时 0.3°」的最大值；
- 实测 IMG_7260（6D + 24mm，73°×51°）：`rms_px` 2.14（0.034°），亮星复核 398 颗、对照 90 颗，30 秒；
- 地景占掉大半幅、只剩一条窄天区的画面（如 IMG_7262）块解很勉强，同一张图重试才能成功——
  复核不过时宁可返回 `no_match`，也不返回一个错的中心（这正是旧版 89 颗「亮星吻合」却整体偏 18° 的原因）。

失败响应 `{"ok": false, "code": "no_match", "reason": "...", "blocks": [...]}`：分块解不出，
或者解出了但全画面复核没通过（两者用 `blocks` 区分）。

## 部署记录

- `wide_solve.py`：新增（广域分块盲解 + 亮星显著性复核 + 全画幅拟合），`solve_api.py` 的
  `handle_solve_wide` 改成薄委托（备份 `solve_api.py.bak-wide2`）；依赖 `/home/ubuntu/astro` 下的
  `solver.py` / `solve_api.py` 的星表索引；
- `solve_api.py`：响应字段与错误码（备份 `solve_api.py.bak-nebula3d-*`）；
- `solver.py`：`SOLVE_BUDGET` 45→14 秒（备份 `solver.py.bak-budget-*`）；
- `interactions_service.py`：429 带 `retry_after`（备份 `interactions_service.py.bak-starcone-*` / `-3`）。
