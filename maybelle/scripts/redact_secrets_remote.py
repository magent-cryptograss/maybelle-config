#!/usr/bin/env python3
"""
Redact secrets from magenta database - run from maybelle.

This script connects directly to the PostgreSQL database on hunter
and redacts vault secrets from message content.

Usage (from maybelle):
    export ANSIBLE_VAULT_PASSWORD=$(cat ~/.vault_pass)
    python scripts/redact_secrets_remote.py --dry-run --verbose
    python scripts/redact_secrets_remote.py  # actually redact
"""

import os
import sys
import json
import argparse
import psycopg2
from pathlib import Path

# Add project root for security module
sys.path.insert(0, str(Path(__file__).parent.parent))
from security.secrets_filter import SecretsFilter


def main():
    parser = argparse.ArgumentParser(description='Redact secrets from magenta database')
    parser.add_argument(
        '--vault-path',
        type=str,
        default=os.path.expanduser('~/workspace/maybelle-config/group_vars/all/vault.yml'),
        help='Path to Ansible vault file'
    )
    parser.add_argument(
        '--db-host',
        type=str,
        default='hunter.cryptograss.live',
        help='Database host'
    )
    parser.add_argument(
        '--db-port',
        type=int,
        default=5432,
        help='Database port'
    )
    parser.add_argument(
        '--db-name',
        type=str,
        default='magenta_memory',
        help='Database name'
    )
    parser.add_argument(
        '--db-user',
        type=str,
        default='magent',
        help='Database user'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be redacted without making changes'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit number of messages to process'
    )

    args = parser.parse_args()

    # Get vault password
    vault_password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
    if not vault_password:
        print("ERROR: ANSIBLE_VAULT_PASSWORD environment variable not set")
        print("Run: export ANSIBLE_VAULT_PASSWORD=$(cat ~/.vault_pass)")
        sys.exit(1)

    # Get database password from vault
    db_password = os.environ.get('MAGENTA_DB_PASSWORD')
    if not db_password:
        print("ERROR: MAGENTA_DB_PASSWORD environment variable not set")
        print("Set it to the memory_lane_postgres_password from vault")
        sys.exit(1)

    # Initialize secrets filter
    print(f"Loading secrets from vault: {args.vault_path}")
    secrets_filter = SecretsFilter(vault_path=args.vault_path, vault_password=vault_password)

    if not secrets_filter.secrets:
        print("ERROR: No secrets loaded from vault")
        sys.exit(1)

    print(f"Loaded {len(secrets_filter.secrets)} secrets to scrub")

    # Connect to database
    print(f"Connecting to {args.db_host}:{args.db_port}/{args.db_name}...")
    conn = psycopg2.connect(
        host=args.db_host,
        port=args.db_port,
        dbname=args.db_name,
        user=args.db_user,
        password=db_password
    )

    try:
        with conn.cursor() as cur:
            # Count messages
            cur.execute("SELECT COUNT(*) FROM conversations_message")
            total = cur.fetchone()[0]
            print(f"Total messages in database: {total}")

            # Query messages
            query = "SELECT id, sender_id, content, created_at FROM conversations_message ORDER BY created_at"
            if args.limit:
                query += f" LIMIT {args.limit}"

            cur.execute(query)

            redacted_count = 0
            updates = []

            for row in cur:
                msg_id, sender_id, content, created_at = row

                # Scrub the content
                scrubbed_content = secrets_filter.scrub_json(content)

                # Check if anything changed
                if scrubbed_content != content:
                    redacted_count += 1

                    if args.verbose:
                        print(f"\n--- Message {msg_id} ---")
                        print(f"Sender: {sender_id}")
                        print(f"Created: {created_at}")
                        orig_str = json.dumps(content)[:200] if content else ''
                        scrub_str = json.dumps(scrubbed_content)[:200] if scrubbed_content else ''
                        print(f"Original: {orig_str}...")
                        print(f"Scrubbed: {scrub_str}...")

                    if not args.dry_run:
                        updates.append((json.dumps(scrubbed_content), str(msg_id)))

            # Apply updates
            if not args.dry_run and updates:
                print(f"\nApplying {len(updates)} updates...")
                with conn.cursor() as update_cur:
                    for scrubbed_json, msg_id in updates:
                        update_cur.execute(
                            "UPDATE conversations_message SET content = %s WHERE id = %s",
                            (scrubbed_json, msg_id)
                        )
                conn.commit()
                print(f"Successfully redacted secrets from {redacted_count} messages")
            elif args.dry_run:
                print(f"\nDRY RUN: Would redact secrets from {redacted_count} messages")
            else:
                print("No messages contained secrets to redact")

            print(f"\nMessages processed: {args.limit or total}")
            print(f"Messages with secrets: {redacted_count}")

    finally:
        conn.close()


if __name__ == '__main__':
    main()
