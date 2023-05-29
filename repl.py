#!/usr/bin/env python3

import sys
import regex as re

def apply_repls(text, repls):
    for r in repls:
        text = re.sub(r[1], r[2], text) if r[0] else text.replace(r[1], r[2])
    return text

repls = []
for r in sys.argv[1:]:
    r = r.split(',')
    repls.append((int(r[0]), r[1], r[2]))

sys.stdout.write(apply_repls(sys.stdin.read(), repls))
