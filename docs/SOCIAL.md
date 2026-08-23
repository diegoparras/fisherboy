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

| Network | Status | Notes |
|---|---|---|
| **X / Twitter** | ✅ posts | Needs a logged-in session |
| **LinkedIn** | 🔜 detected, no extractor yet | Hardest: aggressive detection, fast bans |
| **Facebook** | 🔜 detected, no extractor yet | `mbasic.facebook.com` is the lighter path |
| **Instagram** | partial | Comments + followers already via `/api/instagram/*` |
| **Reddit / YouTube** | ✅ comments | Via `/api/comments`, no session needed for Reddit |

Networks without a post extractor still work as regular scraping — you just don't get
normalized records.

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
