#!/usr/bin/env python3

"""
Determines which files on remote aren't tracked by local nor ignored.
Useful when deploying with tar without removing old files.

Usage: python remote_added.py <host> <remote_dir>
"""
from subprocess import run as run_
import sys

run = lambda cmd, **kwargs: run_(cmd, capture_output=True, encoding='utf-8', **kwargs)

host = sys.argv[1]
remote_dir = sys.argv[2]
git_files = run(['git', 'ls-files']).stdout[:-1].split('\n')
remote_files = run(f'ssh {host} "find {remote_dir} -type f"', shell=True)
remote_files = remote_files.stdout[:-1].replace(f'{remote_dir.rstrip("/")}/', '').split('\n')

for file in sorted(set(remote_files) - set(git_files)):
    if not run(['git', 'check-ignore', file]).stdout:
        print(file)
