# APT Logistics Services Company Limited

## Overview

This repository contains a Python-based IMAP email migration toolkit for APT Logistics Services Company Limited. It is designed to move mailbox data from a source IMAP server to a destination IMAP server in a controlled, resumable, and secure manner.

The project is suitable for migrating email folders and messages across multiple accounts while reducing the risk of service throttling or connection failures.

## Features

- Validate IMAP access for both the source and destination mailboxes
- Create destination folder structures to mirror the source mailbox layout
- Migrate selected folders and messages with retry handling
- Resume safely from previous runs without re-copying already processed messages
- Track migration progress using a local SQLite database

## Repository contents

- `test_connection.py` – verifies IMAP login access for all configured accounts
- `folder_setup.py` – creates the destination folder structure only
- `migrate_all.py` – performs the full message migration workflow
- `.env` – local environment file containing IMAP hosts and credentials (not committed)
- `migration_state.db` – local state file created during migration runs

## Prerequisites

- Python 3.13
- Access to the source IMAP server
- Access to the destination IMAP server
- Valid credentials for each mailbox account

## Setup

1. Clone the repository.
2. Create a local `.env` file in the project root.
3. Add your IMAP host settings and account credentials to `.env`.
4. Run the scripts from the command line.

### Environment file example

Use the following structure for your local `.env` file:

```env
SOURCE_IMAP_HOST=your-source-host
SOURCE_IMAP_PORT=993
SINK_IMAP_HOST=your-destination-host
SINK_IMAP_PORT=993

ACCOUNT1_EMAIL=first@example.com
ACCOUNT1_SOURCE_PASS=source-password
ACCOUNT1_SINK_PASS=destination-password
```

You can add more accounts using the `ACCOUNTn_` pattern.

## Usage

### 1. Verify IMAP access

```bash
python3.13 test_connection.py
```

This checks whether both the source and destination mailboxes can be accessed successfully before any migration begins.

### 2. Create destination folders

```bash
python3.13 folder_setup.py
```

This step creates the destination mailbox folders only. It does not move messages.

### 3. Migrate email messages

```bash
python3.13 migrate_all.py
```

By default, the migration script processes accounts 1 through 9. To target specific accounts, pass their numbers as arguments:

```bash
python3.13 migrate_all.py 4 5 6
```

## Migration behavior

The migration workflow:

- copies mail from the source to the destination mailbox
- migrates the default folders `INBOX`, `INBOX.Archive`, `INBOX.Sent`, and `INBOX.Drafts`
- retries transient connection issues
- skips messages already copied in a previous run
- pauses between accounts and folders to reduce the chance of being blocked by the server

## Security note

Sensitive information is stored in `.env` and should never be committed to GitHub. Keep this file local, and make sure it is covered by your Git ignore rules.

## Recommended workflow

1. Configure `.env` with the correct credentials.
2. Run `test_connection.py`.
3. Run `folder_setup.py`.
4. Run `migrate_all.py`.
5. Re-run `migrate_all.py` if the process is interrupted.

## Notes

- The migration is designed to be resumable.
- Always test on a small batch first when working with production mailboxes.
- Review the destination mailbox carefully before performing a full migration.
# APT-Logistics-Email-Migration
