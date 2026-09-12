### Fixed — acknowledge only a real message addressed to the acking agent (FR-28) (#628)

`POST /coordination/messages/{message_id}/acknowledge` emitted an `agent.message.acknowledged`
event **unconditionally** — `acknowledge_message` never checked the id. Two defects:

- **Phantom ack:** any id (including one that is not a message) answered `200 acknowledged: true`
  and recorded an acknowledgement of a message that never existed.
- **Cross-agent inbox suppression:** `get_inbox` builds its suppression set from every
  acknowledgement for the user, so one agent could acknowledge another agent's message by id and
  make it vanish from that agent's inbox.

`acknowledge_message` now **resolves** the message (by id, scoped to the user, and only
`agent.message.*` types), **authorises** the caller as its `recipient_agent_id` (case-insensitive,
matching `get_inbox`), and only then emits. It raises `MessageNotFoundError` (absent, malformed,
or not a message → **404**) and `MessageNotOwnedError` (addressed to another agent → **403**); the
route maps both. The `message_id` path param is now `UUIDPath`, so a malformed id is a **422** at
the boundary rather than reaching the handler. Acknowledging one's own message is unchanged (200).

The cross-agent suppression is closed at the source: an acknowledgement can now only be created by
the message's own recipient, so nothing another agent does can suppress your inbox.
