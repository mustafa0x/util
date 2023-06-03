#!/usr/bin/env python3

import sys
from time import sleep
import regex as re
from pathlib import Path
from subprocess import run as run_
from tempfile import NamedTemporaryFile
from contextlib import contextmanager, nullcontext
from argparse import ArgumentParser


#################################################
# Arguments
#################################################
parser = ArgumentParser()
parser.add_argument('file-old', help='The old file')
parser.add_argument('file-new', help='The new file')
parser.add_argument('-o', '--out', help='Save the diff to this file')
args = parser.parse_args()


#################################################
# Util
#################################################
run = lambda cmd, **kwargs: run_(cmd, capture_output=True, encoding='utf-8', **kwargs)

@contextmanager
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


#################################################
# Dependencies
#################################################
if not run(['which', 'aha']).stdout:
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
result = run(git_cmd + [sys.argv[1], sys.argv[2]])

if result.returncode == 0:
    print('No changes')
    sys.exit(0)

output = re.sub(r'(?s)^.*?@@.*?\n', '', result.stdout)  # remove diff header

if len(output) > 5 * 1024 * 1024:
    print(f'The output diff is {len(output) / (1024 * 1024)}mb (the max is 5mb)')
    sys.exit(1)

output = run(['aha', '--word-wrap'], input=output).stdout
output = apply_repls(output, [
    # remove all of the header, add an id
    (1, r'(?s)<\?xml.*?<pre>', '<pre id=diff-cont dir=auto>'),
    # file positions; replace with a separator
    (1, r'<span style="color:teal;">@@.*', '<div class="sep">• • •</div>'),
    # use del/ins instead of colors
    (1, r'<span style="color:red;">(.*?)</span>', r'<del>\1</del>'),
    (1, r'<span style="color:green;">(.*?)</span>', r'<ins>\1</ins>'),
])

with nullcontext(Path(args.out)) if args.out else tmpfile(suffix='.html') as diff_file:
    fh = diff_file.open('w')
    header, footer = viewer.read_text().split('<!-- SPLIT_AT -->')
    fh.write(header + '\n')
    fh.write(output)
    fh.write(footer)
    fh.close()
    try:
        run(['open', diff_file])
    except Exception:
        pass
    if not args.out:
        sleep(0.1)  # give the browser a chance to open the file before deleting
