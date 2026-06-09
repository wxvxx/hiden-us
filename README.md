# HidenCloud 自动续期

## 配置

在仓库 `Settings → Secrets and variables → Actions` 中添加以下 Secrets：

| Secret 名称 | 必填 | 说明 | 示例 |
|---|---|---|---|
| `HIDENCLOUD_COOKIE` | ✅ | HidenCloud cookie |  |
| `HIDENCLOUD_EMAIL` | ✅ | HidenCloud    账号  |  |
| `HIDENCLOUD_PASSWORD` | ✅ | HidenCloud 密码  |  |
| `PROXY_NODE` | ✅ | 代理节点地址，支持多种协议 | 见下方代理格式 |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID | `123456789` |


### 代理格式（最好本地浏览器试试能不能登陆再用）`最好用注册号的代理`

`PROXY_NODE` 支持以下任意一种代理协议的完整分享链接（不配置则直连）：

- **VLESS**：`vless://uuid@server:port?security=reality&sni=...&type=ws&...`
- **VMess**：`vmess://base64encoded`
- **Trojan**：`trojan://password@server:port?sni=...&type=ws&...`
- **Shadowsocks**：`ss://base64@server:port`
- **SOCKS5**：`socks5://user:pass@server:port` 或 `socks5://server:port`

---

**⚠️ 免责声明**：本脚本仅供学习交流使用，使用者需遵守 [HidenCloud](https://hidencloud.com) 的服务条款。因使用本脚本造成的任何问题，作者不承担任何责任。
