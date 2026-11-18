# MCP — Fisherboy as a tool your agent can call

Fisherboy speaks [MCP](https://modelcontextprotocol.io), so an agent (Claude Code, Claude
Desktop, n8n, Escriba, anything that speaks the protocol) can scrape through it without you
writing a single HTTP request.

Two transports, same server, same role gating:

| Transport | Where | Who uses it |
|---|---|---|
| **HTTP** | `https://your-fisherboy/mcp` | Anything remote: Claude Desktop, Claude Code, n8n, another machine |
| **stdio** | `python -m app.mcp_server` | A process on the same box — what Escriba uses internally |

## The door

The key is the **same `API_TOKEN`** as the REST API. Either header works:

```
Authorization: Bearer <API_TOKEN>
X-API-Key: <API_TOKEN>
```

It's **fail-closed**: with no `API_TOKEN` set, `/mcp` answers `401` to everything rather than
standing open. `/mcp` and `/mcp/` both work, with no redirect in between — some clients won't
follow a `307` on a POST with a body.

## Connecting

**Claude Code**

```bash
claude mcp add fisherboy --transport http https://your-fisherboy/mcp \
  --header "Authorization: Bearer $API_TOKEN"
```

**Claude Desktop / anything with a JSON config**

```jsonc
{
  "mcpServers": {
    "fisherboy": {
      "type": "http",
      "url": "https://your-fisherboy/mcp",
      "headers": { "Authorization": "Bearer EL_TOKEN" }
    }
  }
}
```

**Checking it by hand** — the handshake answers with a session id:

```bash
curl -i -X POST https://your-fisherboy/mcp \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
        "protocolVersion":"2025-06-18","capabilities":{},
        "clientInfo":{"name":"curl","version":"1"}}}'
```

`200` plus an `mcp-session-id` header means you're in. `401` means the token didn't match.

## The tools

| Tool | What it does |
|---|---|
| `submit_job(url, …)` | Enqueues a scrape and returns `{job_id, status}`. Takes the same options as `POST /api/jobs`: `output_format`, `tier_hint`, `crawl_depth`, `max_pages`, `paginate`, `capture_api`, `tarantula`, `extract_schema`, `proxy`, `cookies`, `callback_url`. |
| `get_job(job_id)` | The envelope: status and result. Secrets (proxy, captcha keys, cookies) are stripped. |
| `revert(content, mapping_ref)` | Rehydrates pseudonymized content (`reversible` mode). Single use. |

Jobs are asynchronous: `submit_job` returns immediately, then you poll `get_job`. A worker has
to be running — the MCP only enqueues.

## Roles: the operator decides, not the caller

`MCP_ROLE` is both the **default** and the **ceiling**:

```
MCP_ROLE=humano   → cheap tiers, no expensive weapons
MCP_ROLE=angel    → browser, proxy, API capture
MCP_ROLE=dios     → everything, including the captcha solver and tarantula
```

A caller may pass `rol` to ask for **less**. Asking for more is ignored, silently and on
purpose — accepting the caller's role was a privilege escalation (2026-06 audit).

> **Picking `dios` is a real decision.** It puts every weapon in the system behind one static
> token, reachable from the internet. The token is the whole perimeter: rotate it like a
> password, never put it in a URL, and if you want the MCP available but harmless, `angel` is
> the sensible middle.

To stop publishing it on the web entirely, set `MCP_HTTP=0`. stdio keeps working.

## Rate limiting and privacy

Jobs from the MCP share the REST rate limit (`MAX_JOBS_PER_MIN`) under their own bucket.
`privacy_mode` behaves exactly as in the REST API and stays bounded by the role
(`privacy_matrix.yaml`), so an agent can't talk its way into raw data its role doesn't allow.
