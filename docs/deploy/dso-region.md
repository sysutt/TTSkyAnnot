# 在线星表区域查询（dso_region）

应用在解析成功后，会用解出的**中心坐标**和**视场半径**（覆盖到画面四角）请求视野内的在线天体，
把它们并入标注、叠加层、预览图和天体列表。服务端只需要增加一个动作，行为和现有的 `dso_search` 一致。

## 请求

```
POST https://app.tickwhale.com/weather
Content-Type: application/json

{"a": "dso_region",
 "d": {"ra": 60.5, "dec": 26.25, "radius": 1.18, "limit": 500, "catalogs": ["lbn", "ldn"]}}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ra` | float | 中心赤经，0–360 度 |
| `dec` | float | 中心赤纬，-90–90 度 |
| `radius` | float | 半径，度；服务端建议钳制到 0.01–30 |
| `limit` | int | 最多返回条数，建议钳制到 1–1000，默认 500 |
| `catalogs` | array | 可选，只返回这些目录的对象：`lbn` `ldn` `vdb` `ced` `rcw`；缺省只查这五个目录（不传就不返回整张表） |

## 响应

与 `dso_search` 的每一行完全相同（`dso_search` 的 `_one()` 可以直接复用）：

```json
{"errcode": 0, "result": {"total": 12, "list": [
  {"id": "LBN777", "ra": 60.5, "dec": 26.25, "type": "BN",
   "size_major": 20.0, "size_minor": 18.0, "angle": 0.0, "bmag": null,
   "name_zh": "LBN 777", "constellation_zh": "", "description_short": ""}
]}}
```

- `size_major` / `size_minor`：**角分**，应用用它画尺寸圆圈；`angle` 是位置角（度，北起向东），缺省 0；
- `bmag` 缺省给 `null` 或 ≥90 的哨兵值（应用会把 ≥90 当作"没有星等"）；
- 排序不要求，应用会按目录编号自行排序；
- 没有对象时返回 `{"errcode":0,"result":{"list":[],"total":0}}` 即可。

## 参考实现（Python）

```python
import math

def dso_region(d):
    ra = float(d["ra"]) % 360.0
    dec = max(-90.0, min(90.0, float(d["dec"])))
    radius = max(0.01, min(30.0, float(d.get("radius", 1.0))))
    limit = max(1, min(1000, int(d.get("limit", 500))))
    cats = d.get("catalogs") or ["lbn", "ldn", "vdb", "ced", "rcw"]

    # 1) 用包围盒走索引，避免全表算距离
    ddec = radius
    cosd = max(0.01, math.cos(math.radians(dec)))
    dra = radius / cosd
    rows = db.query(
        "SELECT id, ra, dec, type, size_major, size_minor, bmag, name_zh, "
        "       constellation_zh, description_short "
        "FROM dso "
        "WHERE dec BETWEEN %s AND %s "
        "  AND (ra BETWEEN %s AND %s OR ra BETWEEN %s AND %s) "
        "  AND (%s) LIMIT 5000",
        (dec - ddec, dec + ddec,
         ra - dra, ra + dra, ra + 360 - dra, ra - 360 + dra,
         " OR ".join("id LIKE %s" for _ in cats) if cats else "1=1"),
    )
    # 2) 再按真实球面距离过滤
    out = []
    for r in rows:
        dist = math.degrees(math.acos(max(-1.0, min(1.0,
            math.sin(math.radians(dec)) * math.sin(math.radians(r["dec"])) +
            math.cos(math.radians(dec)) * math.cos(math.radians(r["dec"])) *
            math.cos(math.radians(r["ra"] - ra))))))
        if dist <= radius:
            r["distance"] = round(dist, 4)
            out.append(r)
    out.sort(key=lambda r: r["distance"])
    return {"errcode": 0, "result": {"total": len(out), "list": out[:limit]}}
```

`catalogs` 到 `id` 前缀的映射：`lbn → LBN%`，`ldn → LDN%`，`vdb → vdB%` / `VDB%`，
`ced → Ced%` / `CED%`，`rcw → RCW%`（以库里实际编号写法为准）。

## 应用侧行为（已实现，0.2.21）

- 解析完成后按 `ra0/de0/rad` 调用一次区域查询，`catalogs` 只传**卡片里开启的**在线星表；
- 返回的记录按目录前缀分组、排序，并入 `dso:<目录>` 文本块和叠加层（名称 + 尺寸圆圈）；
- 只把**画面内**的对象画出来（中心点在画面内），视场外的会忽略；
- 接口不存在或网络失败时静默跳过，不影响解析本身；
- 切换星表卡片会按已返回的记录重新过滤，无需重新解析。

## 已上线

2026-10-24 已部署到 `app.tickwhale.com`（`WeatherController::dso_region`，提交 `3a58efb`），
`dso_search` 也补上了 `angle` 字段。验收：

```
curl -s https://app.tickwhale.com/weather -H 'Content-Type: application/json' \
  -d '{"a":"dso_region","d":{"ra":60.5,"dec":26.25,"radius":1.2}}'
# -> total 4: LBN777, LDN1489, LDN1491, LBN775
```

## 验收

```
curl -s https://app.tickwhale.com/weather -H 'Content-Type: application/json' \
  -d '{"a":"dso_region","d":{"ra":60.5,"dec":26.25,"radius":0.5}}' | head -c 300
```

LBN777 那张图（中心约 04h02m41s +26°40′，视场约 1.2°）应返回 LBN777 等对象。
