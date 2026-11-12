#!/usr/bin/env python
"""Fails (exit 1) if tracked/committed files look like they contain secrets, or .env is tracked.
Usage: python scripts/scan_secrets.py [--history]   (--history also scans every commit in git history)"""
import re, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    "PayPal-style secret (assigned)": re.compile(r"(PAYPAL_SECRET|PAYPAL_CLIENT_ID)\s*=\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    "Generic secret assignment": re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*['\"][A-Za-z0-9_\-/+=]{16,}['\"]"),
    "Private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
SKIP_SUFFIX = {".png", ".jpg", ".gif", ".zip", ".pyc", ".db"}

def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, errors="replace")

def main():
    problems = []
    if git("rev-parse", "--git-dir").returncode != 0:
        sys.exit("not a git repository (run `git init` first)")
    tracked = git("ls-files").stdout.split()
    if ".env" in tracked:
        problems.append(".env is TRACKED by git (remove it: git rm --cached .env)")
    for f in tracked:
        p = ROOT / f
        if p.suffix in SKIP_SUFFIX or not p.is_file(): continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(p.read_text(errors="replace")):
                problems.append(f"{f}: {name}: {m.group(0)[:12]}...")
    if "--history" in sys.argv:
        log = git("log", "-p", "--all").stdout
        for name, rx in PATTERNS.items():
            for m in rx.finditer(log):
                problems.append(f"git history: {name}: {m.group(0)[:12]}...")
        if git("log", "--all", "--oneline", "--", ".env").stdout.strip():
            problems.append(".env appears in git history")
    if problems:
        print("POSSIBLE SECRETS FOUND:"); [print(" -", x) for x in sorted(set(problems))]; sys.exit(1)
    print(f"secret scan: nothing found in {len(tracked)} tracked files" + (" + history" if "--history" in sys.argv else ""))

if __name__ == "__main__":
    main()
