#!/usr/bin/env python3

import sys
import os
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
parser.add_argument('file_old', nargs='?', help='The old file')
parser.add_argument('file_new', nargs='?', help='The new file')
parser.add_argument('-o', '--out', help='Save the diff to this file')
parser.add_argument('-i', '--input', help='Manually input old and new text', action='store_true')
parser.add_argument('-s', '--separator', help='The separator to use between diffs', default='\n---\n')
parser.add_argument('--rtl', help='set html[dir=rtl]', action='store_true')
parser.add_argument('--max', help='The max size of the diff in mb', type=int, default=5)
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
if args.input:
    old_text, new_text = sys.stdin.read().split(args.separator)
    file_old = NamedTemporaryFile(mode='w', delete=False)
    file_new = NamedTemporaryFile(mode='w', delete=False)
    file_old.write(old_text.strip())
    file_new.write(new_text.strip())
    file_old.close()
    file_new.close()
    args.file_old = file_old.name
    args.file_new = file_new.name
elif not args.file_old or not args.file_new:
    print('Please provide both the old and new files')
    sys.exit(1)

git_cmd = ['git', 'diff', '--no-index', '--color-words', '--word-diff-regex=[^[:space:],<>:!.""‘’“”«»،؟?‹›()^]+|[!.""‘’“”«»،؟?‹›()^,<>:]']
result = run(git_cmd + [args.file_old, args.file_new])

if args.input:
    os.unlink(file_old.name)
    os.unlink(file_new.name)

if result.returncode == 0:
    print('No changes')
    sys.exit(0)
elif result.returncode == 1 and result.stderr:
    print(result.stderr, end='')
    sys.exit(1)

output = re.sub(r'(?s)^.*?@@.*?\n', '', result.stdout)  # remove diff header

if len(output) > args.max * 1024 * 1024:
    print(f'The output diff is {round(len(output) / (1024 * 1024), 2)}mb (the max is {args.max}mb)')
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
    if args.rtl:
        header = header.replace('<html>', '<html dir=rtl>')
    header += f'<title>{args.file_old} -> {args.file_new}</title>'
    fh.write(header + '\n')
    fh.write(output)
    fh.write(footer)
    fh.close()
    try:
        run(['open', diff_file])
    except Exception:
        pass
    if not args.out:
        sleep(0.5)  # give the browser a chance to open the file before deleting

sys.exit(1)
