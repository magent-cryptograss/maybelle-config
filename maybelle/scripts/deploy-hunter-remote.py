#!/usr/bin/env python3
"""
Deploy hunter from your laptop via maybelle
Runs ansible playbook on hunter with SSH agent forwarding
Posts logs to Jenkins for history
"""

import subprocess
import sys
import tempfile
import os
from pathlib import Path
from datetime import datetime


def run_ssh(host, command, forward_agent=False, capture_output=False, check=True):
    """Run SSH command"""
    ssh_cmd = ['ssh']
    if forward_agent:
        ssh_cmd.append('-A')
    ssh_cmd.extend([host, command])

    result = subprocess.run(
        ssh_cmd,
        capture_output=capture_output,
        text=True,
        check=check
    )
    return result


def list_backups():
    """List available database backups on maybelle"""
    result = run_ssh(
        'root@maybelle.cryptograss.live',
        'ls -1 /var/jenkins_home/hunter-db-backups/*.dump 2>/dev/null | xargs -n 1 basename || echo "No backups found"',
        capture_output=True
    )
    backups = [line.strip() for line in result.stdout.strip().split('\n') if line.strip() and line != 'No backups found']
    return backups


def select_backup():
    """Prompt user to select a backup"""
    print("\nAvailable database backups:")
    backups = list_backups()

    if not backups:
        print("  (no backups available)")
        return None

    print("  0) Skip database restoration")
    for i, backup in enumerate(backups, 1):
        print(f"  {i}) {backup}")

    while True:
        try:
            choice = input(f"\nSelect backup (0-{len(backups)}): ").strip()
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= len(backups):
                return backups[idx - 1]
            print("Invalid selection")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled")
            sys.exit(0)


def setup_ssh_agent(key_path):
    """Start SSH agent and add key"""
    print("\nSetting up SSH agent...")

    # Start ssh-agent
    result = subprocess.run(['ssh-agent', '-s'], capture_output=True, text=True)

    # Parse and set environment variables
    for line in result.stdout.split('\n'):
        if '=' in line and ';' in line:
            line = line.split(';')[0]
            key, value = line.split('=', 1)
            os.environ[key] = value

    # Add key (will prompt for passphrase)
    result = subprocess.run(['ssh-add', key_path])
    if result.returncode != 0:
        print("Failed to add SSH key")
        sys.exit(1)

    print("✓ SSH agent configured")


def cleanup_ssh_agent():
    """Kill SSH agent"""
    subprocess.run(['ssh-agent', '-k'], capture_output=True)


def deploy_hunter(backup_file, vault_password):
    """Run ansible deployment on hunter FROM maybelle"""
    print("\n" + "=" * 60)
    print("DEPLOYING HUNTER")
    print("=" * 60)
    print()

    # First, ensure maybelle-config repo is on maybelle and up to date
    print("Updating maybelle-config repository on maybelle...")
    repo_setup = '''
        if [ ! -d /root/maybelle-config ]; then
            git clone https://github.com/cryptograss/maybelle-config.git /root/maybelle-config
        fi
        cd /root/maybelle-config
        git fetch origin
        git checkout production
        git pull origin production

        # Check that production is not behind main
        git fetch origin main
        if ! git merge-base --is-ancestor origin/main production; then
            echo "ERROR: production branch is behind main"
            echo "Please update production to include latest main changes"
            exit 1
        fi
    '''
    run_ssh('root@maybelle.cryptograss.live', repo_setup, forward_agent=True)
    print("✓ Repository updated\n")

    # Build ansible command - run from maybelle, targeting hunter
    # Pass vault password via environment variable
    if backup_file:
        print(f"Using database backup: {backup_file}\n")
        ansible_cmd = f"ANSIBLE_VAULT_PASSWORD='{vault_password}' cd /root/maybelle-config && echo $ANSIBLE_VAULT_PASSWORD | ansible-playbook --vault-password-file=/dev/stdin -i hunter/ansible/inventory.yml hunter/ansible/playbook.yml -e db_backup_file=/var/jenkins_home/hunter-db-backups/{backup_file}"
    else:
        print("Skipping database restoration\n")
        ansible_cmd = f"ANSIBLE_VAULT_PASSWORD='{vault_password}' cd /root/maybelle-config && echo $ANSIBLE_VAULT_PASSWORD | ansible-playbook --vault-password-file=/dev/stdin -i hunter/ansible/inventory.yml hunter/ansible/playbook.yml"

    # Run ansible FROM maybelle (ansible SSHs to hunter using our forwarded agent)
    result = subprocess.run(
        ['ssh', '-A', '-t', 'root@maybelle.cryptograss.live', ansible_cmd],
        check=False
    )

    print()
    print("=" * 60)

    if result.returncode != 0:
        raise Exception(f"Deployment failed with exit code {result.returncode}")

    print("✓ Deployment complete")


def get_vault_password():
    """Get vault password from environment or file"""
    # Try direct password first
    password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
    if password:
        return password

    # Try password file
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

    # Select backup
    backup_file = select_backup()

    # Confirm
    print("\n" + "-" * 60)
    if backup_file:
        print(f"Will deploy hunter WITH database backup: {backup_file}")
    else:
        print("Will deploy hunter WITHOUT database restore")
    print("-" * 60)

    confirm = input("\nContinue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        sys.exit(0)

    # Setup SSH agent
    key_path = str(Path.home() / '.ssh' / 'id_ed25519_hunter')
    setup_ssh_agent(key_path)

    try:
        # Deploy
        deploy_hunter(backup_file, vault_password)
        print("\n✓ SUCCESS")

    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        sys.exit(1)

    finally:
        cleanup_ssh_agent()
        print("SSH agent cleaned up")


if __name__ == '__main__':
    main()
