#!/usr/bin/env python3
"""
Redact secrets from magenta database - run from your laptop via maybelle.

This script SSHes to maybelle, where it:
1. Uses the local vault password to decrypt secrets
2. Connects to hunter's PostgreSQL database
3. Redacts vault secrets from message content

Usage (from laptop):
    export ANSIBLE_VAULT_PASSWORD='your-vault-password'
    # or
    export ANSIBLE_VAULT_PASSWORD_FILE=~/.vault_pass

    python scripts/redact_secrets_via_maybelle.py --dry-run --verbose
    python scripts/redact_secrets_via_maybelle.py  # actually redact
"""

import subprocess
import sys
import os
import argparse
from pathlib import Path


def run_ssh(host, command, capture_output=False, check=True):
    """Run SSH command"""
    result = subprocess.run(
        ['ssh', host, command],
        capture_output=capture_output,
        text=True,
        check=check
    )
    return result


def get_vault_password():
    """Get vault password from environment or file"""
    password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
    if password:
        return password

    password_file = os.environ.get('ANSIBLE_VAULT_PASSWORD_FILE')
    if password_file:
        try:
            with open(os.path.expanduser(password_file), 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            raise Exception(f"Vault password file not found: {password_file}")

    raise Exception("Neither ANSIBLE_VAULT_PASSWORD nor ANSIBLE_VAULT_PASSWORD_FILE is set")


def main():
    parser = argparse.ArgumentParser(description='Redact secrets from magenta database via maybelle')
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

    print("=" * 60)
    print("REDACT SECRETS VIA MAYBELLE")
    print("=" * 60)

    # Get vault password
    try:
        vault_password = get_vault_password()
        print("✓ Vault password found")
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        print("\nSet one of:")
        print("  export ANSIBLE_VAULT_PASSWORD='your-password'")
        print("  export ANSIBLE_VAULT_PASSWORD_FILE='~/.vault_pass'")
        sys.exit(1)

    # Ensure maybelle-config repo is up to date on maybelle
    print("\nUpdating maybelle-config repository on maybelle...")
    repo_setup = '''
        if [ ! -d /root/maybelle-config ]; then
            git clone https://github.com/cryptograss/maybelle-config.git /root/maybelle-config
        fi
        cd /root/maybelle-config
        git fetch origin
        git checkout production || git checkout -b production origin/production
        git reset --hard origin/production
    '''
    run_ssh('root@maybelle.cryptograss.live', repo_setup)
    print("✓ Repository updated")

    # Create temp vault password file on maybelle
    print("\nSetting up vault password on maybelle...")
    vault_file_path = f'/tmp/vault_pass_{os.getpid()}'

    # Escape single quotes in password for shell
    escaped_password = vault_password.replace("'", "'\\''")
    write_vault = f"echo '{escaped_password}' > {vault_file_path} && chmod 600 {vault_file_path}"
    run_ssh('root@maybelle.cryptograss.live', write_vault)
    print("✓ Vault password configured")

    try:
        # Build the command to run on maybelle
        # The script will decrypt vault to get DB password, then connect to hunter

        # Build args
        script_args = []
        if args.dry_run:
            script_args.append('--dry-run')
        if args.verbose:
            script_args.append('--verbose')
        if args.limit:
            script_args.append(f'--limit {args.limit}')

        args_str = ' '.join(script_args)

        # Run redaction script on maybelle
        # First, get the DB password from vault
        redact_cmd = f'''
            cd /root/maybelle-config

            # Get DB password from vault
            export MAGENTA_DB_PASSWORD=$(ansible-vault view --vault-password-file={vault_file_path} /root/maybelle-config/secrets/vault.yml 2>/dev/null | grep memory_lane_postgres_password | awk '{{print $2}}')

            if [ -z "$MAGENTA_DB_PASSWORD" ]; then
                echo "ERROR: Could not extract DB password from vault"
                exit 1
            fi

            # Run the redaction script using ops venv
            /opt/magenta-ops/bin/python maybelle/scripts/redact_secrets_remote.py {args_str}
        '''

        print("\nRunning redaction on maybelle...")
        print("-" * 60)

        result = subprocess.run(
            ['ssh', '-t', 'root@maybelle.cryptograss.live', redact_cmd],
            check=False
        )

        print("-" * 60)

        if result.returncode != 0:
            print(f"\n✗ Redaction failed with exit code {result.returncode}")
            sys.exit(1)

        print("\n✓ Redaction complete")

    finally:
        # Clean up vault password file
        print("\nCleaning up...")
        run_ssh('root@maybelle.cryptograss.live', f'rm -f {vault_file_path}', check=False)
        print("✓ Vault password removed from maybelle")


if __name__ == '__main__':
    main()
