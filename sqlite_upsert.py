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
def insert(db, d):
    stmt = f'INSERT OR REPLACE INTO {d["table"]} ({", ".join(d["data"].keys())}) VALUES ({", ".join(["?"] * len(d["data"]))})'
    db.execute(stmt, tuple(d["data"].values()))

db = sqlite3.connect(args.db)
data_source = args.file.open() if args.file else sys.stdin
if args.lines:
    for line in data_source:
        insert(db, json.loads(line))
else:
    data = json.load(data_source)
    if isinstance(data, dict):
        data = [data]

    for d in data:
        insert(db, d)

db.commit()
