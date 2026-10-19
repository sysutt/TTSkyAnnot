# 解析接口的滥用防护：现状、已做的改动与 API Key 注册方式分析

> 面向 ttastro.com `/api/i/solve`（桌面端 TTSkyAnnot 与「晴天小程序」共用）。
> 写于 2026-10-20。第一部分是已经上线的改动，第二部分是 B 方案（API Key）的选型报告。

## 一、当前的调用方

| 调用方 | 路径 | 服务端看到的来源 | 特征 |
|---|---|---|---|
| 桌面端 TTSkyAnnot | 直连 `https://ttastro.com/api/i/solve` | 每个用户自己的公网 IP | `python-requests`（可以改成 `TTSkyAnnot/<ver>`） |
| 晴天小程序 | 小程序 → jiaowa 后端 `https://app.tickwhale.com/astro`（`a: astrometry_solve`）→ ttastro | 固定为 jiaowa 的出口 IP **120.79.222.226** | `GuzzleHttp`，带 `tkid` / `token` |

关键点：**小程序不直连 ttastro**（域名未备案，微信不允许），所有小程序请求都由 jiaowa 一台机器转发，所以在 ttastro 这一侧看起来都是同一个 IP。任何按单 IP 的限流如果不把这一台机器单独处理，就会把所有小程序用户一起限死。

## 二、已经上线的 A 层防护

### 1. nginx（`/etc/nginx/conf.d/ttskyannot-limits.conf` + 站点里的 `location = /api/i/solve`）

- 桌面端按来源 IP：`4 r/s`、burst 6、每 IP 同时 2 个连接，超限直接 **429**；
- **jiaowa 的 IP 在 map 里把 key 置空**（nginx 对空 key 不计数），所以小程序不受 nginx 这层影响；
- 只对 `= /api/i/solve` 生效，其它 `/api/i/*`（留言、观星点等）不受影响。

### 2. `interactions_service.py`（127.0.0.1:5002）

| 维度 | 桌面端（按 IP） | 小程序池（jiaowa 整体） | 全局 |
|---|---|---|---|
| 带提示解析 | 120 次 / 10 分钟 | 300 次 / 分钟 | 90 次 / 分钟 |
| 盲解（完全没有提示） | 6 次 / 小时 | 20 次 / 小时 | 12 次 / 小时 |
| 每日 | 600 次 | — | — |
| 独占解析槽 | ≤ 150 秒 / 10 分钟 | ≤ 450 秒 / 10 分钟 | 单槽串行 |

补充：

- **“盲解”指没有任何提示**：有 `target`、有 `ra/dec`、有 `pixscale` 都算有提示。之前只看 `target`，桌面端用“中心坐标”时会被误判成盲解，正常用户几下就被限死，已修正；
- 请求先做结构校验（尺寸 100–20000、星点 8–500 个），畸形请求不占额度、不拖住解析槽；
- 429 响应带 `Retry-After`；每次解析写一行日志（IP、是否小程序池、是否盲解、星点数、返回码、耗时、UA）；
- 过期的来源键会定期清理，服务长时间运行不会越占越多。

### 3. 验证结果

- 正常带提示解析：公网 **3.7 秒**解出（不受影响）；
- 分类正确：`target` → 有提示；`ra/dec` → 有提示；无任何提示 → 盲解；`X-Forwarded-For: 120.79.222.226` → 走小程序池；
- 限流生效：同一 IP 连发 16 个并发请求，6 个 429、10 个通过（nginx burst 6）；
- 小程序侧：来源 IP 在 nginx 不计数、在服务端走单独池子，**调用方式、请求格式、返回格式都没有变化**。

### 4. 仍然挡不住的情况

- 有人用很多 IP 一起刷带提示解析：全局 90 次/分钟是唯一的上限，解析槽会一直忙，真实用户会收到 `busy`；
- 无法定位到具体的人/机器，只能封 IP；
- 重启服务后计数清零。

这就是为什么还需要 B 层：给请求一个**可归属、可吊销**的身份。

---

## 三、B 方案：API Key 的注册方式分析

### 3.1 候选方式

| 方式 | 用户摩擦 | 开发量 | 批量滥用抵抗 | 可归属 / 吊销 | 覆盖人群 | 隐私 | 维护成本 |
|---|---|---|---|---|---|---|---|
| **A. 匿名 + PoW**（不注册） | 无 | 小（0.5 天） | 中（成本随难度上升） | 弱（只有 IP） | 所有人 | 最好 | 低 |
| **B. 邮箱验证码** | 小（收一封信） | 中（1 天，含发信） | 较好（Turnstile 挡批量） | 强（邮箱） | 有邮箱的所有人 | 中 | 中（发信/退信/垃圾注册） |
| **C. 微信登录**（网站扫码 / 小程序一键） | 最低（中国用户几乎必备） | 中大（2–3 天，需开放平台/unionid） | 最强（实名生态） | 强（openid/unionid） | 中国用户几乎全覆盖，海外弱 | 中（收集微信身份） | 中（token 维护） |
| **D. GitHub OAuth** | 小（对开发者） | 最小（0.5 天） | 较强（需 GitHub 账号） | 强（GitHub 账号） | 天文圈里偏少 | 好 | 低 |
| **E. 邀请码 / 人工审核** | 大 | 最小（0.5 天） | 最强 | 中（凭邀请码追溯） | 受限 | 好 | 人工 |

### 3.2 每种方式的要点

**A. 匿名 + PoW**
- 服务端下发 `{challenge, bits}`，客户端找出 nonce 使 `sha256(challenge+nonce)` 前 N 位为 0（N=18~20，约 0.1–0.3 秒），随请求提交，服务端微秒级验证；
- 优点：不注册、不收集身份、对正常用户几乎无感；
- 缺点：没有“人”的概念，封只能封 IP；脚本仍然可以预计算/并行；
- 适合：作为“匿名小额度的门槛”，而不是唯一手段。

**B. 邮箱验证码**
- 流程：邮箱 → Turnstile 人机验证 → 6 位验证码 → 自动发 key；
- 优点：覆盖面广（不依赖微信）、可归属、可吊销、可发通知；
- 缺点：需要发信服务（阿里云邮件推送/Resend），要处理临时邮箱、退信、垃圾注册；
- 适合：作为“没有微信/不想用微信”的兜底入口。

**C. 微信登录**
- 两种形态：
  1. **小程序内一键领取**：用户在小程序里点“获取桌面端密钥”，服务端拿 openid 生成 key 并显示，用户复制到桌面端设置里——对现有小程序用户几乎零摩擦；
  2. **网站扫码登录**：网站接入微信开放平台「网站应用」扫码（需要 300 元认证 + 域名备案），生成 key 后展示；
- 优点：中国天文爱好者基本都有微信，防批量最好，身份天然唯一（openid/unionid）；
- 缺点：开发量最大；网站扫码依赖开放平台认证；海外用户和不用微信的人被排除；
- 注意：小程序调用 ttastro 的链路**不需要改**——key 由 jiaowa 服务端持有（见 3.4），用户不感知。

**D. GitHub OAuth**
- 优点：OAuth 现成、开发最省、对开源项目受众自然、身份可归属；
- 缺点：中国普通星友大多没有 GitHub，只能作为补充入口；
- 适合：和 B/C 并列的“另一种登录方式”，不单独作为主入口。

**E. 邀请码 / 人工审核**
- 优点：最省开发、控制最强；
- 缺点：增长受限、依赖人工；
- 适合：内测期或额度非常紧张时。

### 3.3 推荐组合（分阶段）

**阶段 0（已完成）**：A 层 IP + 池 + 槽预算，先止血。

**阶段 1（建议先做）**：**微信登录自助发 key** + **GitHub OAuth** 作为第二入口。
- 主入口覆盖绝大多数用户（微信），第二入口覆盖开发者/海外（GitHub），不额外做邮箱系统；
- 匿名用户保留很小的额度（例如每天 3 次/IP，或要求 PoW），保证“下载来试一下”的体验；
- 后台只需要一个 key 列表页（生成、吊销、用量、最后使用时间），不需要完整用户系统。

**阶段 2（按需）**：邮箱验证码作为兜底；邀请码用于额度紧张时的白名单。

**如果不想做任何登录**：只做 A + PoW + 每天匿名 3 次，也能挡住绝大多数滥用，但要接受“没有归属、只能封 IP”。

### 3.4 接口设计（无论选哪种注册方式都适用）

- **key 格式**：`ttsky_` + 32 位随机十六进制；服务端只存 `sha256(key)`，明文只在生成时显示一次；
- **请求头**：`X-API-Key: ttsky_...`；无效 → 401，超额 → 429 + `Retry-After`；
- **表结构**：`api_keys(key_hash, user_ref, provider, created, last_used, quota_day, quota_hour, revoked, note)`；
- **配额分层**：free（如 300 次/天、20 次/小时）、beta（1000/天）、service（jiaowa 专用，按 IP 白名单）；
- **jiaowa 服务 key**：小程序链路完全不变——jiaowa 在转发时加上自己的 `X-API-Key`，ttastro 按“服务 key + IP 白名单”识别，额度按整池计；
- **桌面端**：设置里加“在线解析密钥”，留空时走匿名额度（或直接提示改用本地 ASTAP）；
- **统计**：按 key 统计成功/失败/耗时，异常 key 自动告警；
- **吊销**：一个开关，立即生效（服务端每次请求查表或缓存 60 秒）。

### 3.5 工作量估算

| 项 | 工作量 |
|---|---|
| 微信登录（小程序一键发 key） | 1–1.5 天 |
| 网站扫码登录（需开放平台认证） | +1 天 |
| GitHub OAuth | 0.5 天 |
| 邮箱验证码（含发信） | 1 天 |
| key 表 + 校验 + 配额 + 后台列表 | 1 天 |
| 桌面端设置项 + 请求头 | 0.5 天 |
| 小程序侧 | 0（jiaowa 加一个请求头） |

### 3.6 结论

**推荐：微信登录（小程序内一键）作为主入口 + GitHub OAuth 作为补充，匿名保留极小额度；jiaowa 使用服务 key，小程序调用方式不变。**

理由：
1. 用户群在中国，微信是摩擦最低、防批量最强的身份；
2. 不需要邮箱系统，开发量集中在微信授权（1–1.5 天）；
3. 桌面端只多一个“密钥”输入框，匿名额度保证“下载即用”的体验；
4. 小程序链路零改动，符合“不影响小程序”的约束。

如果希望**完全不收集身份**，则选择 A（PoW）+ 严格匿名额度；但一旦需要按用户限额或封禁，最终还是会回到 key。


---

## 四、实施记录（2026-10-20）

### 已上线

| 项 | 位置 | 状态 |
|---|---|---|
| nginx 入口限速（4 r/s，burst 6，429） | `/etc/nginx/conf.d/ttskyannot-limits.conf` | 已生效，jiaowa 的 IP 不计入 |
| 服务端分级限流 + 解析槽预算 | `interactions_service.py` | 已生效 |
| API Key 表、邀请码表 | `interactions.db`（`api_keys` / `invites`） | 已建 |
| 领取接口 | `POST /api/i/key/redeem`（邀请码）、`/key/github`、`/key/wechat` | 已上线 |
| 公开配置 | `GET /api/i/key/config`（只返回 client id / appid 是否存在） | 已上线 |
| 密钥校验 | 解析请求头 `X-API-Key`；无效 401，超额 429 + `Retry-After` | 已上线 |
| 自助领取 | `GET /api/i/key/challenge` + `POST /api/i/key/claim`（16 位前导零的工作量证明） | 已上线 |
| 命令行管理 | `/home/ubuntu/key_admin.py invite/key/list/revoke` | 已有 |
| 桌面端 | 0.2.5：设置「在线解析密钥」，请求带 `X-API-Key` + UA | 已发布 |
| 领取页 | <https://www.ttastro.com/ttskyannot/key> | 已上线 |

### 自助领取（不需要人工发邀请码）

领取页第一个卡片就是「免费领取」：浏览器从 `/api/i/key/challenge` 拿一个随机 challenge，算出满足 16 位前导零的
nonce（约 6.5 万次 SHA-256，页面上一两秒），再提交给 `/api/i/key/claim` 换一个密钥。

- 每个来源网络每天 2 个，全局每小时 30 个；
- challenge 一次性、5 分钟过期；
- 密钥绑定到来源 IP 的哈希（`owner = ip:<hash>`），便于排查；
- 邀请码仍然保留，用于定向发放；GitHub / 微信登录配置好之后是更强的身份入口。

### 额度

- 密钥：每小时 150 次、每天 600 次（`KEY_QUOTAS["free"]`）；
- 匿名（没有密钥的桌面端）：每小时 6 次、每天 20 次（环境变量 `TTASTRO_ANON_HOUR` / `TTASTRO_ANON_DAY`，默认值即此）；
- 小程序池（jiaowa 的来源 IP）：不受匿名额度影响，保持每分钟 300 次 + 解析槽 450 秒/10 分钟；
- 全局：每分钟 90 次、盲解每小时 12 次。

### 还需要配置才能启用

**GitHub 登录**：在 GitHub 建一个 OAuth App（回调地址填 `https://www.ttastro.com/ttskyannot/key`），
然后给服务加环境变量并重启：

```
sudo systemctl edit ttastro-interactions
# [Service]
# Environment=TT_GITHUB_ID=Ov23...
# Environment=TT_GITHUB_SECRET=...
sudo systemctl restart ttastro-interactions
```

配置后 `/api/i/key/config` 会返回 `github: true`，领取页上的 GitHub 按钮自动出现。

**微信登录（网站扫码）**：需要微信开放平台的「网站应用」（要认证），拿到 AppID/AppSecret 后同样加
`TT_WX_APPID` / `TT_WX_SECRET`。回调地址同样是 `https://www.ttastro.com/ttskyannot/key`。
**小程序内一键领 key** 走的是另一条链路：由 jiaowa 增加一个 `a=key_issue` 动作（用小程序 openid 作为 owner），
这样不需要开放平台的网站应用。

**jiaowa 的服务密钥**：已在服务器生成并存于 `/home/ubuntu/.jiaowa_key`（600 权限，只有 jiaowa 的服务端需要读到）。
启用步骤：
1. jiaowa 转发 `/api/i/solve` 时加上 `X-API-Key: <那个值>`；
2. 之后 ttastro 会把小程序请求记为 service 密钥（每小时 1200 次、每天 20000 次）；
3. 确认 Mini Program 正常后，可以把“小程序池”的 IP 兜底收紧，只信任带服务密钥的转发。

### 运维

- 备份：改动前已备份到 `/root/ttastro.nginx.bak-*`、`/home/ubuntu/interactions_service.py.bak-*`；
- 查看密钥：`python3 /home/ubuntu/key_admin.py list`；
- 吊销：`python3 /home/ubuntu/key_admin.py revoke <id>`；
- 发邀请码：`python3 /home/ubuntu/key_admin.py invite 5 --note "..."`；
- 日志：`sudo journalctl -u ttastro-interactions | grep solve`，每行包含 IP、是否小程序池、密钥标签、耗时、UA。
