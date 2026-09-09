#!/usr/bin/env bash
#
# Print the podcast import bot's credentials as shell exports, read from the
# ansible vault.
#
# Usage, from anywhere:
#
#     eval "$(/path/to/maybelle-config/maybelle/scripts/podcast-bot-env.sh)"
#     podcast-episodes-to-wiki.py --write < episodes.json
#
# The credentials are already in secrets/vault.yml, because the VPS cron needs
# them there. This saves retyping a generated BotPassword to do the same job by
# hand.
#
# It lives here rather than in cryptograss/pickipedia on purpose. Those tools
# are copied to the wiki VPS and run there, where there is no vault and no
# ansible — they read an env file the playbook writes. Teaching them to open a
# vault would give them a dependency they cannot satisfy where they actually
# run, to save a step somewhere else.
#
# Vault password comes from ANSIBLE_VAULT_PASSWORD or
# ANSIBLE_VAULT_PASSWORD_FILE, the same as the deploy scripts.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VAULT="$REPO/secrets/vault.yml"

if [ ! -f "$VAULT" ]; then
    echo "no vault at $VAULT" >&2
    exit 1
fi

cleanup() { [ -n "${TMP_PASS:-}" ] && rm -f "$TMP_PASS"; }
trap cleanup EXIT

if [ -n "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]; then
    PASS_FILE="$ANSIBLE_VAULT_PASSWORD_FILE"
elif [ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
    # Written rather than passed as an argument: ansible-vault wants a file,
    # and a command line is readable from ps by every other process.
    TMP_PASS="$(mktemp)"
    chmod 600 "$TMP_PASS"
    printf '%s\n' "$ANSIBLE_VAULT_PASSWORD" > "$TMP_PASS"
    PASS_FILE="$TMP_PASS"
else
    echo "set ANSIBLE_VAULT_PASSWORD or ANSIBLE_VAULT_PASSWORD_FILE" >&2
    exit 1
fi

# The whole vault is decrypted in memory, but only these two keys are printed.
#
# Parsed with sed rather than a YAML library on purpose: this pipes into
# whichever python3 is first on PATH, which is not necessarily the one ansible
# runs under, and it is a poor trade to make a two-line lookup depend on that
# guess. The two values are a username and a generated BotPassword — plain
# scalars, optionally quoted, no YAML subtlety to get wrong.
VAULT_TEXT="$(ansible-vault view --vault-password-file "$PASS_FILE" "$VAULT")"

extract() {
    printf '%s\n' "$VAULT_TEXT" \
        | sed -n "s/^$1:[[:space:]]*//p" \
        | head -1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

BOT_USER="$(extract podcast_import_bot_user)"
BOT_PASSWORD="$(extract podcast_import_bot_password)"

if [ -z "$BOT_USER" ] || [ -z "$BOT_PASSWORD" ]; then
    echo "vault is missing podcast_import_bot_user and/or podcast_import_bot_password" >&2
    exit 1
fi

# %q, because a generated BotPassword is arbitrary characters and one unlucky
# one in an unquoted export is a confusing evening.
printf 'export PICKIPEDIA_BOT_USER=%q\n' "$BOT_USER"
printf 'export PICKIPEDIA_BOT_PASSWORD=%q\n' "$BOT_PASSWORD"
