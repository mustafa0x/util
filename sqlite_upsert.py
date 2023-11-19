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
parser.add_argument('json', nargs='?', help='The json file to insert. If not provided, stdin is used')
args = parser.parse_args()


#####################################################################
# Main
#####################################################################
db = sqlite3.connect(args.db)
data = json.load(sys.stdin if not args.json else open(args.json))
if isinstance(data, dict):
    data = [data]

for d in data:
    stmt = f'INSERT OR REPLACE INTO {d["table"]} ({", ".join(d["data"].keys())}) VALUES ({", ".join(["?"] * len(d["data"]))})'
    db.execute(stmt, tuple(d["data"].values()))

db.commit()
