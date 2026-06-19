<div align="center">

# 🎣 Fisherboy

**你的网页数据提取助手。**

将它指向任意网页，即可获得**干净的 Markdown 或结构化 JSON** —— 可直接喂给任意 LLM。当网站设防时
Fisherboy 才逐级升级（静态 → TLS 指纹 → 隐身浏览器 → 真实浏览器），捕获单页应用已在加载的
**隐藏 JSON/XHR**，跟随分页并以树状抓取，并在交付前**对 PII 进行匿名化**。可自托管，自带网页
界面，也可作为无界面的 REST + MCP 服务。它是 [**Escriba**](https://github.com/diegoparras/escriba)
家族的一员。

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · [Español](README.es.md) · [Français](README.fr.md) · [Português](README.pt.md) · [Italiano](README.it.md) · **中文** · [日本語](README.ja.md)

</div>

---

## ✨ 功能

- 🎣 **任意网页 → 干净的 Markdown 或 JSON** —— [Crawl4AI](https://github.com/unclecode/crawl4ai) 的 `fit_markdown`（按密度剪除导航/样板）并以 [Trafilatura](https://github.com/adbar/trafilatura) 作为兜底；或通过 LLM 按 JSON Schema 进行结构化抽取。
- 🪜 **分级抓取（仅在被拦截时升级）** —— 第 0 级 `httpx` → 第 1 级 TLS 指纹（`curl_cffi`）→ 第 2 级隐身浏览器（Camoufox/Patchright）→ 第 3 级真实浏览器（nodriver/Playwright）。网关检测到拦截/验证码即升级；获胜的级别**按域名缓存**。
- 🛰️ **捕获隐藏 API** —— 与其与渲染后的 HTML 较劲，Fisherboy 监听页面已加载的 **XHR/fetch JSON** 并保留它。这是抓取 SPA 与动态表格最可靠的方式。
- 🕷️ **蜘蛛与深度抓取** —— 以树状跟随站内链接（可限定到某个区块），扫描分页（ASP.NET 回发 ·“下一页”· `?page=`），以及**塔兰图拉**模式：抓取每个节点的内容 + API，构成数据树。
- 🔌 **轻松配置代理** —— 以**任意格式**粘贴代理（`主机:端口` · `主机:端口:用户:密码` · `用户:密码@主机:端口` · URL），Fisherboy 会自动规范化。**测试**按钮会通过该代理发起请求，显示你的**出口 IP + 国家 + 延迟**，无法连接时给出可操作的提示。带轮换/冷却的代理池、按任务覆盖、保存常用代理。
- 🍪 **会话 Cookie，无需扩展** —— 粘贴 Cookie（Netscape `cookies.txt` / JSON / `名称=值`），或直接从本地浏览器（Chrome/Firefox/Edge/Brave）读取，用于登录或地区受限的页面。
- 🛡️ **交付前的 PII 匿名化** —— 三种受角色限制的隐私模式：**不透明**（`«PERSON_1»`）、**可逆**（先打码 → 让 LLM 推理 → 本地还原）和**直接**（原始，仅用于非敏感数据）。失败即关闭：若匿名化失败，绝不输出任何原始内容。配合 [Escriba](https://github.com/diegoparras/escriba) 的 Anonimal 可获得完整 NER；独立运行时回退到内置的正则匿名化（邮箱/证件号/IP/银行卡/电话）。
- ✏️ **内置编辑器** —— 在带 **Markdown · JSON · 表格** 选项卡的弹窗中打开结果：带实时预览的 Markdown 工具栏、带校验的 JSON 编辑器，以及可编辑表格 —— **JSON ↔ 表格只需切换选项卡**。可下载 `.md` / `.json` / `.csv`。
- 📤 **下载一切** —— 整个信封、仅数据（内容 + 记录 + 树 + 链接），或扁平的记录数组。一键将结果**发送到 Escriba**，继续转换 / 匿名化 / 导出。
- 🔑 **三种访问级别** —— DIOS / ANGEL / HUMANO，各有自己的密码与限额。
- 🐳 **自包含镜像** —— API + worker + Redis。可在 Escriba 之后以无界面（REST + MCP）运行，或**独立运行并自带网页界面**。
- 🛡️ **已加固** —— 默认失败即关闭、反 SSRF（每一跳重定向都重新校验）、按任务清除密钥、REST **与** MCP 的角色门控、限流、非 root 容器。已审计；见 [`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)。
- 🌐 **REST + MCP** —— 通过 `curl`、n8n、Claude Code 或 Escriba 驱动。

---

## 🚀 快速开始（Docker）

最快的方式 —— 独立运行，带网页界面：

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # 设置 SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → 打开 http://localhost:8000
```

不想自行构建？拉取已发布的镜像：

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **完整部署指南**（Docker Desktop 分步、EasyPanel、环境变量参考、上生产）：[`docs/DEPLOY.md`](../DEPLOY.md)。

---

## 🧭 两种模式

Fisherboy 通过 `APP_MODE` 以两种模式之一运行。**核心完全相同**；模式只决定是否挂载网页界面，
以及把文档转换委托给谁。

| | `standalone` | `sidekick` |
|---|---|---|
| 网页界面 | ✅ 自带 | ❌ 无界面 |
| 接口 | UI + REST + MCP | REST + MCP |
| 用途 | 自托管、个人 | 置于 Escriba 之后，内网 |

---

## 🔌 REST API

```http
POST /api/jobs            # 校验 schema、角色 × 模式、回调与代理（SSRF）；入队 → 202
GET  /api/jobs/{job_id}   # 状态与结果（“信封”）
POST /api/proxy/test      # 通过代理发起请求；返回出口 IP + 国家 + 延迟
POST /api/revert          # 还原经假名化的内容（可逆模式）
GET  /healthz · GET /metrics
```

任务字段：`url`、`rol`、`privacy_mode`（`opaco`/`reversible`/`directo`）、`output_format`
（`markdown`/`llms_txt`/`json`）、`tier_hint`（0–3）、`crawl_depth`、`max_pages`、`paginate`、
`capture_api`、`tarantula`、`extract_schema`、`proxy`、`cookies`、`callback_url`。同一管线也作为
MCP 工具暴露：`python -m app.mcp_server`。

---

## 🔒 隐私与角色

模式**按任务**选择并**受角色限制**（`privacy_matrix.yaml`）。若角色不允许所请求的模式，网关返回
**403** —— 绝不静默降级。

| 角色 | 不透明 | 可逆 | 直接 |
|------|:------:|:----:|:----:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

除 NER（存在 Anonimal 时）之外，始终会对高风险 PII（证件号、邮箱、IP、Luhn 校验的银行卡、电话）
运行一次确定性的正则处理。

---

## 🛡️ 安全

经过多智能体对抗式审计；问题已修复并由测试锁定（[`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)）。

- **默认失败即关闭** —— 未配置密码时返回 401；开发用的开放模式需显式启用（`FISHERBOY_OPEN_GOD=1`）。
- **反 SSRF** —— 拦截私网/回环/链路本地/云元数据网段，并在**每一跳**重定向与每个浏览器请求时重新校验；代理覆盖同样校验。
- **不泄露密钥** —— 按任务的密钥（代理凭据、验证码密钥、Cookie）会从信封与 webhook 中清除。
- **REST 与 MCP 的角色门控**、限流、非 root 容器、不含 PII 的 JSON 日志。

公开前请查看[生产清单](../DEPLOY.md#going-to-production)。

---

## 🧩 Escriba 家族

Fisherboy 是 [**Escriba**](https://github.com/diegoparras/escriba) 的独立卫星 —— Escriba 是把任意
文档转成干净、匿名、可供 AI 使用的 Markdown 的中枢。每个应用都能单独使用，但它们共享一套设计系统
和一键 **“发送到 Escriba”** 交接 —— 于是你从网上钓到的内容可直接流向转换、匿名化、分块与导出。

---

## 📜 许可证

MIT © 2026 Diego Parrás。Fisherboy 可使用的第三方抓取器各有其许可证（多为宽松型：Crawl4AI、
Trafilatura — Apache‑2.0；curl_cffi、httpx — MIT/BSD）。部分可选引擎为网络 copyleft（AGPL：
nodriver、Firecrawl）：个人非商业使用不施加任何义务；作为商业服务提供则需公开你的修改。

作者：Diego Parrás。
