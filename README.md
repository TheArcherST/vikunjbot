# vikunjbot

`vikunjbot` connects Vikunja project webhooks with Telegram. It consists of two
application processes sharing a dedicated PostgreSQL database:

- `vikunjbot-event-relay` accepts Vikunja webhooks on the private Docker network and
  commits them to PostgreSQL before returning `202 Accepted`.
- `vikunjbot` polls that queue, sends or updates the corresponding Telegram task
  message, and accepts replies as Vikunja comments, label changes, or assignments.
- `vikunjbot-migrate` applies Alembic migrations before the relay and bot start.

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

  vikunjbot-postgres:
    image: postgres:18.4-trixie@sha256:8ff36f3c66371cba71d20ceedccfc3de9669a68737607888c4ef0af93abe8e39
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    env_file:
      - path: ./vikunjbot/.env
        required: false
    environment:
      POSTGRES_DB: vikunjbot
      POSTGRES_USER: vikunjbot
    volumes:
      - ./vikunjbot-data/postgres:/var/lib/postgresql
    networks:
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h localhost -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 30s

  vikunjbot-migrate:
    build: ./vikunjbot
    restart: "no"
    security_opt:
      - no-new-privileges:true
    env_file:
      - path: ./vikunjbot/.env
        required: false
    command: ["vikunjbot-migrate"]
    networks:
      - backend
    depends_on:
      vikunjbot-postgres:
        condition: service_healthy

  vikunjbot-event-relay:
    build: ./vikunjbot
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    env_file:
      - path: ./vikunjbot/.env
        required: false
    environment:
      RELAY_HOST: 0.0.0.0
      RELAY_PORT: 8080
    command: ["vikunjbot-relay"]
    networks:
      - backend
    depends_on:
      vikunjbot-migrate:
        condition: service_completed_successfully

  vikunjbot:
    build: ./vikunjbot
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    env_file:
      - path: ./vikunjbot/.env
        required: false
    command: ["vikunjbot"]
    networks:
      - backend
    depends_on:
      vikunjbot-event-relay:
        condition: service_started
      vikunjbot-migrate:
        condition: service_completed_successfully
      vikunja:
        condition: service_started
```

The example uses `./vikunjbot-data/postgres` as a bind mount, so all bot state — the
durable queue, encrypted token bindings, hook configuration, and task-message mapping
— is stored beside the instance compose file. Add `./vikunjbot-data` to the host
repository's `.gitignore`, restrict its host permissions, and include it in backups.
This is a separate PostgreSQL database from Vikunja's own database.

The relay deliberately does **not** verify an HMAC, as requested. Do not attach
`vikunjbot-event-relay` to a public network, publish its port, or add a Traefik route.

> **Security:** `ALLOWNONROUTABLEIPS` broadens Vikunja's outbound access. In a
> multi-user instance, prefer a Mole proxy with an ACL limited to the relay.

Create `./vikunjbot/.env` from `./vikunjbot/.env.example` and fill in the required
values. The root `compose.yaml` loads this file only for the bot's PostgreSQL,
migration, relay, and bot services, so these settings do not need to be in the root
Compose project's `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=...
TOKEN_ENCRYPTION_KEY=...
POSTGRES_PASSWORD=...
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
submitted token once and stores only a Fernet-encrypted form in its dedicated
PostgreSQL database.

In the destination private chat or group, send `/webhook <project-id> [kanban-view-ids]`.
It gives a target URL to paste into that Vikunja project's webhook. For example,
`/webhook 12 4,9` tracks Kanban views 4 and 9. `/install_webhook <project-id>
[kanban-view-ids]` creates the same project webhook via the connected user's API
token. Use `/views <project-id>` to list the project's Kanban view IDs.

For a regular group, add the bot as an administrator so it can post and edit task
messages. For a **channel with a linked discussion**, add the bot as a channel
administrator with permission to post, and as an administrator in the linked discussion
group too.
Then, in a private chat with the bot, forward any post from that channel and reply to
it with `/install_channel_webhook <project-id> [kanban-view-ids]`.

The bot verifies that the requester is a channel administrator, that the channel
actually has a linked discussion, and that the bot can post in both required places.
It then publishes task messages in the channel itself. Telegram's automatic forward
creates the corresponding thread in the linked discussion, and replies in that thread
are treated as replies to the channel task message — not as a separate group route.

## Hook identifiers and access boundary

The final component of a webhook URL is an opaque, canonical UUID, for example:

```text
http://vikunjbot-event-relay:8080/events/8b3f07eb-2ec0-4c5c-9bc5-b50f41239705
```

The UUID is only a lookup key. The database holds the hook configuration: project,
destination chat, optional linked discussion, the Telegram users allowed to act, its
event TTL, and the Kanban views selected for that destination. Unknown or inactive
identifiers receive `404`; payloads are not accepted for a guessed route.

The relay deliberately does not expose a public port and does not verify an HMAC, as
requested. Keep it exclusively on an internal Docker network. The UUID is defense in
depth, not a replacement for network isolation.

For a channel route, the database records both the channel post and its verified linked
discussion ID. A reply can trigger a Vikunja action only when it is under Telegram's
automatic forward of that stored channel post in that exact discussion, and only from
the Telegram account granted access for that hook. A manually forwarded post in another
group does not pass this check.

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

Kanban bucket names belong to a specific view. When a hook selects Kanban views, the
bot obtains the task's current `expand=buckets` state from Vikunja and renders a
separate bucket line for every selected view. This avoids borrowing the bucket title
from whichever view happened to send the webhook. Create a dedicated Vikunja account
named `vikunjbot`, grant it read access to the relevant projects, issue an API token,
and set `VIKUNJBOT_SERVICE_TOKEN`. It is used only for that read-only enrichment and
never for a user-requested write.

Without selected views, the bot retains the webhook's single generic bucket when it
can identify it unambiguously. Without a service token, configuring selected views is
rejected, rather than silently showing stale or ambiguous columns.

## Reliability model

PostgreSQL is the source of truth for hooks, encrypted token bindings, accepted events,
and Telegram-message mappings. A relay request is acknowledged only after its event
row is committed. Identical raw payloads to the same hook are safely deduplicated.
Workers claim queue rows using PostgreSQL row locks with `SKIP LOCKED`, retain a
persisted lease, and use an unbounded, capped exponential backoff, so a restart or
Telegram/Vikunja outage does not lose accepted events.

Delivery to Telegram is inherently at-least-once: no Telegram send API offers an
idempotency key, so a process crash after Telegram accepted a new message but before
PostgreSQL recorded its `message_id` can cause one duplicate. Subsequent updates
converge on the saved message mapping.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
make test
```

`make test` builds the `test` Docker target and launches a disposable PostgreSQL test
database. The test setup refuses protected database names and recreates the test schema
before every test; setting `VIKUNJBOT_TEST_DROP_DATABASE=1` (the Compose default) also
drops the entire test database afterwards. The suite runs without Telegram or a
Vikunja instance.
