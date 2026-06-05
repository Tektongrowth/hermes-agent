# Telegram Recent Chat Context Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Give Hermes a safe, first-class way to retrieve recent Telegram group/DM context from messages the gateway has already observed, so Nick can ask “what were we just talking about in this chat?” without relying on browser scraping or full Telegram account access.

**Architecture:** Add a small platform message ledger in the Hermes session database, write inbound gateway messages into it before agent dispatch, then expose a read-only `chat_context` tool that can fetch recent messages for the current chat/session source. Keep this separate from normal LLM transcripts because most group messages should be remembered as chat context, not as assistant conversation turns.

**Tech Stack:** Python, SQLite via `hermes_state.py`, gateway adapters under `gateway/`, existing tool registry under `tools/registry.py`, pytest.

---

## Design Decisions

- **Do not scrape Telegram Web as the core solution.** It is fragile, login-gated, and over-broad.
- **Do store only messages the bot/gateway receives.** Telegram Bot API cannot retrieve arbitrary pre-bot history. The tool should clearly say “captured by Hermes,” not “all Telegram history.”
- **Make it multi-platform-ready.** Table names and APIs should say `platform_messages` or `chat_messages`, not `telegram_messages`, because Discord/Slack/Yuanbao will want the same capability.
- **Default read-only.** This feature never sends, deletes, edits, or marks messages.
- **Current-chat safe default.** If called from Telegram, the tool should default to the current chat/thread from the active session source. Cross-chat reads should require explicit `platform` + `chat_id`, and later can be permission-gated.
- **PII/privacy aware.** Honor existing `privacy.redact_pii` behavior for prompt injection/output. Store raw IDs in the local DB only, as Hermes already does for routing/session state.
- **Retention configurable.** Avoid unbounded growth. Start with count/time pruning, default `gateway.chat_history.retention_days: 30` and `max_messages_per_chat: 2000`.

---

## Acceptance Criteria

1. Incoming Telegram messages in groups and DMs are stored in a local read-only history table with platform, chat, thread, sender, message id, timestamp, text, type, and reply metadata.
2. Messages that do not trigger the agent because of mention-gating are still stored when the gateway receives them.
3. A new tool, tentatively `chat_context`, can return the last N captured messages for the current chat.
4. Tool output is compact, chronological, sender-attributed, and Telegram-friendly.
5. The tool refuses or errors clearly when asked for uncaptured history.
6. Tests cover storage, retrieval, current-chat scoping, retention pruning, and Telegram adapter integration.
7. Existing session transcript search behavior is unchanged.
8. Existing dirty repo changes are not touched.

---

## Current Code Touchpoints

- `hermes_state.py`
  - `SessionDB` owns SQLite schema and migrations.
  - Existing `messages` table stores agent transcripts, not complete chat history.
  - `append_message(..., platform_message_id=..., observed=...)` exists but is session-bound and not enough for non-triggering group chatter.

- `gateway/session.py`
  - `SessionSource` contains `platform`, `chat_id`, `chat_name`, `chat_type`, `user_id`, `user_name`, `thread_id`, `message_id`.

- `gateway/platforms/base.py`
  - `MessageEvent` already has `source`, `message_id`, `reply_to_message_id`, `reply_to_text`, `timestamp`, `channel_context`.

- `gateway/platforms/telegram.py`
  - `_message_to_event` returns `MessageEvent` around line 5770.
  - This is the right adapter-level shape, but the storage call should live in gateway core if possible so other platforms get it later.

- `tools/session_search_tool.py`
  - Good reference for a read-only retrieval tool backed by `SessionDB`.

- `toolsets.py`
  - Add `chat_context` to an appropriate existing toolset or create a narrow `chat_context` toolset. Prefer existing `session_search`/gateway-safe tool availability if this is meant to be always available on messaging platforms.

---

## Task 1: Add `platform_messages` schema migration

**Objective:** Create a normalized SQLite table for captured platform chat messages.

**Files:**
- Modify: `hermes_state.py`
- Test: `tests/test_platform_message_history.py` or `tests/gateway/test_platform_message_history.py`

**Schema sketch:**

```sql
CREATE TABLE IF NOT EXISTS platform_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    thread_id TEXT,
    chat_type TEXT,
    chat_name TEXT,
    user_id TEXT,
    user_name TEXT,
    is_bot INTEGER DEFAULT 0,
    message_id TEXT NOT NULL,
    update_id TEXT,
    message_type TEXT,
    text TEXT,
    reply_to_message_id TEXT,
    reply_to_text TEXT,
    raw_summary TEXT,
    timestamp REAL NOT NULL,
    captured_at REAL NOT NULL,
    UNIQUE(platform, chat_id, COALESCE(thread_id, ''), message_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_messages_scope_time
ON platform_messages(platform, chat_id, thread_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_platform_messages_sender_time
ON platform_messages(platform, user_id, timestamp);
```

**Implementation notes:**

- SQLite does not allow expressions like `COALESCE(...)` inside a `UNIQUE(...)` table constraint in all target versions. If needed, use a unique index instead:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_messages_unique
ON platform_messages(platform, chat_id, IFNULL(thread_id, ''), message_id);
```

- Store `timestamp` as epoch seconds. If `MessageEvent.timestamp` is a datetime, convert safely.
- `raw_summary` should be a compact JSON string with only safe metadata, not full raw Telegram update dumps.

**Verification:**

Run:

```bash
python -m pytest tests/test_platform_message_history.py -q
```

Expected: schema creation test passes.

---

## Task 2: Add `record_platform_message()` to `SessionDB`

**Objective:** Give gateway code one safe method to insert/update captured messages idempotently.

**Files:**
- Modify: `hermes_state.py`
- Test: `tests/test_platform_message_history.py`

**Method signature sketch:**

```python
def record_platform_message(
    self,
    *,
    platform: str,
    chat_id: str,
    message_id: str,
    thread_id: str | None = None,
    chat_type: str | None = None,
    chat_name: str | None = None,
    user_id: str | None = None,
    user_name: str | None = None,
    is_bot: bool = False,
    update_id: str | int | None = None,
    message_type: str | None = None,
    text: str | None = None,
    reply_to_message_id: str | None = None,
    reply_to_text: str | None = None,
    raw_summary: dict | str | None = None,
    timestamp: float | datetime | None = None,
) -> int:
    ...
```

**Behavior:**

- No-op or raise `ValueError` when `platform`, `chat_id`, or `message_id` is missing.
- `INSERT OR IGNORE` for duplicate platform messages.
- Optional `INSERT ... ON CONFLICT DO UPDATE` only if a later event adds better text or sender metadata. Start with `INSERT OR IGNORE` to avoid accidental mutation.
- Return the row id.

**Verification:**

Test duplicate insert does not create a second row.

---

## Task 3: Add retrieval methods to `SessionDB`

**Objective:** Fetch recent chat messages by platform/chat/thread with stable ordering.

**Files:**
- Modify: `hermes_state.py`
- Test: `tests/test_platform_message_history.py`

**Method signature sketch:**

```python
def get_recent_platform_messages(
    self,
    *,
    platform: str,
    chat_id: str,
    thread_id: str | None = None,
    limit: int = 50,
    include_bots: bool = True,
    before_message_id: str | None = None,
) -> list[dict[str, Any]]:
    ...
```

**Behavior:**

- Clamp `limit` to `[1, 200]`.
- Return chronological order oldest to newest, even if SQL fetches descending first.
- If `thread_id` is provided, scope to that thread/topic.
- If `thread_id` is omitted, return all chat messages for the chat. Later we can add `scope='chat'|'thread'`.
- Include sender display as `user_name or user_id or 'unknown'`.

**Verification:**

- Insert messages A, B, C.
- Fetch `limit=2`.
- Assert result is B, C in chronological order.

---

## Task 4: Add retention pruning

**Objective:** Keep the local history bounded.

**Files:**
- Modify: `hermes_state.py`
- Potentially modify: gateway startup code where DB maintenance already runs.
- Test: `tests/test_platform_message_history.py`

**Method signature sketch:**

```python
def prune_platform_messages(
    self,
    *,
    retention_days: int | None = 30,
    max_messages_per_chat: int | None = 2000,
) -> dict[str, int]:
    ...
```

**Behavior:**

- Delete messages older than `retention_days` if set.
- Keep only newest `max_messages_per_chat` per `(platform, chat_id, thread_id)` if set.
- Return counts: `{"deleted_by_age": n, "deleted_by_count": m}`.

**Verification:**

Create 5 messages with `max_messages_per_chat=3`, prune, assert only newest 3 remain.

---

## Task 5: Store incoming events in gateway core

**Objective:** Record every inbound message event as soon as it has been normalized, including messages that may not trigger an agent response.

**Files:**
- Modify: `gateway/run.py` or the central event handling path that all adapters use.
- Test: `tests/gateway/test_platform_message_history.py`

**Implementation notes:**

- Find the central method that receives `MessageEvent` from adapters before mention/authorization/session dispatch.
- Add helper:

```python
def _record_incoming_platform_message(self, event: MessageEvent) -> None:
    source = event.source
    if not source:
        return
    self.session_db.record_platform_message(
        platform=getattr(source.platform, "value", str(source.platform)),
        chat_id=str(source.chat_id),
        thread_id=str(source.thread_id) if source.thread_id else None,
        chat_type=source.chat_type,
        chat_name=source.chat_name,
        user_id=str(source.user_id) if source.user_id else None,
        user_name=source.user_name,
        is_bot=bool(getattr(source, "is_bot", False)),
        message_id=str(event.message_id or source.message_id or ""),
        update_id=str(event.platform_update_id) if event.platform_update_id is not None else None,
        message_type=getattr(event.message_type, "value", str(event.message_type)),
        text=event.text or None,
        reply_to_message_id=event.reply_to_message_id,
        reply_to_text=event.reply_to_text,
        timestamp=event.timestamp,
    )
```

- Catch/log exceptions. Chat history capture should never break message handling.
- Do not store outbound bot responses in this task unless they already flow through inbound paths. Outbound capture can be a later improvement.

**Verification:**

Mock a `MessageEvent`, run the gateway handler path, assert one platform message row exists even if the agent is not invoked.

---

## Task 6: Add focused Telegram adapter test

**Objective:** Prove Telegram normalized events carry enough data for the history ledger.

**Files:**
- Test: `tests/gateway/test_telegram_platform_message_history.py`

**Test cases:**

- Telegram group text message:
  - `platform='telegram'`
  - group `chat_id`
  - group `chat_name`
  - sender `user_id` and `user_name`
  - `message_id`
  - `timestamp`

- Telegram forum/topic message:
  - includes `thread_id` / `message_thread_id`

- Reply message:
  - includes `reply_to_message_id`
  - includes quoted or fallback `reply_to_text`

**Verification:**

Run:

```bash
python -m pytest tests/gateway/test_telegram_platform_message_history.py -q
```

Expected: all pass.

---

## Task 7: Create `tools/chat_context_tool.py`

**Objective:** Expose a read-only tool for the agent to fetch recent captured chat history.

**Files:**
- Create: `tools/chat_context_tool.py`
- Modify: `toolsets.py`
- Test: `tests/tools/test_chat_context_tool.py`

**Tool schema sketch:**

```python
{
    "name": "chat_context",
    "description": "Fetch recent messages captured by the messaging gateway for the current chat or an explicitly specified chat. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            "platform": {"type": "string", "description": "Optional explicit platform, e.g. telegram"},
            "chat_id": {"type": "string", "description": "Optional explicit chat ID. Defaults to current chat when available."},
            "thread_id": {"type": "string", "description": "Optional thread/topic ID. Defaults to current thread when available."},
            "include_bots": {"type": "boolean", "default": true}
        }
    }
}
```

**Important implementation detail:**

The tool needs access to current session source. If the existing tool executor does not pass source metadata into tools, there are two options:

1. Use current `task_id`/session metadata lookup if the active session stores origin source.
2. Extend tool execution context to pass `platform`, `chat_id`, and `thread_id` to handlers, similar to how `task_id` is passed.

Prefer the smallest existing-context path. Do not add globals unless there is already a gateway context mechanism.

**Output format:**

```json
{
  "success": true,
  "scope": {"platform": "telegram", "chat_id": "-5210005075", "thread_id": null},
  "captured_only": true,
  "messages": [
    {"time": "2026-06-04 13:20:10", "sender": "Nick Conley", "text": "Let’s test it", "message_id": "10530"}
  ]
}
```

**Verification:**

Seed DB with messages, call handler directly, assert compact JSON output.

---

## Task 8: Add current-chat default scoping

**Objective:** Make `chat_context` useful from Telegram without Nick needing chat IDs.

**Files:**
- Modify: `agent/tool_executor.py` or equivalent dispatch layer if needed.
- Modify: `tools/chat_context_tool.py`
- Test: `tests/tools/test_chat_context_tool.py`

**Behavior:**

- If `platform` and `chat_id` are absent, resolve from the active gateway session source.
- If no current chat source exists, return:

```json
{"success": false, "error": "No current messaging chat is available. Pass platform and chat_id explicitly."}
```

- If only one of `platform` or `chat_id` is provided, return a validation error.

**Verification:**

Simulate tool context with Telegram source and assert it fetches that chat.

---

## Task 9: Add prompt/tool guidance

**Objective:** Teach the agent to use `chat_context` for “what did we just say here?” questions before asking Nick to repeat himself.

**Files:**
- Modify: `agent/system_prompt.py` or the tool description only, depending on current prompt design.
- Test: existing prompt/tool schema tests if present.

**Guidance text:**

- Use `chat_context` when the user references recent messages in the same messaging chat and the context is not already visible.
- Use `session_search` for past Hermes conversations across sessions.
- Use gateway logs only for routing/debugging, not conversational reconstruction.

**Verification:**

Run relevant prompt/tool schema tests.

---

## Task 10: Add config defaults

**Objective:** Make retention and enablement explicit.

**Files:**
- Modify: `hermes_cli/config.py` or wherever `DEFAULT_CONFIG` is defined.
- Modify: docs if config docs are generated/maintained.
- Test: config default tests if present.

**Config sketch:**

```yaml
gateway:
  chat_history:
    enabled: true
    retention_days: 30
    max_messages_per_chat: 2000
    capture_text: true
    capture_media_metadata: true
```

**Behavior:**

- If disabled, gateway does not write `platform_messages`.
- Tool should return a clear disabled message if no table/data exists.

**Verification:**

Set enabled false in a config fixture, assert no row is written.

---

## Task 11: Add docs

**Objective:** Document what the feature can and cannot see.

**Files:**
- Modify: `website/docs/user-guide/messaging/telegram.md` if present.
- Or create/update nearest messaging/gateway docs.

**Docs must say:**

- Hermes can retrieve recent messages captured by the gateway.
- It cannot fetch arbitrary Telegram history from before the bot saw it.
- Groups may require privacy mode settings and mention behavior to receive non-command messages.
- History is local to the Hermes profile.
- Retention is configurable.

**Verification:**

Run docs lint/build if this repo has a docs command, or at minimum run markdown checks if available.

---

## Task 12: End-to-end manual test in Nick’s Design group

**Objective:** Prove the real workflow works in Telegram.

**Steps:**

1. Restart gateway after deploying code.
2. In `🫟Design🫟`, send three messages that do not all mention the bot.
3. Ask: “what were the last few messages here?”
4. Confirm Hermes calls `chat_context` and summarizes the captured messages.
5. Ask for explicit count: “show last 10 raw captured messages.”
6. Confirm sender names, order, and message text are correct.

**Expected:**

Hermes answers from `chat_context`, not from guessed session memory.

---

## Testing Bundle

Run after implementation:

```bash
python -m pytest tests/test_platform_message_history.py -q
python -m pytest tests/gateway/test_platform_message_history.py -q
python -m pytest tests/gateway/test_telegram_platform_message_history.py -q
python -m pytest tests/tools/test_chat_context_tool.py -q
```

Then run a focused broader suite:

```bash
python -m pytest tests/gateway tests/tools/test_session_search_tool.py -q
```

If the repo’s normal wrapper is required:

```bash
scripts/run_tests.sh tests/gateway tests/tools/test_chat_context_tool.py
```

---

## Rollout Plan

1. Implement behind `gateway.chat_history.enabled` defaulting to true.
2. Deploy to Nick’s default profile only.
3. Restart gateway.
4. Validate in `🫟Design🫟` and one Discord channel if the gateway core capture is platform-neutral.
5. Watch `~/.hermes/logs/gateway.log` for insert errors.
6. After 48 hours, tune retention if DB growth is too high.

---

## Risks and Guardrails

- **Telegram Bot API limitation:** no historical backfill before bot receives messages. Be explicit.
- **Privacy mode:** if bot privacy is enabled in groups, Telegram may not deliver all ordinary messages. This feature cannot store what Telegram never sends to the bot.
- **Prompt injection:** chat history is untrusted user content. Tool descriptions and prompt guidance should tell the agent to treat fetched messages as context, not instructions.
- **DB growth:** retention pruning is mandatory.
- **Cross-chat privacy:** default to current chat. Explicit cross-chat reads should be auditable and possibly restricted later.
- **Dirty repo:** current checkout has unrelated modifications and many untracked image files. Implementation should happen on a clean branch/worktree or preserve a dirty-state packet before edits.

---

## Future Extensions

- Capture outbound bot messages into `platform_messages` so history shows both sides.
- Add `search_chat_context(query=...)` over captured platform messages using FTS5.
- Add “conversation packet” output: last N messages plus thread facts plus open questions.
- Add per-chat `/context on|off` controls.
- Add dashboard view in Watchtower for recent captured chat context.
- Support explicit import/backfill from exported Telegram JSON for one-time historical migrations, only when Nick provides the export.
