#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "rich",
# ]
# ///
"""
To generate svcheck-errors.json:
- `pnpx svelte-check --output machine-verbose 2>&1 | sed -E '1d;$d;s/^[0-9]+ //' > svcheck-errors.json`
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.errors import MarkupError

console = Console()

IGNORED_WARNINGS: set[str] = {
    'a11y_autofocus',
    'a11y_click_events_have_key_events',
    'a11y_no_static_element_interactions',
    'a11y_label_has_associated_control',
    'a11y_no_noninteractive_element_interactions',
}

p = subprocess.run(
    [
        'pnpx',
        'svelte-check',
        '--output',
        'machine-verbose',
        '--compiler-warnings',
        ','.join(f'{code}:ignore' for code in sorted(IGNORED_WARNINGS)),
    ],
    capture_output=True,
    text=True,
)

root = ''
files_scanned, ts_completed = 0, None
errors = warnings = 0
files_with_problems: set[str] = set()
git_ignore_cache: dict[str, bool] = {}
IGNORE_PATH: list[str] = [
    'src/lib/components/ui/',
    'client/lib/components/ui/',
    *[s.strip().replace('\\', '/') for s in os.getenv('SVCHECK_IGNORE_PATHS', '').split(',') if s.strip()],
]

ignore_keys: set[tuple] = set()
ignore_file = Path('svcheck-errors.json')
for line in ignore_file.read_text().splitlines() if ignore_file.exists() else []:
    line = line.strip()
    if not line or line[0] != '{':
        continue
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = item.get('type')
    if t not in {'ERROR', 'WARNING'}:
        continue
    fn0 = (item.get('filename') or '').replace('\\', '/')
    st0 = item.get('start') or {}
    key = (
        t,
        fn0,
        int(st0.get('line') or 0),
        int(st0.get('character') or 0),
        str(item.get('code') or ''),
    )
    ignore_keys.add(key)

for line in p.stdout.splitlines():
    if ' START ' in line:
        m = re.search(r'START\s+"([^"]+)"', line)
        if m:
            root = m.group(1)
        continue
    if ' COMPLETED ' in line:
        m = re.match(r'^(\d+)\s+COMPLETED\s+(\d+)\s+FILES\b', line)
        if m:
            ts_completed = int(m.group(1))
            files_scanned = int(m.group(2))
        continue
    i = line.find('{')
    if i < 0:
        continue
    try:
        j = json.loads(line[i:])
    except json.JSONDecodeError:
        continue

    t = j.get('type')
    if t not in {'ERROR', 'WARNING'}:
        continue
    fn = (j.get('filename') or '').replace('\\', '/')
    if any(fn.startswith(p) for p in IGNORE_PATH):
        continue

    start = j.get('start') or {}
    ln0, ch0 = int(start.get('line') or 0), int(start.get('character') or 0)
    ln, ch = ln0 + 1, ch0 + 1
    path = fn if os.path.isabs(fn) else os.path.join(root, fn)
    norm = os.path.normpath(path)

    # check if git-ignored
    rel = os.path.relpath(norm)
    ignored = git_ignore_cache.get(rel)
    if ignored is None:
        r = subprocess.run(['git', 'check-ignore', '-q', '--', rel])
        ignored = r.returncode == 0
        git_ignore_cache[rel] = ignored
    if ignored:
        continue

    key = (t, fn, ln0, ch0, str(j.get('code') or ''))
    if key in ignore_keys:
        continue

    files_with_problems.add(norm)
    if t == 'ERROR':
        errors += 1
    else:
        warnings += 1

    console.print(f'{norm}:{ln}:{ch}', style='bold cyan')
    console.print(
        f'{"Error" if t == "ERROR" else "Warning"}: {j.get("message", "")} ({j.get("source", "")})',
        style=('bold red' if t == 'ERROR' else 'bold yellow'),
    )
    try:
        with open(norm) as f:
            lines = f.readlines()
        snippet = ''.join(lines[max(0, ln - 1) : ln + 2]).rstrip('\n')
        if snippet:
            try:
                console.print(snippet, style='dim')
            except MarkupError:
                print(snippet)
    except OSError:
        pass
    console.print()

ts = ts_completed or int(time.time() * 1000)
console.print(
    # f'FILES: {files_scanned}, ERRORS: {errors}, WARNINGS: {warnings}, FILES_WITH_PROBLEMS: {len(files_with_problems)}',
    f'ERRORS: {errors}, WARNINGS: {warnings}',
    style='bold',
)
sys.exit(bool(errors + warnings))
