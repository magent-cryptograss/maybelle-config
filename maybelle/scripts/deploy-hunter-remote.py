#!/usr/bin/env python3
"""
Deploy hunter from your laptop via maybelle
Runs ansible playbook on hunter using maybelle's SSH key

Note: PostgreSQL is now on maybelle, not hunter. Hunter only runs:
- User containers
- Watcher (posting to maybelle's ingest endpoint)

Prerequisites:
- SSH access to maybelle from your laptop
- Maybelle has SSH access to hunter (via its own key)
- Vault password available via ANSIBLE_VAULT_PASSWORD or ANSIBLE_VAULT_PASSWORD_FILE
"""

import subprocess
import sys
import os
import time
import urllib.request
import urllib.parse
import base64
import getpass


def run_ssh(host, command, capture_output=False, check=True, allocate_tty=False):
    """Run SSH command on remote host"""
    ssh_cmd = ['ssh']
    if allocate_tty:
        ssh_cmd.append('-t')
    ssh_cmd.extend([host, command])

    result = subprocess.run(
        ssh_cmd,
        capture_output=capture_output,
        text=True,
        check=check
    )
    return result


def get_jenkins_credentials():
    """Get Jenkins reporter credentials from environment"""
    password = os.environ.get('JENKINS_REPORTER_PASSWORD')
    if not password:
        return None
    return ('reporter', password)


def report_to_jenkins(user, status, duration, log_output):
    """Report deployment result to Jenkins for logging"""
    creds = get_jenkins_credentials()
    if not creds:
        print("⚠ JENKINS_REPORTER_PASSWORD not set, skipping Jenkins report")
        return

    try:
        jenkins_url = "http://maybelle.cryptograss.live:8080/job/deploy-hunter/buildWithParameters"

        params = {
            'DEPLOY_USER': user,
            'DEPLOY_STATUS': status,
            'DEPLOY_DURATION': str(int(duration)),
            'DEPLOY_LOG': log_output[-50000:] if log_output else '(no log captured)'  # Truncate if huge
        }

        data = urllib.parse.urlencode(params).encode('utf-8')
        req = urllib.request.Request(jenkins_url, data=data, method='POST')

        # Add Basic Auth header
        auth_string = f"{creds[0]}:{creds[1]}"
        auth_bytes = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')
        req.add_header('Authorization', f'Basic {auth_bytes}')

        urllib.request.urlopen(req, timeout=10)
        print("✓ Reported to Jenkins")
    except Exception as e:
        print(f"⚠ Could not report to Jenkins: {e}")


def deploy_hunter(vault_password):
    """Run ansible deployment on hunter FROM maybelle"""
    print("\n" + "=" * 60)
    print("DEPLOYING HUNTER")
    print("=" * 60)
    print()

    maybelle = 'root@maybelle.cryptograss.live'
    start_time = time.time()
    log_output = []
    deploy_user = getpass.getuser()

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
    run_ssh(maybelle, repo_setup)
    print("✓ Repository updated\n")

    # Create temp vault password file on maybelle
    print("Creating temporary vault password file on maybelle...")
    vault_file_path = '/tmp/vault_pass_' + str(os.getpid())

    # Escape single quotes in password for shell
    escaped_password = vault_password.replace("'", "'\"'\"'")
    write_vault = f"echo '{escaped_password}' > {vault_file_path} && chmod 600 {vault_file_path}"
    run_ssh(maybelle, write_vault)

    try:
        # Build ansible command using the temp vault file
        # Run from hunter/ansible directory so ansible.cfg is found
        # Maybelle uses its own SSH key to reach hunter (no agent forwarding needed)
        ansible_cmd = f"cd /root/maybelle-config/hunter/ansible && ansible-playbook --vault-password-file={vault_file_path} -i inventory.yml playbook.yml"

        print("Running ansible playbook on maybelle (targeting hunter)...")
        print()

        # Run ansible FROM maybelle - maybelle SSHs to hunter using its own key
        # Capture output for Jenkins report
        result = subprocess.run(
            ['ssh', '-t', maybelle, ansible_cmd],
            capture_output=True,
            text=True,
            check=False
        )

        # Print output to console
        if result.stdout:
            print(result.stdout)
            log_output.append(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
            log_output.append(result.stderr)

    finally:
        # Clean up vault password file
        run_ssh(maybelle, f'rm -f {vault_file_path}', check=False)

    duration = time.time() - start_time
    print()
    print("=" * 60)

    if result.returncode != 0:
        report_to_jenkins(deploy_user, 'failure', duration, '\n'.join(log_output))
        raise Exception(f"Deployment failed with exit code {result.returncode}")

    print("✓ Deployment complete")
    report_to_jenkins(deploy_user, 'success', duration, '\n'.join(log_output))


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
    print()
    print("Note: PostgreSQL is now on maybelle. Hunter runs:")
    print("  - User containers")
    print("  - Watcher (posting to maybelle)")
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

    try:
        # Deploy - no local SSH agent needed, maybelle has its own key
        deploy_hunter(vault_password)
        print("\n✓ SUCCESS")

    except Exception as e:
        print(f"\n✗ FAILED: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
