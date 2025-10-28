#!/usr/bin/env python

import sys
import sqlite3
import tomli_w
from pathlib import Path

to_toml = lambda s: tomli_w.dumps(s, multiline_strings=True)

def prep_row(row):
    d = dict(row)
    for k in d:
        if isinstance(d[k], str):
            d[k] = d[k].replace('\r', '\n')
        elif d[k] is None:
            d[k] = ''  # None can't be serialized
    return d

def sqlite_to_toml(db_file, toml_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    data = {}
    # fetchall since we execute another query in the loop, so it changes the data
    for name, in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        data[name] = [prep_row(row) for row in c.execute(f'SELECT * FROM {name}')]

    Path(toml_file).write_text(to_toml(data))

    conn.close()

if __name__ == '__main__':
    db = sys.argv[1]
    db_name = db[:db.rfind('.')]
    sqlite_to_toml(db, f'{db_name}.toml')
