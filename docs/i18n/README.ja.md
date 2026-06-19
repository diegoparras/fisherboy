<div align="center">

# 🎣 Fisherboy

**ウェブからデータを取り出す、あなたの相棒。**

任意のページに向けるだけで、**きれいな Markdown または構造化 JSON** が手に入ります —— どんな
LLM にもそのまま渡せます。Fisherboy はサイトが抵抗したときだけ段階的に強化し（静的 → TLS
フィンガープリント → ステルスブラウザ → 実ブラウザ）、シングルページアプリが既に読み込んでいる
**隠れた JSON/XHR** を捕捉し、ページネーションを辿りツリー状にクロールし、**配信前に PII を
匿名化**します。セルフホスト可能で、独自の Web UI を備え、ヘッドレスな REST + MCP サービスとしても
動きます。[**Escriba**](https://github.com/diegoparras/escriba) ファミリーの一員です。

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · [Español](README.es.md) · [Français](README.fr.md) · [Português](README.pt.md) · [Italiano](README.it.md) · [中文](README.zh.md) · **日本語**

</div>

---

## ✨ 機能

- 🎣 **任意のページ → きれいな Markdown または JSON** —— [Crawl4AI](https://github.com/unclecode/crawl4ai) の `fit_markdown`（密度でナビ/定型部を剪定）と [Trafilatura](https://github.com/adbar/trafilatura) のフォールバック。または LLM で JSON Schema への構造化抽出。
- 🪜 **段階的フェッチ（ブロックされた時だけ昇格）** —— tier 0 `httpx` → tier 1 TLS フィンガープリント（`curl_cffi`）→ tier 2 ステルスブラウザ（Camoufox/Patchright）→ tier 3 実ブラウザ（nodriver/Playwright）。ゲートがブロック/CAPTCHA を検知して昇格し、勝った tier は**ドメイン単位でキャッシュ**します。
- 🛰️ **隠れた API のキャプチャ** —— レンダリング済み HTML と格闘する代わりに、ページが既に読み込む **XHR/fetch の JSON** を観測して保持します。SPA や動的グリッドに最も確実な方法です。
- 🕷️ **スパイダーと深いクロール** —— 内部リンクをツリー状に辿り（セクション単位に限定可）、ページネーション（ASP.NET ポストバック・「次へ」・`?page=`）を走査し、各ノードのコンテンツ + API をデータツリーに集める**タランチュラ**モードも備えます。
- 🔌 **プロキシをかんたんに** —— **任意の形式**（`host:port` · `host:port:user:pass` · `user:pass@host:port` · URL）で貼り付ければ Fisherboy が正規化します。**テスト**ボタンはプロキシ経由でリクエストを送り、**出口 IP + 国 + レイテンシ**を表示し、接続できない場合は実行可能なヒントを出します。ローテーション/クールダウン付きプール、ジョブごとの上書き、プロキシ保存。
- 🍪 **セッション Cookie、拡張機能なし** —— Cookie を貼り付け（Netscape `cookies.txt` / JSON / `name=value`）、またはローカルブラウザ（Chrome/Firefox/Edge/Brave）から直接読み取り、ログインや地域の壁の向こうのページに対応します。
- 🛡️ **配信前の PII 匿名化** —— ロールで制限される 3 つのプライバシーモード：**opaque**（`«PERSON_1»`）、**reversible**（マスク → LLM が推論 → ローカルで復元）、**direct**（生データ、機微でないデータ用）。フェイルクローズド：匿名化に失敗したら生データは一切出ません。[Escriba](https://github.com/diegoparras/escriba) の Anonimal で完全な NER に、スタンドアロンでは内蔵の正規表現パス（メール/ID/IP/カード/電話）にフォールバックします。
- ✏️ **内蔵エディタ** —— 結果を **Markdown · JSON · テーブル** のタブ付きモーダルで開けます：ライブプレビュー付き Markdown ツールバー、検証付き JSON エディタ、編集可能なテーブル —— **JSON ↔ テーブルはタブの切り替えだけ**。`.md` / `.json` / `.csv` をダウンロード。
- 📤 **すべてダウンロード** —— エンベロープ全体、データのみ（コンテンツ + レコード + ツリー + リンク）、またはフラットなレコード配列。ワンクリックで結果を **Escriba に送信**して、変換 / 匿名化 / エクスポートを続けられます。
- 🔑 **3 つのアクセスレベル** —— DIOS / ANGEL / HUMANO、それぞれ独自のパスワードと制限。
- 🐳 **自己完結イメージ** —— API + worker + Redis。Escriba の背後でヘッドレス（REST + MCP）に、または**独自 Web UI でスタンドアロン**に動作。
- 🛡️ **堅牢化** —— 既定でフェイルクローズド、アンチ SSRF（リダイレクトの各ホップで再検証）、ジョブごとのシークレット除去、REST **と** MCP でのロールゲーティング、レート制限、非 root コンテナ。監査済み。[`docs/ADR-012`](../ADR-012-auditoria-seguridad.md) を参照。
- 🌐 **REST + MCP** —— `curl`、n8n、Claude Code、Escriba から操作。

---

## 🚀 クイックスタート（Docker）

最速の手順 —— スタンドアロン、Web UI 付き：

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD を設定
docker compose -f docker-compose.standalone.yml up -d --build
# → http://localhost:8000 を開く
```

ビルドしたくない？公開済みイメージを取得：

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **完全なデプロイガイド**（Docker Desktop の手順、EasyPanel、環境変数リファレンス、本番化）：[`docs/DEPLOY.md`](../DEPLOY.md)。

---

## 🧭 2 つのモード

Fisherboy は `APP_MODE` により 2 つのモードのいずれかで動作します。**コアは同一**で、モードは
Web UI をマウントするか、ドキュメント変換をどこに委譲するかだけを決めます。

| | `standalone` | `sidekick` |
|---|---|---|
| Web UI | ✅ 独自 | ❌ ヘッドレス |
| インターフェース | UI + REST + MCP | REST + MCP |
| 用途 | セルフホスト、個人 | Escriba の背後、内部ネットワーク |

---

## 🔌 REST API

```http
POST /api/jobs            # スキーマ・ロール × モード・コールバック・プロキシ（SSRF）を検証し、キューへ → 202
GET  /api/jobs/{job_id}   # ステータスと結果（「エンベロープ」）
POST /api/proxy/test      # プロキシ経由でリクエストを送り、出口 IP + 国 + レイテンシを返す
POST /api/revert          # 仮名化されたコンテンツを復元（reversible モード）
GET  /healthz · GET /metrics
```

ジョブのフィールド：`url`、`rol`、`privacy_mode`（`opaco`/`reversible`/`directo`）、
`output_format`（`markdown`/`llms_txt`/`json`）、`tier_hint`（0–3）、`crawl_depth`、`max_pages`、
`paginate`、`capture_api`、`tarantula`、`extract_schema`、`proxy`、`cookies`、`callback_url`。
同じパイプラインは MCP ツールとしても公開されます：`python -m app.mcp_server`。

---

## 🔒 プライバシーとロール

モードは**ジョブごと**に選び、**ロールで制限**されます（`privacy_matrix.yaml`）。ロールが要求の
モードを許可しない場合、ゲートウェイは **403** を返します —— 黙ってダウングレードしません。

| ロール | opaque | reversible | direct |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

NER（Anonimal がある場合）に加えて、高リスク PII（公的 ID、メール、IP、Luhn 検証のカード、電話）に
対し決定的な正規表現パスを常に実行します。

---

## 🛡️ セキュリティ

マルチエージェントの敵対的レビューで監査済み。発見事項は修正され、テストで固定されています
（[`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)）。

- **既定でフェイルクローズド** —— パスワード未設定なら 401。開発用のオープンモードは明示的なオプトイン（`FISHERBOY_OPEN_GOD=1`）。
- **アンチ SSRF** —— プライベート/ループバック/リンクローカル/クラウドメタデータ帯域をブロックし、リダイレクトの**各ホップ**とブラウザの各リクエストで再検証。プロキシ上書きも同様に検証。
- **シークレットを漏らさない** —— ジョブごとのシークレット（プロキシ資格情報、CAPTCHA キー、Cookie）はエンベロープと webhook から除去されます。
- **REST と MCP でのロールゲーティング**、レート制限、非 root コンテナ、PII を含まない JSON ログ。

公開前に[本番チェックリスト](../DEPLOY.md#going-to-production)を確認してください。

---

## 🧩 Escriba ファミリー

Fisherboy は [**Escriba**](https://github.com/diegoparras/escriba) のスタンドアロン衛星です ——
Escriba は任意のドキュメントを、AI 用にきれいで匿名化された Markdown に変えるハブです。各アプリは
単体で使えますが、デザインシステムとワンクリックの **「Escriba に送る」** 受け渡しを共有します ——
ウェブで釣り上げたものが、変換・匿名化・チャンク化・エクスポートへとそのまま流れます。

---

## 📜 ライセンス

MIT © 2026 Diego Parrás。Fisherboy が使用できるサードパーティのスクレイパーは各自のライセンスに
従います（多くは寛容型：Crawl4AI、Trafilatura — Apache‑2.0；curl_cffi、httpx — MIT/BSD）。一部の
オプションエンジンはネットワーク copyleft（AGPL：nodriver、Firecrawl）です：個人の非商用利用では
何も課しませんが、商用サービスとして提供する場合は変更の公開が必要です。

作者：Diego Parrás。
