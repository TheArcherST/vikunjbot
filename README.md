# vikunjbot

`vikunjbot` connects Vikunja project webhooks with Telegram. It consists of two
processes sharing one durable SQLite database:

- `vikunjbot-event-relay` accepts Vikunja webhooks on the private Docker network and
  commits them to SQLite before returning `202 Accepted`.
- `vikunjbot` polls that queue, sends or updates the corresponding Telegram task
  message, and accepts replies as Vikunja comments, label changes, or assignments.

## Include it in a Vikunja Compose stack

Keep this directory as `./vikunjbot` beside your instance's `compose.yaml`. Add the
following reference configuration to that compose file. It assumes the existing
Vikunja service is named `vikunja`, runs Vikunja 2.4 or newer, and is connected to a
private `backend` network. The bot uses Vikunja API v2 only.

```yaml
services:
  vikunja:
    environment:
      # Required for internal webhook target URLs.
      VIKUNJA_OUTGOINGREQUESTS_ALLOWNONROUTABLEIPS: "true"

  vikunjbot-event-relay:
    build: ./vikunjbot
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    environment:
      APP_DB_PATH: /data/vikunjbot.sqlite3
    command: ["vikunjbot-relay"]
    volumes:
      - ./vikunjbot-data:/data
    networks:
      - backend

  vikunjbot:
    build: ./vikunjbot
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    env_file:
      - path: ./vikunjbot/.env
        required: false
    environment:
      APP_DB_PATH: /data/vikunjbot.sqlite3
    command: ["vikunjbot"]
    volumes:
      - ./vikunjbot-data:/data
    networks:
      - backend
    depends_on:
      vikunjbot-event-relay:
        condition: service_started
      vikunja:
        condition: service_started
```

The example uses `./vikunjbot-data` as a bind mount, so both the SQLite state and
encrypted token bindings are visible beside the instance compose file. Add this path
to the host repository's `.gitignore`, restrict its host permissions, and include it
in backups. The images briefly drop from root to an unprivileged user after making a
new bind mount writable.

The relay deliberately does **not** verify an HMAC, as requested. Do not attach
`vikunjbot-event-relay` to a public network, publish its port, or add a Traefik route.

> **Security:** `ALLOWNONROUTABLEIPS` broadens Vikunja's outbound access. In a
> multi-user instance, prefer a Mole proxy with an ACL limited to the relay.

Create `./vikunjbot/.env` from `./vikunjbot/.env.example` and fill in the required
values. The root `compose.yaml` loads this file only for the bot service, so these
settings do not need to be in the root Compose project's `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TOKEN_ENCRYPTION_KEY=...
# Optional: http://proxy.example:8080 or socks5://user:password@proxy.example:1080
TELEGRAM_PROXY_URL=
```

Generate the encryption key once with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Do not rotate `TOKEN_ENCRYPTION_KEY` without first asking users to reconnect:
existing encrypted bindings cannot be read with a new key.

`TELEGRAM_PROXY_URL` is optional. When set, all Telegram Bot API requests (including
polling, sends, and edits) use that HTTP, SOCKS4, or SOCKS5 proxy. Its URL must include
a scheme, host, and port; URL-encode reserved characters in proxy credentials. Only
the `vikunjbot` process uses the proxy; the relay and Vikunja traffic remain direct.
If the proxy runs in another Docker network, attach that network to `vikunjbot` too;
for example, an external Xray network can be connected with this Compose fragment
(replace `xray_default` with its actual name):

```yaml
services:
  vikunjbot:
    networks:
      - backend
      - telegram-proxy

networks:
  telegram-proxy:
    external: true
    name: xray_default
```

Start or rebuild the host stack normally:

```bash
docker compose up -d --build
```

Then, in a private chat with the bot, send `/login <Vikunja API token>`. API tokens
are created in Vikunja's Settings → API Tokens. The bot validates that directly
submitted token once and stores only a Fernet-encrypted form in
`./vikunjbot-data/` beside the host compose file.

In the destination private chat or group, send `/webhook 1d`. It gives a target URL
to paste into the desired Vikunja project webhook. `/install_webhook <project-id> 1d`
creates the same project webhook via the connected user's API token.

For a regular group, add the bot as an administrator so it can post and edit task
messages. For a **channel with a linked discussion**, add the bot as a channel
administrator with permission to post, and as an administrator in the linked discussion
group too.
Then, in a private chat with the bot, forward any post from that channel and reply to
it with `/install_channel_webhook <project-id> [expiry]`.

The bot verifies that the requester is a channel administrator, that the channel
actually has a linked discussion, and that the bot can post in both required places.
It then publishes task messages in the channel itself. Telegram's automatic forward
creates the corresponding thread in the linked discussion, and replies in that thread
are treated as replies to the channel task message — not as a separate group route.

## Stream tags and access boundary

The final component of a webhook URL is a route tag. The bot emits this form:

```text
http://vikunjbot-event-relay:8080/events/telegram-id:123456,telegram-chat-id:-100987654321,expiry:1d
```

- `telegram-id` identifies the Telegram account allowed to act on that event.
  Without a `telegram-chat-id`, it is also the private-message recipient.
- `telegram-chat-id` targets a regular group. If present, it prevents an additional
  private copy from being sent.
- `telegram-channel-id` together with `telegram-discussion-chat-id` targets a channel
  and its one linked discussion. These directives are produced by
  `/install_channel_webhook` after the Telegram-side checks above.
- `expiry` is mandatory and supports positive `s`, `m`, `h`, `d`, and `w` values.
  Its deadline is calculated from Vikunja's event timestamp, not delivery time.

A `telegram-chat-id` without a `telegram-id` is intentionally read-only. This keeps a
copy-only channel from accidentally granting its members control over a task.

For a channel route, the database records both the channel post and its verified linked
discussion ID. A reply can trigger a Vikunja action only when it is under Telegram's
automatic forward of that stored channel post in that exact discussion, and only from
the `telegram-id` that installed the route. A manually forwarded post in another group
does not pass this check.

Only a reply to the persistent message of a routed task can initiate a Vikunja action.
Before any such Telegram-originated action, the bot calls `GET /user` exactly once
using the stored token; a revoked or expired token cannot be used further in that
interaction. The check is deliberately absent for a token the user supplied directly
to `/login`, and the optional service account is used only for read-only event
enrichment.

## Telegram interaction

Reply to a task message with any combination of:

```text
*urgent @alex Please take a look before Friday
```

- `*urgent` toggles the `urgent` label (creating it if necessary).
- `@alex` toggles assignment of Vikunja user `alex`.
- Remaining text becomes a Vikunja comment from the linked Vikunja account.

Task messages remain tied to a task ID, so later updates edit the same message even
when its bucket or due date changes. Vikunja's empty zero-time due date is omitted,
rather than rendered as a deadline. In a group, an administrator can send
`/enable_comment_updates`; updates then also generate a short reply under the task
message. `/disable_comment_updates` reverses it. Channel routes deliberately omit
these extra summaries: posting one separately would break Telegram's channel-to-
discussion relationship; the original channel post is edited instead.

When a task is completed, the bot also sets its own `✅` reaction on the persistent
task message; it removes that reaction if the task is reopened. This is a visual
indicator only — Vikunja remains the source of truth. The reaction must be allowed in
the target chat or channel; an unavailable reaction is logged but never prevents task
message delivery.

Project update, deletion, and sharing events are also forwarded as standalone
notifications. They do not represent a task and therefore have no reply-to-act mapping.

## Optional `vikunjbot` service account

Some event payloads contain only a Kanban `bucket_id`, or a bucket object without its
title. Create a dedicated Vikunja account named `vikunjbot`, grant it read access to
resources you want enriched, issue an API token, and set `VIKUNJBOT_SERVICE_TOKEN`.
It is only used to read a task (including its buckets) when event data is incomplete;
it never performs a user-requested write. Without this access, an unknown bucket is
omitted rather than displayed as a potentially misleading numeric ID.

The webhook's bucket context takes priority over a later task read: Vikunja can omit
the view-specific `bucket_id` there even when `expand=buckets` returns bucket titles.
When an event supplies exactly one bucket but no ID, that bucket is used directly. If
several buckets are present without an ID, the bot omits the field rather than guess a
column from another view.

## Reliability model

SQLite runs in WAL mode with `synchronous=FULL`; a relay request is acknowledged only
after its event row is committed. Identical raw payloads to the same route are safely
deduplicated. The worker uses a persisted lease and an unbounded, capped exponential
backoff, so a restart or Telegram/Vikunja outage does not lose accepted events.

Delivery to Telegram is inherently at-least-once: no Telegram send API offers an
idempotency key, so a process crash after Telegram accepted a new message but before
SQLite recorded its `message_id` can cause one duplicate. Subsequent updates converge
on the saved message mapping.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The test suite runs without Telegram or a Vikunja instance.
