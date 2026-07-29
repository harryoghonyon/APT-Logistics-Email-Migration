"""
migrate_all.py
Module 3 (FULL MODE): Migrates ALL folders for ALL accounts automatically.

Loops through every account in .env, and for each one, migrates every
folder in FOLDERS_TO_MIGRATE. Fully resumable -- if interrupted (network
drop, circuit breaker trip, you stopping it), just run it again and it
will pick up exactly where it left off, skipping everything already done.

Safety features carried over from testing:
  - Per-email retry with reconnect on connection errors
  - Circuit breaker: stops cleanly if the server seems to be blocking us
  - State database: nothing is ever migrated twice
"""

import imaplib
import ssl
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# --- Folders to migrate for EVERY account (source name -> sink name) ---
# Based on confirmed folder structure from Module 3 (all 9 accounts match).
FOLDERS_TO_MIGRATE = [
    ("INBOX", "INBOX"),
    ("INBOX.Archive", "Archive"),
    ("INBOX.Sent", "Sent"),
    ("INBOX.Drafts", "Drafts"),
]

# --- Which accounts to process ---
# Default: ALL accounts (1-9), run one after another.
# Override from the command line to target specific account(s), e.g.:
#   python3.13 migrate_all.py 4        -> just account 4
#   python3.13 migrate_all.py 4 5 6    -> accounts 4, 5, and 6
if len(sys.argv) > 1:
    ACCOUNT_NUMBERS = [int(arg) for arg in sys.argv[1:]]
else:
    ACCOUNT_NUMBERS = list(range(1, 10))  # 1 through 9

STATE_DB_PATH = "migration_state.db"

# Resilience settings
MAX_RETRIES_PER_EMAIL = 3
RETRY_DELAY_SECONDS = 15
PAUSE_BETWEEN_EMAILS = 1.5
PAUSE_BETWEEN_FOLDERS = 5
PAUSE_BETWEEN_ACCOUNTS = 20
MAX_CONSECUTIVE_CONNECTION_FAILURES = 15


# ---------- .env loader ----------

def load_env_file(path=".env"):
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


# ---------- State database (the "checklist") ----------

class StateManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS migrated_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_email TEXT NOT NULL,
                    source_folder TEXT NOT NULL,
                    source_uid   TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    last_error   TEXT,
                    updated_at   TEXT NOT NULL,
                    UNIQUE(account_email, source_folder, source_uid)
                );
            """)

    def is_migrated(self, account_email, source_folder, source_uid) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT status FROM migrated_messages
                   WHERE account_email=? AND source_folder=? AND source_uid=?""",
                (account_email, source_folder, source_uid),
            ).fetchone()
        return row is not None and row[0] == "success"

    def mark_success(self, account_email, source_folder, source_uid):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO migrated_messages
                       (account_email, source_folder, source_uid, status, updated_at)
                   VALUES (?, ?, ?, 'success', ?)
                   ON CONFLICT(account_email, source_folder, source_uid)
                   DO UPDATE SET status='success', last_error=NULL, updated_at=excluded.updated_at""",
                (account_email, source_folder, source_uid, self._now()),
            )

    def mark_failed(self, account_email, source_folder, source_uid, error_message):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO migrated_messages
                       (account_email, source_folder, source_uid, status, last_error, updated_at)
                   VALUES (?, ?, ?, 'failed', ?, ?)
                   ON CONFLICT(account_email, source_folder, source_uid)
                   DO UPDATE SET status='failed', last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (account_email, source_folder, source_uid, str(error_message)[:1000], self._now()),
            )

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()


# ---------- IMAP helpers ----------

def connect(host, port, username, password):
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host=host, port=port, ssl_context=context, timeout=180)
    conn.login(username, password)
    return conn


def safe_logout(conn):
    try:
        conn.logout()
    except Exception:
        pass


def get_source_uids(conn, folder):
    status, _ = conn.select(f'"{folder}"', readonly=True)
    if status != "OK":
        raise RuntimeError(f"Could not select source folder '{folder}'")

    status, data = conn.uid("search", None, "ALL")
    if status != "OK":
        raise RuntimeError(f"Could not search folder '{folder}'")

    all_uids = data[0].split()
    return [uid.decode() for uid in all_uids]


def fetch_message(conn, uid):
    status, data = conn.uid("fetch", uid, "(RFC822 INTERNALDATE)")
    if status != "OK" or not data or data[0] is None:
        raise RuntimeError(f"Could not fetch UID {uid}")

    raw_email = None
    internal_date_raw = None

    for part in data:
        if isinstance(part, tuple):
            header_bytes = part[0]
            raw_email = part[1]
            if b"INTERNALDATE" in header_bytes:
                text = header_bytes.decode(errors="replace")
                start = text.find('"') + 1
                end = text.find('"', start)
                internal_date_raw = text[start:end]

    return raw_email, internal_date_raw


def parse_internaldate(internal_date_raw):
    if not internal_date_raw:
        return None
    try:
        return parsedate_to_datetime(internal_date_raw)
    except Exception:
        return None


def append_message(conn, folder, raw_email, date_time):
    imap_date = None
    if date_time is not None:
        imap_date = imaplib.Time2Internaldate(date_time.timestamp())

    status, response = conn.append(f'"{folder}"', None, imap_date, raw_email)
    if status != "OK":
        raise RuntimeError(f"APPEND failed: {response}")


# ---------- Folder migration ----------

def migrate_folder(email, source_pass, sink_pass, source_host, source_port,
                    sink_host, sink_port, source_folder, sink_folder, state):
    """Migrates one folder for one account. Returns (migrated, skipped, failed, stopped_early)."""
    print(f"\n  --- Folder: {source_folder} -> {sink_folder} ---")

    source_conn = connect(source_host, source_port, email, source_pass)
    sink_conn = connect(sink_host, sink_port, email, sink_pass)
    sink_conn.select(f'"{sink_folder}"')

    uids = get_source_uids(source_conn, source_folder)
    print(f"  Found {len(uids)} email(s) in this folder.")

    migrated_count = 0
    skipped_count = 0
    failed_count = 0
    consecutive_connection_failures = 0
    stopped_early = False

    for idx, uid in enumerate(uids, start=1):
        if state.is_migrated(email, source_folder, uid):
            skipped_count += 1
            continue

        if consecutive_connection_failures >= MAX_CONSECUTIVE_CONNECTION_FAILURES:
            print(f"  🛑 STOPPING this folder: {consecutive_connection_failures} "
                  f"connection failures in a row.")
            stopped_early = True
            break

        success = False
        last_error = None
        had_connection_error = False

        for attempt in range(1, MAX_RETRIES_PER_EMAIL + 1):
            try:
                raw_email, internal_date_raw = fetch_message(source_conn, uid)
                date_time = parse_internaldate(internal_date_raw)
                append_message(sink_conn, sink_folder, raw_email, date_time)

                state.mark_success(email, source_folder, uid)
                migrated_count += 1
                success = True
                consecutive_connection_failures = 0
                break

            except (imaplib.IMAP4.abort, imaplib.IMAP4.error,
                    ssl.SSLError, OSError, TimeoutError) as e:
                last_error = e
                had_connection_error = True
                safe_logout(source_conn)
                safe_logout(sink_conn)

                if attempt < MAX_RETRIES_PER_EMAIL:
                    time.sleep(RETRY_DELAY_SECONDS)
                    try:
                        source_conn = connect(source_host, source_port, email, source_pass)
                        source_conn.select(f'"{source_folder}"', readonly=True)
                        sink_conn = connect(sink_host, sink_port, email, sink_pass)
                        sink_conn.select(f'"{sink_folder}"')
                    except Exception as reconnect_error:
                        last_error = reconnect_error

            except Exception as e:
                last_error = e
                break

        if not success:
            state.mark_failed(email, source_folder, uid, str(last_error))
            failed_count += 1
            print(f"  ❌ UID {uid}: FAILED -- {last_error}")
            if had_connection_error:
                consecutive_connection_failures += 1

        # Progress update every 25 emails
        if idx % 25 == 0:
            print(f"  ... progress: {idx}/{len(uids)} "
                  f"(migrated: {migrated_count}, skipped: {skipped_count}, failed: {failed_count})")

        time.sleep(PAUSE_BETWEEN_EMAILS)

    safe_logout(source_conn)
    safe_logout(sink_conn)

    print(f"  Folder done. Migrated: {migrated_count}, Skipped: {skipped_count}, "
          f"Failed: {failed_count}")
    return migrated_count, skipped_count, failed_count, stopped_early


# ---------- Main ----------

def main():
    env = load_env_file(".env")
    state = StateManager(STATE_DB_PATH)

    totals = {"migrated": 0, "skipped": 0, "failed": 0}

    for account_number in ACCOUNT_NUMBERS:
        email_key = f"ACCOUNT{account_number}_EMAIL"
        if email_key not in env:
            continue

        email = env[email_key]
        source_pass = env[f"ACCOUNT{account_number}_SOURCE_PASS"]
        sink_pass = env[f"ACCOUNT{account_number}_SINK_PASS"]
        source_host, source_port = env["SOURCE_IMAP_HOST"], int(env["SOURCE_IMAP_PORT"])
        sink_host, sink_port = env["SINK_IMAP_HOST"], int(env["SINK_IMAP_PORT"])

        print(f"\n{'=' * 70}")
        print(f"ACCOUNT {account_number}: {email}")
        print(f"{'=' * 70}")

        account_stopped_early = False

        for source_folder, sink_folder in FOLDERS_TO_MIGRATE:
            try:
                migrated, skipped, failed, stopped_early = migrate_folder(
                    email, source_pass, sink_pass,
                    source_host, source_port, sink_host, sink_port,
                    source_folder, sink_folder, state,
                )
                totals["migrated"] += migrated
                totals["skipped"] += skipped
                totals["failed"] += failed

                if stopped_early:
                    account_stopped_early = True
                    break

            except Exception as e:
                print(f"  ❌ ERROR processing folder {source_folder}: {e}")
                account_stopped_early = True
                break

            time.sleep(PAUSE_BETWEEN_FOLDERS)

        if account_stopped_early:
            print(f"\n🛑 Stopped early during account {account_number}. "
                  f"Re-run this script later to continue -- it will resume "
                  f"exactly where it left off.")
            break

        time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    print(f"\n{'=' * 70}")
    print("OVERALL SUMMARY (this run)")
    print(f"{'=' * 70}")
    print(f"Migrated: {totals['migrated']}")
    print(f"Skipped (already done): {totals['skipped']}")
    print(f"Failed: {totals['failed']}")
    print("\nIf anything failed or stopped early, just re-run this script --")
    print("it will skip everything already done and continue from there.")


if __name__ == "__main__":
    main()