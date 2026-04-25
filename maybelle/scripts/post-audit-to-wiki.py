#!/usr/bin/env python3
"""Run audit-storage.py and post the results to PickiPedia.

Captures audit stdout, estimates the current Ethereum blockheight, and
creates a wiki page at ``Cryptograss:delivery-kid-audits/<blockheight>``
using the Blue Railroad Imports bot.

Required env vars:
  BLUERAILROAD_BOT_USERNAME
  BLUERAILROAD_BOT_PASSWORD

Optional env vars:
  WIKI_URL   — defaults to https://pickipedia.xyz
  AUDIT_TIMEOUT_SECS — defaults to 600
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import mwclient


SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_SCRIPT = SCRIPT_DIR / "audit-storage.py"
WIKI_URL = os.environ.get("WIKI_URL", "https://pickipedia.xyz")
AUDIT_TIMEOUT = int(os.environ.get("AUDIT_TIMEOUT_SECS", "600"))

# Ethereum merge constants — matches the formula used by the Special:Deliver* pages.
MERGE_BLOCK = 15537394
MERGE_TIMESTAMP = 1663224179
SLOT_TIME = 12


def current_blockheight() -> int:
    return MERGE_BLOCK + (int(time.time()) - MERGE_TIMESTAMP) // SLOT_TIME


def run_audit() -> tuple[str, int]:
    """Return (combined_output, returncode) from audit-storage.py."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT_SCRIPT)],
        capture_output=True, text=True, timeout=AUDIT_TIMEOUT,
    )
    out = proc.stdout
    if proc.stderr.strip():
        out += "\n--- stderr ---\n" + proc.stderr
    return out, proc.returncode


def post_to_wiki(blockheight: int, audit_text: str, returncode: int) -> str:
    user = os.environ["BLUERAILROAD_BOT_USERNAME"]
    password = os.environ["BLUERAILROAD_BOT_PASSWORD"]

    host = WIKI_URL.replace("https://", "").replace("http://", "").rstrip("/")
    site = mwclient.Site(host, scheme="https", path="/")
    site.login(user, password)

    title = f"Cryptograss:delivery-kid-audits/{blockheight}"
    status_label = "OK" if returncode == 0 else f"audit script exited {returncode}"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    content = (
        f"Audit captured at Ethereum block "
        f"[https://etherscan.io/block/{blockheight} {blockheight}] "
        f"({timestamp}) — {status_label}.\n\n"
        "<pre>\n"
        f"{audit_text}"
        "\n</pre>\n\n"
        "[[Category:Delivery Kid Audits]]\n"
    )

    page = site.pages[title]
    page.save(content, summary=f"Audit at block {blockheight} ({status_label})")
    return title


def main():
    blockheight = current_blockheight()
    target = f"Cryptograss:delivery-kid-audits/{blockheight}"
    print(f"=== Running audit; will post to {target} ===\n")

    audit_text, rc = run_audit()
    # Mirror audit output to Jenkins console for easy inspection.
    print(audit_text)

    title = post_to_wiki(blockheight, audit_text, rc)
    print(f"\nPosted to: {WIKI_URL}/wiki/{title.replace(' ', '_')}")

    # Exit 0 even if audit had warnings — posting succeeded is what matters here.
    # Non-zero from audit is surfaced in the wiki page header and the summary line.
    sys.exit(0)


if __name__ == "__main__":
    main()
