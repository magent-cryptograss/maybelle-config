#!/usr/bin/env python3
"""Run audit-storage.py; post results to PickiPedia.

Two output channels:

1. ``Cryptograss:delivery-kid-audits/Latest`` — overwritten every run as a
   bot edit (filtered out of recent changes by default), so anyone curious
   about the most recent audit has a stable URL to check.

2. ``Cryptograss:delivery-kid-audits/<blockheight>`` — created only when
   problems are detected. These accumulate over time and DO show in
   recent changes, so they act as the "something needs attention" signal.

Required env vars:
  BLUERAILROAD_BOT_USERNAME
  BLUERAILROAD_BOT_PASSWORD

Optional env vars:
  WIKI_URL           — defaults to https://pickipedia.xyz
  AUDIT_TIMEOUT_SECS — defaults to 600
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import mwclient


SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_SCRIPT = SCRIPT_DIR / "audit-storage.py"
WIKI_URL = os.environ.get("WIKI_URL", "https://pickipedia.xyz")
AUDIT_TIMEOUT = int(os.environ.get("AUDIT_TIMEOUT_SECS", "600"))

LATEST_PAGE_TITLE = "Cryptograss:Delivery-kid-audits/Latest"

# Ethereum merge constants — matches the formula used by the Special:Deliver* pages.
MERGE_BLOCK = 15537394
MERGE_TIMESTAMP = 1663224179
SLOT_TIME = 12

# Summary lines whose non-zero counts indicate something needs human attention.
# Abandoned drafts are deliberate state and don't count. Dead wiki drafts
# accumulate as users start-and-leave, so they aren't urgent on their own —
# include if you decide otherwise.
PROBLEM_LABELS = (
    "Orphan pins",
    "Missing pins",
    "Orphan seeds",
    "Missing seeds",
    "Orphan drafts",
    "Stalled drafts",
    "Cleanup pending",
)
_SUMMARY_LINE_RE = re.compile(
    r"^\s+(" + "|".join(re.escape(label) for label in PROBLEM_LABELS) + r"):\s+(\d+)"
)

# UUID4-format draft IDs in the audit output → ReleaseDraft:<uuid> wiki pages.
_UUID_RE = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
# CIDv1 (bafy…, ~59 chars) and CIDv0 (Qm…, 46 chars). The audit lowercases
# Qm CIDs for cross-comparison so the strict base58 alphabet doesn't match
# anymore — use a permissive [a-zA-Z0-9] character class instead.
_CID_RE = re.compile(
    r"\b((?:[Bb]afy[a-zA-Z0-9]{50,60}|[Qq]m[a-zA-Z0-9]{44}))\b"
)


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


def detect_problems(audit_text: str) -> dict[str, int]:
    """Pull non-zero counts for the labels we care about out of the summary."""
    found: dict[str, int] = {}
    for line in audit_text.splitlines():
        m = _SUMMARY_LINE_RE.match(line)
        if m:
            n = int(m.group(2))
            if n > 0:
                found[m.group(1)] = n
    return found


def linkify_audit(text: str) -> str:
    """Wrap recognized page references in MediaWiki link syntax.

    Targets: ReleaseDraft:<uuid> and Release:<cid>. MediaWiki normalizes
    the first letter of a page title, so passing through whatever case
    appears in the audit output resolves to the canonical page name.
    """
    text = _UUID_RE.sub(r"[[ReleaseDraft:\1|\1]]", text)
    text = _CID_RE.sub(r"[[Release:\1|\1]]", text)
    return text


def to_indented_pre(text: str) -> str:
    """Convert plain text to a MediaWiki leading-space preformatted block.

    HTML <pre>...</pre> renders wiki markup literally, so [[link]] inside
    it stays as raw text. The leading-space variant of pre DOES process
    wiki markup — convert each line to start with a space. Empty lines
    become a single space so they don't break the pre block.
    """
    return "\n".join(
        (" " + line) if line else " "
        for line in text.split("\n")
    )


def _status_banner(problems: dict[str, int]) -> str:
    """Build a colored callout summarizing whether action is required.

    Drawn at the top of every audit page so the action items (or the
    all-clear) are the first thing a reader sees.
    """
    if problems:
        items = "\n".join(
            f"* '''{label}''': {count}" for label, count in problems.items()
        )
        return (
            '<div style="background:#fef6e7; border:2px solid #ac6600; '
            'padding:0.75em 1em; margin:1em 0; border-radius:4px;">\n'
            "'''⚠ Action required'''\n\n"
            f"{items}\n"
            "</div>\n"
        )
    return (
        '<div style="background:#d5fdf4; border:2px solid #14866d; '
        'padding:0.75em 1em; margin:1em 0; border-radius:4px;">\n'
        "'''✓ All clear''' — no problems detected.\n"
        "</div>\n"
    )


def _build_page_content(
    blockheight: int,
    audit_text: str,
    returncode: int,
    problems: dict[str, int],
) -> str:
    status_label = "OK" if returncode == 0 else f"audit script exited {returncode}"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    return (
        f"Audit at Ethereum block "
        f"[https://etherscan.io/block/{blockheight} {blockheight}] "
        f"({timestamp}) — {status_label}.\n\n"
        f"{_status_banner(problems)}\n"
        "==Full audit output==\n"
        f"{to_indented_pre(linkify_audit(audit_text))}\n\n"
        "[[Category:Delivery Kid Audits]]\n"
    )


def _login() -> mwclient.Site:
    user = os.environ["BLUERAILROAD_BOT_USERNAME"]
    password = os.environ["BLUERAILROAD_BOT_PASSWORD"]
    host = WIKI_URL.replace("https://", "").replace("http://", "").rstrip("/")
    site = mwclient.Site(host, scheme="https", path="/")
    site.login(user, password)
    return site


def post_problem_page(
    site: mwclient.Site,
    blockheight: int,
    audit_text: str,
    returncode: int,
    problems: dict[str, int],
) -> str:
    """Create the per-blockheight problem page (visible in recent changes)."""
    title = f"Cryptograss:Delivery-kid-audits/{blockheight}"
    short = ", ".join(f"{label} {count}" for label, count in problems.items())
    site.pages[title].save(
        _build_page_content(blockheight, audit_text, returncode, problems),
        summary=f"Audit at block {blockheight}: {short}",
        bot=False,
    )
    return title


def post_latest_page(
    site: mwclient.Site,
    blockheight: int,
    audit_text: str,
    returncode: int,
    problems: dict[str, int],
) -> str:
    """Update the always-fresh Latest page (bot edit, hidden from RC by default)."""
    short = (
        ", ".join(f"{label} {count}" for label, count in problems.items())
        if problems else "clean"
    )
    site.pages[LATEST_PAGE_TITLE].save(
        _build_page_content(blockheight, audit_text, returncode, problems),
        summary=f"Audit at block {blockheight}: {short}",
        bot=True,
    )
    return LATEST_PAGE_TITLE


def main():
    blockheight = current_blockheight()
    print(f"=== Audit run at block {blockheight} ===\n")

    audit_text, rc = run_audit()
    # Mirror to stdout so the runner's logfile captures the full audit.
    print(audit_text)

    problems = detect_problems(audit_text)
    site = _login()

    # Always update Latest so there's a stable place to see the most recent
    # run. Marked as bot edit → filtered from recent changes by default.
    post_latest_page(site, blockheight, audit_text, rc, problems)
    print(f"Latest updated: {WIKI_URL}/wiki/{LATEST_PAGE_TITLE.replace(' ', '_')}")

    if not problems:
        print(f"\nNo problems detected at block {blockheight}; "
              "no per-block page posted.")
        sys.exit(0)

    summary_inline = ", ".join(f"{k}={v}" for k, v in problems.items())
    print(f"\nProblems detected ({summary_inline}); posting per-block page...")
    title = post_problem_page(site, blockheight, audit_text, rc, problems)
    print(f"Posted to: {WIKI_URL}/wiki/{title.replace(' ', '_')}")
    sys.exit(0)


if __name__ == "__main__":
    main()
