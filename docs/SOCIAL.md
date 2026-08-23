# Social networks — posts as data

```jsonc
POST /api/jobs
{
  "url": "https://x.com/someaccount",
  "social": true,
  "max_posts": 200,
  "session": "my-x-account",     // a logged-in browser session (see below)
  "privacy_mode": "opaco"        // posts are people's data — see Privacy
}
```

You get normalized records — same shape for every network, so a CSV from X lines up with one
from anywhere else:

```json
{"platform": "x", "id": "175…", "url": "https://x.com/user/status/175…",
 "author": "user", "author_name": "Real Name", "text": "…",
 "created_at": "2026-08-17T12:00:00+00:00",
 "likes": 12, "replies": 3, "reposts": 5, "views": 999, "media": ["https://…jpg"]}
```

## What actually works in 2026 (and what doesn't)

The easy paths are **dead**: snscrape, Twint and Nitter no longer work, and X's guest tokens
are now bound to a browser fingerprint with datacenter IPs permanently banned. Anything
promising "scrape X without logging in" is out of date.

What holds up is the boring path, and it's the one Fisherboy takes:

```
browser with a real session → the page calls its own internal API → we intercept THAT
```

And on what's intercepted we search **by shape, not by path**: the extractor walks the JSON
and picks up anything that looks like a post. When X moves a field from
`data.user.result.timeline` to somewhere else — and it will — the extractor keeps working.
That's the difference between a scraper that lasts weeks and one that lasts months.

| Network | Status | How |
|---|---|---|
| **X / Twitter** | ✅ posts | Internal GraphQL, anchored on `full_text` |
| **LinkedIn** | ✅ posts | Voyager API, anchored on the `$type` entity discriminator |
| **Facebook** | ✅ posts | GraphQL (`__typename: Story`), falling back to `mbasic` HTML |
| **Instagram** | partial | Comments + followers already via `/api/instagram/*` |
| **Reddit / YouTube** | ✅ comments | Via `/api/comments`, no session needed for Reddit |

Networks without a post extractor still work as regular scraping — you just don't get
normalized records.

### Per-network notes

**LinkedIn** uses its internal *Voyager* API, whose responses are `$type`-discriminated entity
graphs. We anchor on that discriminator rather than on a path, and pull the handle out of the
profile link. It's the network that detects and bans fastest — keep the volume low.

**Facebook** gets two paths, tried in order: the GraphQL responses (`__typename: Story`), and
if those yield nothing, the plain HTML of **`mbasic.facebook.com`** — which runs no
JavaScript and is far easier to read. Fisherboy rewrites `www.facebook.com/…` to `mbasic`
automatically. The HTML path is more brittle by nature; treat it as the fallback it is.

## Logging in: let the browser show you

The server has no login of yours, and pasting cookies by hand is miserable. So Fisherboy can
open **its own browser and put it on your screen** — you log in with your hands, 2FA and
captchas included, and it keeps the session.

Next to the **Session** field there are two buttons:

| Mode | What you see | Cost |
|---|---|---|
| **Iniciar sesión acá** (light) | The page, streamed as frames; your clicks and keystrokes are replayed on the server | Nothing extra — reuses the browser already in the image |
| **Modo completo** (VNC) | The whole desktop: **popups, new tabs**, address bar | ~330 MB of image, one session at a time |

Use the light one by default. Reach for VNC when the login opens a **popup** — "Sign in with
Google" and most OAuth flows do — because the light mode only ever shows one page and you'd be
stuck.

Both end the same way: hit **Ya me logueé, guardar** and the session is stored under the name
you chose. Jobs don't know or care which one you used.

The VNC server listens on **127.0.0.1 only** and is never exposed: your browser reaches it
through a WebSocket on this same API, which already requires your session and role. No extra
port to open in your deployment. If you'd rather have the smaller image, drop the `xvfb x11vnc
novnc` layer from the Dockerfile — the app detects they're missing and offers only the light
mode.

## Sessions: the part that actually matters

Every big network gates everything behind a login. Log in once with browser actions and name
the session; every later job reuses it:

```jsonc
// once
{"url": "https://x.com/login", "session": "my-x-account",
 "login": {"user_sel": "input[name=text]", "user": "…",
           "pass_sel": "input[name=password]", "password": "…"}}

// from then on
{"url": "https://x.com/someaccount", "social": true, "session": "my-x-account"}
```

See [ACTIONS.md](ACTIONS.md). Sessions are namespaced per owner and stored in Redis with a TTL.

## Not getting banned

This is the real constraint — not the parsing.

- **Use a throwaway account.** Never your main one. Session-based scraping is, as practitioners
  put it, account-ban roulette.
- **Use a residential proxy** (`YT_PROXY` / the Advanced panel). Datacenter IPs are flagged
  immediately on these platforms.
- **Go slow.** `scroll_actions` already paces itself; don't crank `max_posts` into the
  thousands on one account.
- **LinkedIn deserves extra care**: it detects fast, bans fast, and the company has a history
  of litigating against large-scale scraping. Low-volume personal use is a different animal
  from bulk harvesting, but the pacing matters more than any trick.

## Privacy

Posts are personal data. `privacy_mode` applies here like everywhere else, and in `opaco` /
`reversible` **nothing raw leaves** — the records are masked too, not just the text body. If
you're feeding an LLM or sharing results, prefer `opaco`.

## Maintenance

The hidden cost of any social scraper is upkeep: GraphQL `doc_id`s rotate every few weeks.
Shape-based extraction is what buys us resilience — we never hardcode an endpoint, we
intercept whatever the page itself calls.
