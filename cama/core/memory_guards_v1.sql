-- CAMA database guards. Tested on a copy first (db_guards.py). Not applied to the live store.
-- Pattern source: Professor Chu's Day 5 triggers (activityLog on status change, NEW/OLD, IF ... END IF)
-- and Day 6 transactions (START TRANSACTION ... COMMIT), translated to SQLite.

CREATE TABLE IF NOT EXISTS memory_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   INTEGER NOT NULL,
    changed_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    field       TEXT    NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_log_memory ON memory_log (memory_id, id);

-- 1. Status changes are recorded (confirm, reject, expire, quarantine, release).
CREATE TRIGGER IF NOT EXISTS trg_memories_status_log
AFTER UPDATE OF status ON memories
FOR EACH ROW WHEN NEW.status IS NOT OLD.status
BEGIN
    INSERT INTO memory_log (memory_id, field, old_value, new_value)
    VALUES (NEW.id, 'status', OLD.status, NEW.status);
END;

-- 2. Sensitivity changes are recorded.
CREATE TRIGGER IF NOT EXISTS trg_memories_consent_log
AFTER UPDATE OF consent_level ON memories
FOR EACH ROW WHEN NEW.consent_level IS NOT OLD.consent_level
BEGIN
    INSERT INTO memory_log (memory_id, field, old_value, new_value)
    VALUES (NEW.id, 'consent_level', OLD.consent_level, NEW.consent_level);
END;

-- 3. A self-account's text is append-only. A changed reading is a new row with revision_of set.
CREATE TRIGGER IF NOT EXISTS trg_self_account_text_immutable
BEFORE UPDATE OF raw_text ON memories
FOR EACH ROW WHEN OLD.memory_type = 'self_account' AND NEW.raw_text IS NOT OLD.raw_text
BEGIN
    SELECT RAISE(ABORT, 'self_account raw_text is append-only; store a revision instead');
END;

-- 4. User-authored rows keep their provenance. Nothing relabels a teaching.
CREATE TRIGGER IF NOT EXISTS trg_user_provenance_guard
BEFORE UPDATE OF proposed_by, source_type ON memories
FOR EACH ROW WHEN OLD.proposed_by = 'user'
             AND (NEW.proposed_by IS NOT 'user' OR NEW.source_type IS NOT OLD.source_type)
BEGIN
    SELECT RAISE(ABORT, 'user-authored memory provenance cannot be relabeled');
END;

-- 5. Nothing becomes user-authored by an update. Only the user authors teachings.
CREATE TRIGGER IF NOT EXISTS trg_no_promotion_to_user
BEFORE UPDATE OF proposed_by ON memories
FOR EACH ROW WHEN OLD.proposed_by IS NOT 'user' AND NEW.proposed_by = 'user'
BEGIN
    SELECT RAISE(ABORT, 'only the user authors teachings; cannot relabel a row as user-authored');
END;

-- 6. Deleting a self-account leaves a trace: id and type only, never the text.
CREATE TRIGGER IF NOT EXISTS trg_self_account_delete_log
AFTER DELETE ON memories
FOR EACH ROW WHEN OLD.memory_type = 'self_account'
BEGIN
    INSERT INTO memory_log (memory_id, field, old_value, new_value, note)
    VALUES (OLD.id, 'deleted', 'self_account', NULL, 'row removed; text not retained in log');
END;

-- Prevent relabel-then-edit and preserve self-account provenance.
CREATE TRIGGER IF NOT EXISTS trg_self_account_identity
BEFORE UPDATE OF memory_type, proposed_by, source_type ON memories
WHEN OLD.memory_type = 'self_account' AND
 (NEW.memory_type IS NOT OLD.memory_type OR NEW.proposed_by IS NOT OLD.proposed_by
  OR NEW.source_type IS NOT OLD.source_type)
BEGIN SELECT RAISE(ABORT, 'self_account type and provenance are immutable'); END;

-- REPLACE is a delete plus insert, not an UPDATE; reject existing ids first.
CREATE TRIGGER IF NOT EXISTS trg_memories_no_replace
BEFORE INSERT ON memories WHEN EXISTS(SELECT 1 FROM memories WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT, 'existing memory id cannot be replaced; use UPDATE'); END;

CREATE TRIGGER IF NOT EXISTS trg_memories_id_immutable
BEFORE UPDATE OF id ON memories WHEN NEW.id IS NOT OLD.id
BEGIN SELECT RAISE(ABORT, 'memory ids are immutable'); END;

-- Lifecycle metadata may change, but it cannot reattribute an account.
CREATE TRIGGER IF NOT EXISTS trg_self_account_context_author
BEFORE UPDATE OF context ON memories
WHEN OLD.memory_type = 'self_account' AND json_valid(OLD.context)
BEGIN
    SELECT CASE WHEN NOT json_valid(NEW.context)
        THEN RAISE(ABORT, 'self_account context must remain valid JSON') END;
    SELECT CASE WHEN json_extract(NEW.context,'$.author') IS NOT json_extract(OLD.context,'$.author')
        OR json_extract(NEW.context,'$.subject') IS NOT json_extract(OLD.context,'$.subject')
        THEN RAISE(ABORT, 'self_account author and subject are immutable') END;
END;
