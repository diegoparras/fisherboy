# Browser actions — operating the page before extracting it

Fisherboy extracts. With **actions** it also *operates*: dismiss the cookie banner, log in,
press "load more", scroll an infinite feed, take a screenshot. This is the part people used to
write Playwright by hand for — except it's declarative, and it rides on the browser tier
Fisherboy already had.

```jsonc
POST /api/jobs
{
  "url": "https://sitio.com/panel",
  "actions": [
    {"do": "click", "sel": "#aceptar-cookies", "optional": true},
    {"do": "type",  "sel": "#user", "text": "diego"},
    {"do": "type",  "sel": "#pass", "text": "…", "secret": true},
    {"do": "click", "sel": "button[type=submit]"},
    {"do": "wait_for", "sel": ".dashboard"},
    {"do": "scroll_until"},
    {"do": "screenshot", "name": "final", "full_page": true}
  ],
  "session": "mi-sitio"
}
```

Actions **force tier 2+** (you can't click with `httpx`), and they run on every page of a crawl
— which is what you want for a cookie banner or a login.

## Verbs

| `do` | Fields | What it does |
|---|---|---|
| `click` | `sel` | Clicks the element. |
| `type` / `fill` | `sel`, `text`, `enter?` | Fills an input. `enter: true` presses Enter after. |
| `press` | `key`, `sel?` | Presses a key (`"Enter"`, `"Escape"`, …). |
| `hover` | `sel` | Hovers (reveals menus/tooltips). |
| `select` | `sel`, `value` | Picks an option in a `<select>`. |
| `wait` | `s` | Waits N seconds. |
| `wait_for` | `sel`, `state?` | Waits for an element (`visible`\|`hidden`\|`attached`). |
| `scroll` | `amount?`, `times?` | Scrolls a fixed amount. |
| `scroll_until` | `max_rounds?`, `pause_s?` | **Infinite scroll**: keeps going until the page stops growing. |
| `goto` | `url` | Navigates. Re-validated against SSRF. |
| `screenshot` | `name?`, `full_page?` | PNG, retrievable afterwards. |
| `pdf` | `name?` | PDF (Chromium only). |
| `eval` | `script` | Arbitrary JS — **off by default**, see below. |

Every step takes `optional: true` (if it fails, log it and carry on — right for a cookie banner
that isn't always there) and `timeout_s`. A **non-optional** step that fails **fails the job**:
returning the wrong page silently would be worse than an error.

## Login and persistent sessions

`login` is sugar that expands into actions and runs first:

```jsonc
{"login": {"user_sel": "#u", "user": "diego", "pass_sel": "#p", "password": "…",
           "submit_sel": "#entrar", "wait_for": ".panel"}}
```

Pair it with `"session": "a-name"` and the browser state (cookies + localStorage) is saved, so
the *next* job starts already logged in:

```jsonc
job 1 → {"actions": [...login...], "session": "mi-sitio"}   // logs in, saves
job 2 → {"url": ".../otra-pagina", "session": "mi-sitio"}   // straight in, no login
```

Sessions live in Redis with `BROWSER_SESSION_TTL_S` and are **namespaced by owner** — guessing
someone else's session name gets you nothing.

## Screenshots and PDFs

They're bytes, so they don't travel inside the envelope. The job records their names in
`meta.artifact_names`, and you fetch them at:

```
GET /api/jobs/{job_id}/artifacts/{name}
```

Same ownership rule as the job itself, and only names that job actually produced.

## Secrets

A password passed in `actions`/`login` **never comes back out**: those keys are scrubbed from
`GET /api/jobs` and from the callback, and any step marked `secret: true` is redacted in the
action log (which *is* returned, in `meta.actions_log`, so you can debug your selectors).

## `eval`, and why it's off

`eval` runs arbitrary JavaScript in the page. That's genuinely more dangerous than the rest:
a script can reach the server's internal network *from inside the browser*, sidestepping the
SSRF check done on the URL. So it needs **two keys at once**:

1. `BROWSER_ALLOW_EVAL=1` on the server, and
2. the `dios` role on the request.

Everything else in this document is safe by default and needs no configuration.
