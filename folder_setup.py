"""
folder_setup.py
Module 2: Folder Discovery & Replication.

Processes accounts in a controlled RANGE (see ACCOUNT_RANGE_START/END below)
so you can run this in small batches with breaks in between, instead of
hitting all 9 accounts back-to-back -- which can trigger cPanel's
brute-force protection on shared hosting.

No emails are touched in this module -- only folder structure.
"""

import imaplib
import ssl
import os
import re
import time

# --- Control which accounts to process in THIS run ---
# Example: to test just accounts 1-3, set START=1, END=3.
# Next run, set START=4, END=6. Then START=7, END=9.
ACCOUNT_RANGE_START = 7
ACCOUNT_RANGE_END = 9

# Folders we never want to migrate
EXCLUDED_FOLDER_KEYWORDS = ["trash", "junk", "spam"]

# Pause between accounts (seconds) -- increased significantly after
# hitting cPanel's brute-force protection at 5s.
PAUSE_BETWEEN_ACCOUNTS = 45


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


def discover_accounts(env: dict, start: int, end: int):
    """Scans env for ACCOUNT{start}..ACCOUNT{end} entries."""
    accounts = []
    for i in range(start, end + 1):
        email_key = f"ACCOUNT{i}_EMAIL"
        if email_key not in env:
            continue
        accounts.append({
            "number": i,
            "email": env[email_key],
            "source_pass": env.get(f"ACCOUNT{i}_SOURCE_PASS", ""),
            "sink_pass": env.get(f"ACCOUNT{i}_SINK_PASS", ""),
        })
    return accounts


def connect(host, port, username, password):
    context = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host=host, port=port, ssl_context=context, timeout=15)
    conn.login(username, password)
    return conn


def parse_list_response(raw_line: bytes):
    text = raw_line.decode("utf-8", errors="replace")
    match = re.match(r'\(.*?\)\s+"(.*?)"\s+(.*)', text)
    if not match:
        pieces = text.split()
        return ".", pieces[-1] if pieces else ""
    delimiter = match.group(1)
    folder_name = match.group(2).strip()
    if folder_name.startswith('"') and folder_name.endswith('"'):
        folder_name = folder_name[1:-1]
    return delimiter, folder_name


def get_folders(conn):
    status, raw_folders = conn.list()
    if status != "OK":
        return []
    results = []
    for raw_line in raw_folders:
        delimiter, folder_name = parse_list_response(raw_line)
        results.append((delimiter, folder_name))
    return results


def clean_folder_name(folder_name: str, source_delimiter: str) -> str:
    if folder_name.upper() == "INBOX":
        return "INBOX"
    prefix = "INBOX" + source_delimiter
    if folder_name.upper().startswith(prefix.upper()):
        return folder_name[len(prefix):]
    return folder_name


def is_excluded(folder_name: str) -> bool:
    lowered = folder_name.lower()
    return any(keyword in lowered for keyword in EXCLUDED_FOLDER_KEYWORDS)


def process_account(account: dict, env: dict):
    email = account["email"]
    print(f"\n{'=' * 70}")
    print(f"ACCOUNT {account['number']}: {email}")
    print(f"{'=' * 70}")

    try:
        print("Connecting to SOURCE (cPanel)...")
        source_conn = connect(
            env["SOURCE_IMAP_HOST"], int(env["SOURCE_IMAP_PORT"]),
            email, account["source_pass"],
        )
        source_folders_raw = get_folders(source_conn)
        source_conn.logout()

        print(f"Found {len(source_folders_raw)} folders on source:")
        for delimiter, name in source_folders_raw:
            print(f"   - {name}  (delimiter: '{delimiter}')")

        clean_names = []
        for delimiter, name in source_folders_raw:
            cleaned = clean_folder_name(name, delimiter)
            if is_excluded(cleaned):
                print(f"   Skipping excluded folder: {cleaned}")
                continue
            clean_names.append(cleaned)

        print(f"\nFolders to replicate on sink: {clean_names}")

        print("\nConnecting to SINK (Namecheap)...")
        sink_conn = connect(
            env["SINK_IMAP_HOST"], int(env["SINK_IMAP_PORT"]),
            email, account["sink_pass"],
        )
        sink_folders_raw = get_folders(sink_conn)
        sink_delimiter = sink_folders_raw[0][0] if sink_folders_raw else "/"
        existing_sink_names = {name for _, name in sink_folders_raw}

        print(f"Sink delimiter detected: '{sink_delimiter}'")
        print(f"Sink already has: {sorted(existing_sink_names)}\n")

        for folder in clean_names:
            if folder.upper() == "INBOX":
                print(f"   Skipping creation of INBOX (already exists by default).")
                continue

            sink_folder_name = folder.replace(".", sink_delimiter)

            if sink_folder_name in existing_sink_names:
                print(f"   Already exists on sink: {sink_folder_name}")
                continue

            try:
                status, response = sink_conn.create(sink_folder_name)
                if status == "OK":
                    print(f"   ✅ Created on sink: {sink_folder_name}")
                else:
                    print(f"   ❌ Failed to create {sink_folder_name}: {response}")
            except Exception as e:
                print(f"   ❌ Error creating {sink_folder_name}: {e}")

        sink_conn.logout()
        print(f"\nAccount {account['number']} done.")
        return True

    except imaplib.IMAP4.error as e:
        print(f"❌ LOGIN/IMAP ERROR for {email}: {e}")
        return False
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        print(f"❌ NETWORK ERROR for {email}: {e}")
        return False
    except Exception as e:
        print(f"❌ UNEXPECTED ERROR for {email}: {e}")
        return False


def main():
    env = load_env_file(".env")
    accounts = discover_accounts(env, ACCOUNT_RANGE_START, ACCOUNT_RANGE_END)

    if not accounts:
        print(f"No accounts found in range {ACCOUNT_RANGE_START}-{ACCOUNT_RANGE_END}.")
        return

    print(f"Processing accounts {ACCOUNT_RANGE_START} to {ACCOUNT_RANGE_END} "
          f"({len(accounts)} account(s)).")
    print(f"Pause between accounts: {PAUSE_BETWEEN_ACCOUNTS}s\n")

    results = {}
    for idx, account in enumerate(accounts):
        success = process_account(account, env)
        results[account["email"]] = success

        if idx < len(accounts) - 1:
            print(f"\nPausing {PAUSE_BETWEEN_ACCOUNTS}s before next account...")
            time.sleep(PAUSE_BETWEEN_ACCOUNTS)

    print(f"\n{'=' * 70}")
    print("SUMMARY (this batch)")
    print(f"{'=' * 70}")
    for email, success in results.items():
        symbol = "✅" if success else "❌"
        print(f"{symbol} {email}")

    print("\nDone with this batch.")
    if ACCOUNT_RANGE_END < 9:
        print(f"Next: update ACCOUNT_RANGE_START={ACCOUNT_RANGE_END + 1} and "
              f"ACCOUNT_RANGE_END accordingly, wait a few minutes, then run again.")


if __name__ == "__main__":
    main()