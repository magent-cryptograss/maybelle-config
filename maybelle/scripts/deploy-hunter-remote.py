#!/usr/bin/env python3
"""
Deploy hunter from your laptop via maybelle

This script:
1. Gets the vault password from your environment
2. SSHs to maybelle and runs the deploy script there
3. The deploy script on maybelle handles ansible + Jenkins reporting

Prerequisites:
- SSH access to maybelle from your laptop
- Vault password in ANSIBLE_VAULT_PASSWORD or ANSIBLE_VAULT_PASSWORD_FILE
"""

import subprocess
import sys
import os
import getpass


def get_vault_password():
    """Get vault password from environment or file"""
    password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
    if password:
        return password

    password_file = os.environ.get('ANSIBLE_VAULT_PASSWORD_FILE')
    if password_file:
        try:
            with open(password_file, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            raise Exception(f"Vault password file not found: {password_file}")

    raise Exception("Neither ANSIBLE_VAULT_PASSWORD nor ANSIBLE_VAULT_PASSWORD_FILE is set")


def main():
    print("=" * 60)
    print("DEPLOY HUNTER VIA MAYBELLE")
    print("=" * 60)
    print()

    # Check for vault password early
    try:
        vault_password = get_vault_password()
        print("✓ Vault password found")
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        print("\nSet one of:")
        print("  export ANSIBLE_VAULT_PASSWORD='your-password'")
        print("  export ANSIBLE_VAULT_PASSWORD_FILE='/path/to/password/file'")
        sys.exit(1)

    # Confirm
    print("\n" + "-" * 60)
    print("Will deploy hunter (user containers + watcher)")
    print("-" * 60)

    confirm = input("\nContinue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        sys.exit(0)

    # Get local username to pass to maybelle
    local_user = getpass.getuser()

    # Run deploy script on maybelle, passing vault password via stdin
    print("\nConnecting to maybelle...")
    print()

    maybelle = 'root@maybelle.cryptograss.live'
    deploy_script = '/mnt/persist/maybelle-config/maybelle/scripts/deploy-hunter.sh'

    result = subprocess.run(
        ['ssh', '-t', maybelle, f'{deploy_script} {local_user}'],
        input=vault_password + '\n',
        text=True
    )

    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
