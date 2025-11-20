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
import argparse
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


def deploy_hunter(backup_file, vault_password, no_cache=False):
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

        # Hard reset to production (handles force pushes/rebases)
        git checkout production || git checkout -b production origin/production
        git reset --hard origin/production

        # Check that production is not behind main
        if ! git merge-base --is-ancestor origin/main origin/production; then
            echo "ERROR: production branch is behind main"
            echo "Please update production to include latest main changes"
            exit 1
        fi
    '''
    run_ssh('root@maybelle.cryptograss.live', repo_setup, forward_agent=True)
    print("✓ Repository updated\n")

    # Build ansible command - run from maybelle, targeting hunter
    # Create temp vault password file on maybelle
    print("Creating temporary vault password file on maybelle...")
    import tempfile as tmp
    vault_file_path = '/tmp/vault_pass_' + str(os.getpid())

    # Write password to temp file on maybelle
    write_vault = f"echo '{vault_password}' > {vault_file_path} && chmod 600 {vault_file_path}"
    run_ssh('root@maybelle.cryptograss.live', write_vault, forward_agent=True)

    try:
        # Build ansible command using the temp vault file
        # Run from hunter/ansible directory so ansible.cfg is found
        extra_vars = []
        if backup_file:
            print(f"Using database backup: {backup_file}\n")
            extra_vars.append(f"db_backup_file=/var/jenkins_home/hunter-db-backups/{backup_file}")
        else:
            print("Skipping database restoration\n")

        if no_cache:
            print("Docker build cache disabled\n")
            extra_vars.append("docker_no_cache=true")

        extra_vars_str = " -e ".join(extra_vars)
        if extra_vars_str:
            extra_vars_str = f" -e {extra_vars_str}"

        ansible_cmd = f"cd /root/maybelle-config/hunter/ansible && ansible-playbook --vault-password-file={vault_file_path} -i inventory.yml playbook.yml{extra_vars_str}"

        # Run ansible in tmux session on maybelle so it survives connection drops
        session_name = f"hunter-deploy-{os.getpid()}"
        log_file = f"/tmp/hunter-deploy-{os.getpid()}.log"

        # Create tmux session running ansible, logging to file
        tmux_cmd = f"tmux new-session -d -s {session_name} 'set -o pipefail; {ansible_cmd} 2>&1 | tee {log_file}; echo EXIT_CODE=$? >> {log_file}'"
        run_ssh('root@maybelle.cryptograss.live', tmux_cmd, forward_agent=True)

        print(f"Ansible running in tmux session '{session_name}' on maybelle")
        print(f"Log file: {log_file}")
        print("\nAttaching to session (Ctrl-B D to detach without stopping)...\n")

        # Attach to the session so user can watch
        result = subprocess.run(
            ['ssh', '-A', '-t', 'root@maybelle.cryptograss.live', f'tmux attach -t {session_name}'],
            check=False
        )

        # If we got disconnected, session may still be running
        if result.returncode != 0:
            print("\nConnection lost. Checking if deployment is still running...")
            check_result = run_ssh(
                'root@maybelle.cryptograss.live',
                f'tmux has-session -t {session_name} 2>/dev/null && echo "RUNNING" || echo "FINISHED"',
                capture_output=True
            )
            if "RUNNING" in check_result.stdout:
                print(f"\nDeployment still running in tmux session '{session_name}'")
                print(f"Reconnect with: ssh -A root@maybelle.cryptograss.live tmux attach -t {session_name}")
                print(f"Or check logs: ssh root@maybelle.cryptograss.live cat {log_file}")
                return
            else:
                # Session finished, check exit code from log
                exit_check = run_ssh(
                    'root@maybelle.cryptograss.live',
                    f'grep "EXIT_CODE=" {log_file} | tail -1',
                    capture_output=True
                )
                if "EXIT_CODE=0" in exit_check.stdout:
                    print("Deployment completed successfully!")
                    result.returncode = 0
                else:
                    print(f"Deployment may have failed. Check log: {log_file}")
                    result.returncode = 1
    finally:
        # Clean up vault password file
        run_ssh('root@maybelle.cryptograss.live', f'rm -f {vault_file_path}', forward_agent=True, check=False)

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
    parser = argparse.ArgumentParser(description='Deploy hunter via maybelle')
    parser.add_argument('--no-cache', action='store_true',
                        help='Disable Docker build cache (use when magenta code has changed)')
    args = parser.parse_args()

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
        deploy_hunter(backup_file, vault_password, no_cache=args.no_cache)
        print("\n✓ SUCCESS")

    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        sys.exit(1)

    finally:
        cleanup_ssh_agent()
        print("SSH agent cleaned up")


if __name__ == '__main__':
    main()
