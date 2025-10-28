#!/usr/bin/env python

import sys
import sqlite3
import tomli_w
from pathlib import Path
import json
import regex as re

to_toml = lambda s: tomli_w.dumps(s, multiline_strings=True)

def apply_repls(text, repls):
    for r in repls:
        text = re.sub(r[1], r[2], text) if r[0] else text.replace(r[1], r[2])
    return text

is_file = len(sys.argv) > 1
file = sys.argv[1] if is_file else sys.stdin
data_str = open(file).read() if is_file else sys.stdin.read()
data = json.loads(data_str)
if is_file:
    Path(f'{file[:file.rfind(".")]}.toml').write_text(to_toml({'data': data}))
else:
    print(to_toml({'data': data}))
