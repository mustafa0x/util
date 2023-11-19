from pathlib import Path
import regex as re
import sys

def apply_repls(text, repls):
    for r in repls:
        text = re.sub(r[1], r[2], text) if r[0] else text.replace(r[1], r[2])
    return text

text = ''
write_stdout = False
if len(sys.argv) == 1:
    text = sys.stdin.read()
    write_stdout = True
else:
    text = Path(sys.argv[1]).read_text()
    if len(sys.argv) == 2:
        sys.argv.append(sys.argv[1])

text = apply_repls(text, [
])

if write_stdout:
    sys.stdout.write(text)
else:
    Path(sys.argv[2]).write_text(text)
