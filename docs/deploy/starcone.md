# 在线星锥（starcone）— 精修用的参考星

解析得到粗 WCS 后，客户端用这个接口取视场内的 Gaia 星，自建 SIP 精修，不再依赖 VizieR。
服务端读的是 ARM 上现成的全天 Gaia DR3 G<16 内存映射星表（`gaia16/gaia_g160.bin`）。

## 请求

```
POST https://ttastro.com/api/i/starcone
Content-Type: application/json

{"ra": 61.07, "dec": 26.60, "radius": 1.0, "mag": 16.0, "limit": 60000}
```

| 字段 | 说明 |
| --- | --- |
| `ra` / `dec` | 视场中心（度） |
| `radius` | 锥半径，0.02–3.0 度 |
| `mag` | 星等上限，8–16（默认 16） |
| `limit` | 最多返回条数，200–120000 |

## 响应

```json
{"ok": true, "n": 3420, "ms": 334, "deep": true,
 "stars": [[61.193787, 26.200541, 6.612], "..."]}
```

`stars` 每行 `[ra_deg, dec_deg, Gmag]`，按星等升序；`deep=false` 表示深星表不在、用浅层 Tycho 代替。

## 实测

- LBN777 视场（半径 1.0°）：**3420 颗，0.3 秒（服务端）/ 1.3 秒（公网往返）**；
- 精修结果与 VizieR 路径逐角一致（角点差 < 0.1″），matched 1763 颗、SIP 3 阶、留出误差 0.13 px 中位；
- VizieR 仍是兜底：接口不可用时客户端自动回退。

## 部署记录

- `solve_api.py`：新增 `handle_starcone()`（备份 `solve_api.py.bak-starcone-*`）；
- `interactions_service.py`：`POST /api/i/starcone`，按 IP 240 次/小时限流（备份 `interactions_service.py.bak-starcone-*`）；
- 重启 `ttastro-interactions.service`。
