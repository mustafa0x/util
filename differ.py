#!/usr/bin/env python3

import sys
from time import sleep
import regex as re
from pathlib import Path
from subprocess import run
from tempfile import NamedTemporaryFile
import contextlib

#################################################
# Util
#################################################
@contextlib.contextmanager
def tmpfile(suffix=None):
    try:
        file = Path(NamedTemporaryFile(mode='w', suffix=suffix, delete=False).name)
        yield file
    finally:
        file.unlink()

def apply_repls(text, repls):
    for r in repls:
        text = re.sub(r[1], r[2], text) if r[0] else text.replace(r[1], r[2])
    return text

if len(sys.argv) < 3:
    print('Usage: differ.py <file1> <file2>')
    sys.exit(1)

if not run(['which', 'aha'], capture_output=True).stdout:
    print('aha (https://github.com/theZiz/aha) is not installed, please install it')
    sys.exit(1)

viewer_url = 'https://github.com/mustafa0x/util/raw/master/_diff_viewer.html'
viewer = Path(__file__).resolve().parent / '_diff_viewer.html' # `resolve` in case a symlink
if not viewer.exists():
    print('Downloading _diff_viewer.html')
    run(['curl', '-S', viewer_url, '-o', viewer.absolute()])

#################################################
# Main
#################################################
git_cmd = ['git', 'diff', '--no-index', '--color-words', '--word-diff-regex=[^[:space:],!.""‹›^]+|[!.""‹›^,]']
result = run(git_cmd + [sys.argv[1], sys.argv[2]], capture_output=True, encoding='utf-8')

if result.returncode == 0:
    print('No changes')
    sys.exit(0)

output = result.stdout

if len(output) > 5 * 1024 * 1024:
    print(f'The output diff is {len(output) / (1024 * 1024)}mb (the max is 5mb)')
    sys.exit(1)

output = run(['aha', '--word-wrap'], input=output, capture_output=True, encoding='utf-8').stdout
output = apply_repls(output, [
    # remove all of the header, add an id
    (1, r'(?s)<\?xml.*?<pre>', '<pre id=diff-cont dir=auto>'),
    # remove diff header info
    (1, r'<span style="font-weight:bold;color:dimgray;">.*', ''),
    # file positions; replace with a separator
    (1, r'<span style="color:teal;">@@.*', '<div class="sep">• • •</div>'),
    # use del/ins instead of colors
    (1, r'<span style="color:red;">(.*?)</span>', '<del>\1</del>'),
    (1, r'<span style="color:green;">(.*?)</span>', '<ins>\1</ins>'),
])

with tmpfile(suffix='.html') as diff_file:
    fh = diff_file.open('w')
    header, footer = viewer.read_text().split('<!-- SPLIT_AT -->')
    fh.write(header + '\n')
    fh.write(output)
    fh.write(footer)
    fh.close()
    run(['open', diff_file])
    sleep(0.2)
