#!/usr/bin/env python3

"""
Determines which files on remote aren't tracked by local nor ignored.
Useful when deploying with tar without removing old files.

Usage: python remote_added.py <host>:<remote_dir>
"""
import sys
from subprocess import run as run_

run = lambda cmd, **kwargs: run_(cmd, capture_output=True, encoding='utf-8', **kwargs)

host, remote_dir = sys.argv[1].split(':')
git_files = run(['git', 'ls-files']).stdout[:-1].split('\n')
remote_files = run(f'ssh {host} "find {remote_dir} -type f"', shell=True)
remote_files = remote_files.stdout[:-1].replace(f'{remote_dir.rstrip("/")}/', '').split('\n')

untracked_files = sorted(set(remote_files) - set(git_files))
if untracked_files:
    check_result = run(['git', 'check-ignore'] + untracked_files).stdout.strip()
    ignored_files = set(check_result.split('\n')) if check_result else set()

    for file in untracked_files:
        if file not in ignored_files:
            print(file)
