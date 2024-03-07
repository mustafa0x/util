from pathlib import Path
import sqlite3
import json
import sys
from argparse import ArgumentParser


#####################################################################
# Arguments
#####################################################################
parser = ArgumentParser()
parser.add_argument('db', help='The database file', type=Path)
parser.add_argument('file', nargs='?', help='The json file to insert. If not provided, stdin is used')
parser.add_argument('-l', '--lines', help='jsonl format', action='store_true')
args = parser.parse_args()


#####################################################################
# Main
#####################################################################
make_stmt = lambda d: (  # noqa: E731
    f'INSERT OR REPLACE INTO {d["table"]} ({", ".join(d["data"].keys())}) VALUES ({", ".join(["?"] * len(d["data"]))})',
    tuple(d["data"].values()),
)

data_source = args.file.open() if args.file else sys.stdin
data = None
row_changed = 0

try:
    db = sqlite3.connect(args.db)
    if args.lines:
        data = (json.loads(line) for line in data_source)
    else:
        data = json.load(data_source)
        if isinstance(data, dict):
            # The json file is an object, but data needs to be a list
            data = [data]

    for d in data:
        # Always assume that we we need to update many rows for a single table
        if isinstance(d['data'], dict):
            d['data'] = [d['data']]

        for p in d['data']:
            cur = db.execute(*make_stmt({'table': d['table'], 'data': p}))
            row_changed += cur.rowcount

    db.commit()
    print(json.dumps({'rows_changed': row_changed}))
except Exception as e:
    print(json.dumps({'error': str(e)}))
