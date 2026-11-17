-- 0402: Conversations born on external channels (Google Chat first) get
-- registered into calliope_sessions by a mirror worker, so the Floor, search,
-- briefs, and the ledger see them like any other conversation. `channel`
-- records where a conversation actually lives: the web serving loop refuses
-- to write turns into a channel-owned thread (the transcript is a mirror,
-- not a second mouth), and the UI offers "continue it there" instead.
ALTER TABLE rvbbit.calliope_sessions
    ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'web';
COMMENT ON COLUMN rvbbit.calliope_sessions.channel IS
    'Where this conversation lives: web (Calliope-native) or an external channel such as google_chat. Channel sessions are mirrored transcripts — readable everywhere, writable only at their home channel.';
CREATE INDEX IF NOT EXISTS calliope_sessions_channel_idx
    ON rvbbit.calliope_sessions (channel) WHERE channel <> 'web';
