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

## 部署记录

- `solve_api.py`：响应字段与错误码（备份 `solve_api.py.bak-nebula3d-*`）；
- `solver.py`：`SOLVE_BUDGET` 45→14 秒（备份 `solver.py.bak-budget-*`）；
- `interactions_service.py`：429 带 `retry_after`（备份 `interactions_service.py.bak-starcone-*` / `-3`）。
