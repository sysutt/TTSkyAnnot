# 图片解析接口（POST /api/i/solve_image，异步）

给小程序用：没有 FITS 参数的网图/截图，上传一张缩略图，服务器自己提星并解算。
接口是**异步**的：先拿 job，再轮询结果（一次返回会占住连接 30~200 秒，CDN/nginx 容易 504）。

## 1. 提交

```json
POST /api/i/solve_image
{"image": "<base64 JPEG/PNG>", "w": 538, "h": 800, "pixscale": 7.66}
```

- `image`：最长边 **800 px** 左右、JPEG 质量 0.85，原始 ≤ 300 KB（base64 后 ≤ 400 KB）；
- 800 px 是实测的平衡点：更大的缩略图（1200 px）会让 astrometry.net 在 2 核服务器上超出 CPU 预算，反而更容易 `no_match`；
- `w`/`h`：缩略图的像素尺寸（建议带上，`pixscale` 的换算依赖它）；
- `pixscale`：可选，缩略图每像素的角秒数。知道 FOV 时（手机 EXIF 焦距 + 传感器尺寸）强烈建议带上：LBN777 实测从 32.6 秒降到 **21.6 秒**；
- 带 `scale` 字段时（原图/缩略图的比例），返回的 `cd`/`cd_deg` 已经换算回**原图像素**。

响应：

```json
{"ok": true, "job": "53b76fe6d8be559", "status": "running", "poll": "/api/i/solve_result?job=53b76fe6d8be559"}
```

## 2. 轮询

```
GET /api/i/solve_result?job=53b76fe6d8be559
```

运行中：`{"ok": true, "status": "running"}`
完成：同 `/api/i/solve` 的成功响应（`ra`、`dec`、`cd`（弧度，图像坐标）、`cd_deg`、`parity`、`elapsed_ms`）

失败：`no_match`、`bad_frame`、`busy`（带 `retry_after`）。

### 双通道竞速（服务端）

服务器配置了 Astrometry.net 的 key（`TTASTRO_NOVA_KEY`）时，图片解析会**同时**跑：

1. 本地 `solve-field`（4 核并行尺度搜索，1–3 分钟）；
2. astrometry.net 在线（nova API，排队约 3–7 分钟）。

**先成功者胜**，另一路自动丢弃。客户端不需要 key、也不需要改调用方式；轮询建议放宽到 **8 分钟**（本地成功通常 10–40 秒，nova 成功通常 4–7 分钟）。

实测：M33（800 px，本地 4 核解不出）由 nova 在 **347 秒**解出；本地能解的图通常在 8–35 秒返回。

轮询间隔建议 2–3 秒，最长等 3–5 分钟。

## 3. 实测（12 张 M:/H: 真实图，无文件名提示）

| 缩略图 | 成功 | 耗时（成功者） |
| --- | --- | --- |
| 800 px | **3/12** | 11.6 / 21.5 / 43.1 秒 |
| 1/3 缩放 + 中心裁切 1200 px | 0/8 | —（比 800 px 更差） |
| 800 px + pixscale 提示 | LBN777 解出 | 21.6 秒 |
| 1200 px | 0/12 | —（CPU 预算耗尽） |

瓶颈是服务器的 2 核 CPU 与「宽尺度搜索」：astrometry.net 要在多个尺度/索引里搜索，单张 1–3 分钟的 CPU 时间是常态。成功与否还与缩略图里可用的星点数有关（重度拉伸/裁切的图星点少）。

## 4. 部署记录

- `solve_api.py`：`start_image` / `_solve_image_file`（`TTASTRO_DEEP_CPULIMIT` 默认 120 秒）；
- `interactions_service.py`：`POST /api/i/solve_image` 启动任务；`GET /api/i/solve_result` 轮询（与深度解析共用任务表）；
- 重启 `ttastro-interactions.service`。
