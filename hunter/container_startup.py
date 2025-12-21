#!/usr/bin/env python3
"""
Container startup script for magenta-arthel containers.
Handles volume initialization, repository cloning, and service configuration.
"""

import os
import subprocess
import sys
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def run_command(cmd, cwd=None, check=True, user=None):
    """Run a shell command with proper error handling."""
    if user:
        cmd = f"su - {user} -c '{cmd}'"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True
        )
        if result.stdout:
            logger.debug(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed: {cmd}")
        logger.error(f"Error: {e.stderr}")
        if check:
            raise
        return e


def ensure_repo_cloned(repo_url, target_path, user='magent', run_install=False):
    """Clone repository if it doesn't exist, optionally run npm install."""
    target = Path(target_path)

    if target.exists():
        logger.info(f"✓ {target.name} already present at {target}")
        return False

    logger.info(f"Cloning {repo_url} to {target}...")
    target.parent.mkdir(parents=True, exist_ok=True)

    run_command(f"git clone {repo_url} {target}", user=user)
    logger.info(f"✓ Cloned {target.name}")

    if run_install and (target / 'package.json').exists():
        logger.info(f"Installing npm dependencies for {target.name}...")
        run_command("npm install", cwd=target, user=user)
        logger.info(f"✓ Installed dependencies for {target.name}")

    return True


def setup_symlink(source, target, description):
    """Create symlink if source exists and target doesn't."""
    source_path = Path(source)
    target_path = Path(target)

    if not source_path.exists():
        logger.warning(f"Source file not found: {source}")
        return False

    if target_path.exists() or target_path.is_symlink():
        logger.info(f"✓ {description} already exists")
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.symlink_to(source_path)
    logger.info(f"✓ Created symlink: {description}")
    return True


def setup_workspace():
    """Initialize workspace with required repositories."""
    logger.info("=== Setting up workspace ===")

    workspace = Path("/home/magent/workspace")
    workspace.mkdir(parents=True, exist_ok=True)

    # Ensure magent owns the workspace directory
    run_command(f"chown -R magent:magent {workspace}")

    # Clone arthel (main development repo) - optional for local dev
    # Skip if SKIP_ARTHEL env var is set
    if os.getenv('SKIP_ARTHEL'):
        logger.info("Skipping arthel clone (SKIP_ARTHEL set)")
    else:
        ensure_repo_cloned(
            "https://github.com/jMyles/arthel.git",
            workspace / "arthel",
            user='magent',
            run_install=True
        )

    # Clone magenta (for CLAUDE.md and identity docs)
    ensure_repo_cloned(
        "https://github.com/magent-cryptograss/magenta.git",
        workspace / "magenta",
        user='magent',
        run_install=False
    )

    # Clone memory-lane (Django memory system)
    ensure_repo_cloned(
        "https://github.com/jMyles/memory-lane.git",
        workspace / "memory-lane",
        user='magent',
        run_install=False
    )

    # Clone maybelle-config (infrastructure and deployment configs)
    ensure_repo_cloned(
        "https://github.com/cryptograss/maybelle-config.git",
        workspace / "maybelle-config",
        user='magent',
        run_install=False
    )

    # Clone pickipedia (MediaWiki knowledge base)
    ensure_repo_cloned(
        "https://github.com/cryptograss/pickipedia.git",
        workspace / "pickipedia",
        user='magent',
        run_install=False
    )

    # Create/update pickipedia config for local preview
    # Always regenerate .env to ensure correct port assignment
    pickipedia_dir = workspace / "pickipedia"
    if pickipedia_dir.exists():
        pickipedia_env = pickipedia_dir / ".env"
        dev_name = os.environ.get('DEVELOPER_NAME', 'dev')
        # Calculate port based on SSH port offset (justin=4005, rj=4006, skyler=4007)
        ssh_port = int(os.environ.get('SSH_PORT', '2222'))
        wiki_port = 4005 + (ssh_port - 2222)
        # Always write .env to ensure port is correct (may have been created with wrong port)
        pickipedia_env.write_text(f"""# PickiPedia local preview settings for {dev_name}
MEDIAWIKI_VERSION=1.43.0
WIKI_PORT={wiki_port}
COMPOSE_PROJECT_NAME=pickipedia-{dev_name}
DB_NAME=pickipedia
DB_USER=pickipedia
DB_PASSWORD=pickipedia_dev
DB_ROOT_PASSWORD=root_dev
WIKI_URL=https://pickipedia.{dev_name}.hunter.cryptograss.live
""")
        run_command(f"chown magent:magent {pickipedia_env}")
        logger.info(f"✓ Pickipedia .env configured (port {wiki_port})")

        # Create LocalSettings.local.php for docker-compose preview
        # Always regenerate to ensure banner and correct URL are present
        local_settings = pickipedia_dir / "LocalSettings.local.php"
        wiki_url = f"https://pickipedia.{dev_name}.hunter.cryptograss.live"
        local_settings.write_text(f"""<?php
// Local preview settings - connects to docker-compose MySQL
$wgSecretKey = "dev-secret-key-not-for-production-use-only";
$wgUpgradeKey = "dev-upgrade-key";
$wgDBtype = "mysql";
$wgDBserver = "db";  // docker-compose service name
$wgDBname = "pickipedia";
$wgDBuser = "pickipedia";
$wgDBpassword = "pickipedia_dev";

// Override server URL for preview
$wgServer = "{wiki_url}";

// Preview environment banner
$wgSiteNotice = '<div style="background: #ffcc00; color: #000; padding: 10px; text-align: center; font-weight: bold; border: 2px solid #cc9900; margin-bottom: 10px;">
    PREVIEW ENVIRONMENT - This is not the production database. Changes will NOT be saved.
</div>';

// Enable debugging for preview
$wgShowExceptionDetails = true;
$wgShowDBErrorBacktrace = true;
""")
        run_command(f"chown magent:magent {local_settings}")
        logger.info("✓ Created pickipedia LocalSettings.local.php for preview")

        # Create helper script to load production backup into preview
        load_backup_script = pickipedia_dir / "load-backup.sh"
        if not load_backup_script.exists():
            load_backup_script.write_text("""#!/bin/bash
# Load latest PickiPedia backup into preview MySQL
# Run this after 'docker-compose up -d' to populate with production data

set -e

BACKUP_DIR="/opt/magenta/pickipedia-backups"  # Synced from maybelle daily
LATEST_BACKUP=$(ls -t ${BACKUP_DIR}/pickipedia_*.sql.gz 2>/dev/null | head -1)

if [ -z "$LATEST_BACKUP" ]; then
    echo "No backup found in $BACKUP_DIR"
    echo "Backups are created daily at 3:30am on maybelle"
    exit 1
fi

echo "Loading backup: $LATEST_BACKUP"

# Get container name from compose project
CONTAINER=$(docker ps --filter "name=pickipedia.*db" --format "{{.Names}}" | head -1)
if [ -z "$CONTAINER" ]; then
    echo "MySQL container not running. Start with: docker-compose up -d"
    exit 1
fi

# Wait for MySQL to be ready
echo "Waiting for MySQL..."
until docker exec "$CONTAINER" mysqladmin ping -h localhost --silent 2>/dev/null; do
    sleep 1
done

# Load the backup
echo "Loading data (this may take a moment)..."
gunzip -c "$LATEST_BACKUP" | docker exec -i "$CONTAINER" mysql -u pickipedia -ppickipedia_dev pickipedia

echo "Done! PickiPedia preview now has production data."
""")
            run_command(f"chown magent:magent {load_backup_script}")
            run_command(f"chmod +x {load_backup_script}")
            logger.info("✓ Created pickipedia load-backup.sh script")


def setup_host_files():
    """Set up SSH keys and other host-provided config."""
    logger.info("=== Setting up host-provided files ===")

    # SSH authorized_keys from environment variable
    ssh_key = os.environ.get('SSH_AUTHORIZED_KEY', '').strip()
    if ssh_key:
        ssh_dir = Path("/home/magent/.ssh")
        ssh_dir.mkdir(parents=True, exist_ok=True)
        auth_keys = ssh_dir / "authorized_keys"
        if not auth_keys.exists():
            auth_keys.write_text(ssh_key + "\n")
            run_command("chown -R magent:magent /home/magent/.ssh")
            run_command("chmod 700 /home/magent/.ssh")
            run_command("chmod 600 /home/magent/.ssh/authorized_keys")
            logger.info("✓ Set up SSH authorized_keys from environment")
    else:
        logger.info("No SSH_AUTHORIZED_KEY provided, skipping SSH setup")


def setup_claude_config():
    """Set up CLAUDE.md in home directory and ensure .claude directory exists."""
    logger.info("=== Setting up Claude configuration ===")

    # Ensure .claude directory exists for Claude Code's own use
    claude_dir = Path("/home/magent/.claude")
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Symlink CLAUDE.md to home directory (where Claude Code launches)
    setup_symlink(
        "/home/magent/workspace/magenta/CLAUDE.md",
        "/home/magent/CLAUDE.md",
        "CLAUDE.md in home directory"
    )

    # Symlink .env for Django dev server database access
    setup_symlink(
        "/opt/magenta/memory-lane-source/.env",
        "/home/magent/workspace/memory-lane/.env",
        ".env file for memory-lane database access"
    )

    # Fix permissions
    logger.info("Fixing .claude directory permissions...")
    run_command("chown -R magent:magent /home/magent/.claude")
    run_command("chown magent:magent /home/magent/.claude.json", check=False)
    logger.info("✓ Permissions updated")


def configure_github_cli():
    """Configure GitHub CLI if GH_TOKEN is available."""
    logger.info("=== Configuring GitHub CLI ===")

    gh_token = os.environ.get('GH_TOKEN')
    if not gh_token:
        logger.info("No GH_TOKEN provided, skipping GitHub CLI setup")
        return

    logger.info("Authenticating GitHub CLI...")
    run_command(f"echo '{gh_token}' | gh auth login --with-token", user='magent')
    run_command("gh auth setup-git", user='magent')

    # Configure git credential helper to use gh token for HTTPS auth
    # Create a simple credential helper script
    logger.info("Configuring git credential helper...")
    helper_script = Path('/home/magent/.git-credential-helper.sh')
    helper_script.write_text('''#!/bin/bash
echo "username=magent-cryptograss"
echo "password=$(gh auth token)"
''')
    # Fix ownership since we're running as root
    run_command(f'chown magent:magent {helper_script}')
    run_command(f'chmod +x {helper_script}')
    run_command(f'git config --global credential.helper {helper_script}', user='magent', check=False)
    logger.info("✓ GitHub CLI authenticated")


def configure_mcp_server():
    """Configure MCP servers for Claude Code."""
    logger.info("=== Configuring MCP servers ===")

    # The MCP memory server runs on maybelle
    # Use public URL for local dev, private network for hunter deployment
    mcp_url = os.environ.get('MCP_MEMORY_URL', 'https://mcp.maybelle.cryptograss.live')
    run_command(
        f"claude mcp add --scope user --transport http magenta-memory-v2 {mcp_url}",
        user='magent',
        check=False  # Don't fail if already configured
    )
    logger.info(f"✓ MCP memory server configured: {mcp_url}")

    # Add Playwright MCP server for browser automation via Docker
    run_command(
        "claude mcp add --scope user --transport stdio playwright 'docker run -i --rm --init --network magenta-net --pull=always mcr.microsoft.com/playwright/mcp'",
        user='magent',
        check=False
    )
    logger.info("✓ Playwright MCP server configured (via Docker on magenta-net)")

    # Add Jenkins MCP server (connects to Jenkins on maybelle)
    run_command(
        "claude mcp add --scope user --transport http jenkins https://maybelle.cryptograss.live/mcp-server/mcp",
        user='magent',
        check=False
    )
    logger.info("✓ Jenkins MCP server configured: https://maybelle.cryptograss.live/mcp-server/mcp")

    # Add MediaWiki MCP server for PickiPedia
    pickipedia_bot_password = os.environ.get('PICKIPEDIA_BOT_PASSWORD', '')
    if pickipedia_bot_password:
        # Create config file for MediaWiki MCP server
        mw_config_dir = Path("/home/magent/.config/mediawiki-mcp")
        mw_config_dir.mkdir(parents=True, exist_ok=True)
        mw_config_file = mw_config_dir / "config.json"

        import json
        mw_config = {
            "defaultWiki": "pickipedia.xyz",
            "wikis": {
                "pickipedia.xyz": {
                    "sitename": "PickiPedia",
                    "server": "https://pickipedia.xyz",
                    "articlepath": "/wiki",
                    "scriptpath": "",
                    "username": "Magent@magent",
                    "password": pickipedia_bot_password,
                    "private": False
                }
            }
        }
        with open(mw_config_file, 'w') as f:
            json.dump(mw_config, f, indent=2)

        # Set proper ownership
        run_command(f"chown magent:magent {mw_config_file}")
        run_command(f"chmod 600 {mw_config_file}")  # Protect credentials

        # Add the MCP server
        run_command(
            f"claude mcp add --scope user --transport stdio pickipedia --env CONFIG={mw_config_file} -- npx -y @professional-wiki/mediawiki-mcp-server@latest",
            user='magent',
            check=False
        )
        logger.info("✓ PickiPedia MediaWiki MCP server configured")
    else:
        logger.warning("⚠ PICKIPEDIA_BOT_PASSWORD not set, skipping MediaWiki MCP server")


def configure_claude_settings():
    """Configure Claude Code settings."""
    logger.info("=== Configuring Claude Code settings ===")

    settings_dir = Path.home() / '.claude'
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / 'settings.json'

    # Read existing settings if they exist
    if settings_file.exists():
        import json
        with open(settings_file, 'r') as f:
            settings = json.load(f)
    else:
        settings = {}

    # Set includeCoAuthoredBy to false
    settings['includeCoAuthoredBy'] = False

    # Write settings back
    import json
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

    logger.info("✓ Claude Code settings configured")


def setup_environment_variables():
    """Export container environment variables to shell profile."""
    logger.info("=== Setting up environment variables ===")

    # Variables to export to shell sessions
    env_vars = [
        'DEVELOPER_NAME',
        'DEVELOPER_FULL_NAME',
        'DEVELOPER_EMAIL',
        'DEVELOPER_GITHUB',
        'POSTGRES_HOST',
        'POSTGRES_DB',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD'
    ]

    bashrc_path = Path('/home/magent/.bashrc')

    # Read current .bashrc
    if bashrc_path.exists():
        with open(bashrc_path, 'r') as f:
            bashrc_content = f.read()
    else:
        bashrc_content = ''

    # Add environment exports if not already there
    exports_to_add = []
    for var in env_vars:
        value = os.environ.get(var)
        if value and f'export {var}=' not in bashrc_content:
            exports_to_add.append(f'export {var}="{value}"')

    if exports_to_add:
        with open(bashrc_path, 'a') as f:
            f.write('\n# Container environment variables\n')
            f.write('\n'.join(exports_to_add) + '\n')
        logger.info(f"✓ Added {len(exports_to_add)} environment variables to .bashrc")
    else:
        logger.info("✓ Environment variables already configured")


def start_pickipedia_preview():
    """Start PickiPedia preview environment using docker-compose."""
    logger.info("=== Starting PickiPedia preview ===")

    pickipedia_dir = Path("/home/magent/workspace/pickipedia")
    if not pickipedia_dir.exists():
        logger.warning("PickiPedia directory not found, skipping preview startup")
        return

    # Check if docker-compose.yml exists
    compose_file = pickipedia_dir / "docker-compose.yml"
    if not compose_file.exists():
        logger.warning("PickiPedia docker-compose.yml not found, skipping")
        return

    # Get the host path for volume mounts (container path -> host path)
    dev_name = os.environ.get('DEVELOPER_NAME', 'dev')
    host_pickipedia_dir = f"/opt/magenta/{dev_name}/home/workspace/pickipedia"

    # Reset to latest production branch - ensures we have the shared image config
    # Using hard reset because local branch may be stale or have conflicts
    logger.info("Syncing pickipedia to production branch...")
    run_command(f"git config --global --add safe.directory {pickipedia_dir}", check=False)
    run_command("git fetch origin", cwd=pickipedia_dir, user='magent', check=False)
    run_command("git checkout -B production origin/production", cwd=pickipedia_dir, user='magent', check=False)

    # Start docker-compose with host path for volumes
    logger.info(f"Starting PickiPedia containers for {dev_name}...")
    result = run_command(
        f"PICKIPEDIA_DIR={host_pickipedia_dir} docker compose up -d",
        cwd=pickipedia_dir,
        check=False
    )

    if result.returncode != 0:
        logger.warning("PickiPedia preview failed to start (non-fatal)")
        logger.warning(f"  Error: {result.stderr if hasattr(result, 'stderr') else 'unknown'}")
        return

    logger.info("✓ PickiPedia containers started")

    # Wait for DB to be ready, then check if it needs initialization
    import time
    time.sleep(10)  # Give MariaDB time to start

    # Check if database has data (Main_Page exists)
    db_container = f"pickipedia-{dev_name}-db-1"
    check_result = run_command(
        f"docker exec {db_container} mysql -u pickipedia -ppickipedia_dev pickipedia -e \"SELECT COUNT(*) FROM page WHERE page_title='Main_Page'\" 2>/dev/null",
        check=False
    )

    needs_init = check_result.returncode != 0 or "0" in (check_result.stdout or "")

    if needs_init:
        logger.info("Database empty, loading backup...")
        load_script = pickipedia_dir / "load-backup.sh"
        if load_script.exists():
            run_command(f"bash {load_script}", cwd=pickipedia_dir, check=False)
            logger.info("Running MediaWiki update.php...")
            wiki_container = f"pickipedia-{dev_name}-wiki-1"
            run_command(
                f"docker exec {wiki_container} php /var/www/html/maintenance/update.php --quick",
                check=False
            )
            logger.info("✓ Database initialized")

    logger.info(f"✓ PickiPedia preview ready at: https://pickipedia.{dev_name}.hunter.cryptograss.live")


def start_services():
    """Start required services."""
    logger.info("=== Starting services ===")

    # Start SSH
    logger.info("Starting SSH service...")
    run_command("service ssh start")
    logger.info("✓ SSH started")

    # Start code-server (PASSWORD env var required for auth)
    password = os.environ.get('CODE_SERVER_PASSWORD', 'changeme')
    logger.info("Starting code-server...")
    run_command(f"PASSWORD='{password}' nohup code-server --bind-addr 0.0.0.0:8080 --auth password /home/magent/workspace > /tmp/code-server.log 2>&1 &", user='magent')
    logger.info("✓ code-server started on port 8080")

    # Note: PostgreSQL runs in separate container, not started here
    logger.info("✓ Using shared PostgreSQL container")

    # Start PickiPedia preview environment
    start_pickipedia_preview()


def main():
    """Main startup sequence."""
    logger.info("=" * 60)
    logger.info("Container startup beginning")
    logger.info("=" * 60)

    try:
        setup_host_files()
        setup_workspace()
        setup_claude_config()
        setup_environment_variables()
        configure_github_cli()
        configure_mcp_server()
        configure_claude_settings()
        start_services()

        logger.info("=" * 60)
        logger.info("✓ Container startup complete")
        logger.info("=" * 60)

        # Keep container running
        logger.info("Container ready. Keeping alive...")
        subprocess.run(["tail", "-f", "/dev/null"])

    except Exception as e:
        logger.error(f"Startup failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
