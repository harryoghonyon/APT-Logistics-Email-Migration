"""
test_connection.py
Multi-account connection test — Module 1.

Purpose: prove we can log into BOTH mailboxes (source + sink) for
EVERY account listed in .env, before writing any migration logic.
No emails are read, moved, or modified here.
"""

import imaplib
import ssl
import os


def load_env_file(path=".env"):
    """Minimal .env file reader — reads KEY=VALUE lines into a dict."""
    env = {}
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Could not find {path}. Make sure it's in the same folder "
            f"as this script, and named exactly '.env'."
        )
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def discover_accounts(env: dict):
    """
    Scans the env dict for ACCOUNT1, ACCOUNT2, ... entries and builds
    a clean list of account dicts. Scans up to ACCOUNT50 to allow
    for non-sequential numbering.
    """
    accounts = []
    for i in range(1, 51):
        email_key = f"ACCOUNT{i}_EMAIL"
        if email_key not in env:
            continue
        accounts.append({
            "label": f"Account {i}",
            "email": env[email_key],
            "source_pass": env.get(f"ACCOUNT{i}_SOURCE_PASS", ""),
            "sink_pass": env.get(f"ACCOUNT{i}_SINK_PASS", ""),
        })
    return accounts


def test_login(host: str, port: int, username: str, password: str):
    """
    Attempts one IMAP login. Returns (success: bool, message: str).
    Never raises -- always returns a result so the caller can keep going.
    """
    try:
        context = ssl.create_default_context()
        connection = imaplib.IMAP4_SSL(host=host, port=port, ssl_context=context, timeout=15)
        connection.login(username, password)
        status, folders = connection.list()
        folder_count = len(folders) if status == "OK" else 0
        connection.logout()
        return True, f"OK ({folder_count} folders)"
    except imaplib.IMAP4.error as e:
        return False, f"LOGIN FAILED: {e}"
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        return False, f"NETWORK ERROR: {e}"
    except Exception as e:
        return False, f"UNEXPECTED ERROR: {e}"


def main():
    print("Loading .env file...")
    env = load_env_file(".env")

    source_host = env["SOURCE_IMAP_HOST"]
    source_port = int(env["SOURCE_IMAP_PORT"])
    sink_host = env["SINK_IMAP_HOST"]
    sink_port = int(env["SINK_IMAP_PORT"])

    accounts = discover_accounts(env)
    if not accounts:
        print("No accounts found in .env (expected ACCOUNT1_EMAIL, ACCOUNT2_EMAIL, ...).")
        return

    print(f"Found {len(accounts)} account(s) to test.\n")

    results = []

    for acct in accounts:
        print(f"--- {acct['label']}: {acct['email']} ---")

        source_ok, source_msg = test_login(
            source_host, source_port, acct["email"], acct["source_pass"]
        )
        symbol = "✅" if source_ok else "❌"
        print(f"  Source (cPanel):    {symbol} {source_msg}")

        sink_ok, sink_msg = test_login(
            sink_host, sink_port, acct["email"], acct["sink_pass"]
        )
        symbol = "✅" if sink_ok else "❌"
        print(f"  Sink (Namecheap):   {symbol} {sink_msg}")

        print()
        results.append((acct["label"], acct["email"], source_ok, sink_ok))

    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_good = True
    for label, email, source_ok, sink_ok in results:
        overall = "✅ READY" if (source_ok and sink_ok) else "❌ NEEDS FIXING"
        if not (source_ok and sink_ok):
            all_good = False
        print(f"{label:<12} {email:<40} {overall}")

    print()
    if all_good:
        print("All accounts connected successfully. Ready to move to the next module.")
    else:
        print("Some accounts failed. Fix the passwords/hosts for those and re-run this script.")


if __name__ == "__main__":
    main()