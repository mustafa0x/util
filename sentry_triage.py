#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "httpx>=0.27",
#   "rich>=13.7",
# ]
# ///

import argparse
import configparser
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


console = Console()

DEFAULT_BASE_URL = 'https://sentry.nuqayah.com'
DEFAULT_ORG = 'sentry'
DEFAULT_STATS_PERIOD = '14d'
RELEASE_RE = re.compile(r'^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$')


@dataclass
class Config:
    base_url: str
    org: str
    project: str
    project_id: str | None
    bundle_id: str | None
    token: str
    stats_period: str
    json_output: bool
    raw: bool


class SentryClient:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.Client(
            base_url=config.base_url.rstrip('/'),
            headers={
                'Authorization': f'Bearer {config.token}',
                'User-Agent': 'nuqayah-sentry-triage/1.0',
            },
            timeout=30,
            follow_redirects=True,
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(path, params=params)
        if response.status_code == 404:
            return {'_missing': True, '_status': 404}
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = response.text
        if response.is_error:
            raise RuntimeError(f'Sentry API {response.status_code}: {data}')
        return data

    def project_id(self) -> str:
        if self.config.project_id:
            return self.config.project_id

        project = self.get(f'/api/0/projects/{self.config.org}/{self.config.project}/')
        project_id = str(project['id'])
        self.config.project_id = project_id
        return project_id


def package_json(path: Path = Path.cwd()) -> dict[str, Any]:
    current = path.resolve()
    for directory in [current, *current.parents]:
        candidate = directory / 'package.json'
        if candidate.exists():
            return json.loads(candidate.read_text())
    return {}


def token_from_sentryclirc() -> str | None:
    path = Path.home() / '.sentryclirc'
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.read(path)
    if parser.has_option('auth', 'token'):
        return parser.get('auth', 'token').strip()
    return None


def auth_token() -> str:
    token = os.getenv('SENTRY_AUTH_TOKEN') or os.getenv('SENTRY_ACCESS_TOKEN') or token_from_sentryclirc()
    if not token:
        raise SystemExit('Set SENTRY_AUTH_TOKEN, SENTRY_ACCESS_TOKEN, or log in with ~/.sentryclirc.')
    return token


def config_from_args(args: argparse.Namespace) -> Config:
    pkg = package_json()
    pkg_config = pkg.get('config') if isinstance(pkg.get('config'), dict) else {}
    native_dsn = str(pkg_config.get('sentry_native_dsn') or '')
    project_id = getattr(args, 'project_id', None)
    if project_id is None:
        match = re.search(r'/(\d+)(?:\?.*)?$', native_dsn)
        if match:
            project_id = match.group(1)

    return Config(
        base_url=getattr(args, 'base_url', None) or os.getenv('SENTRY_BASE_URL') or DEFAULT_BASE_URL,
        org=getattr(args, 'org', None) or os.getenv('SENTRY_ORG') or DEFAULT_ORG,
        project=getattr(args, 'project', None) or os.getenv('SENTRY_PROJECT') or str(pkg.get('name') or ''),
        project_id=project_id or os.getenv('SENTRY_PROJECT_ID'),
        bundle_id=getattr(args, 'bundle_id', None) or os.getenv('SENTRY_BUNDLE_ID') or pkg_config.get('app_id'),
        token=auth_token(),
        stats_period=getattr(args, 'stats_period', None) or DEFAULT_STATS_PERIOD,
        json_output=bool(getattr(args, 'json', False)),
        raw=bool(getattr(args, 'raw', False)),
    )


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, default=str))


def native_release_alias(version: str, bundle_id: str | None) -> str | None:
    if not bundle_id:
        return None
    match = RELEASE_RE.match(version)
    if not match:
        return None
    version_name = f'{match.group("major")}.{match.group("minor")}'
    build = match.group('patch')
    return f'{bundle_id}@{version_name}+{build}'


def release_candidates(version: str, bundle_id: str | None) -> list[str]:
    candidates = [version]
    native = native_release_alias(version, bundle_id)
    if native and native not in candidates:
        candidates.append(native)
    return candidates


def event_query_for_release(release: str, *, errors_only: bool = False) -> str:
    query = f'release:"{release}"'
    if errors_only:
        query += ' event.type:error'
    return query


def release_detail(client: SentryClient, release: str) -> dict[str, Any]:
    return client.get(f'/api/0/organizations/{client.config.org}/releases/{quote(release, safe="")}/')


def release_files(client: SentryClient, release: str, limit: int = 25) -> list[dict[str, Any]]:
    data = client.get(
        f'/api/0/organizations/{client.config.org}/releases/{quote(release, safe="")}/files/',
        {'per_page': str(limit)},
    )
    return [] if isinstance(data, dict) and data.get('_missing') else data


def issues_for_query(client: SentryClient, query: str, limit: int = 25) -> list[dict[str, Any]]:
    return client.get(
        f'/api/0/projects/{client.config.org}/{client.config.project}/issues/',
        {'query': query, 'statsPeriod': client.config.stats_period, 'limit': str(limit)},
    )


def discover_events(client: SentryClient, query: str, limit: int = 10) -> list[dict[str, Any]]:
    data = client.get(
        f'/api/0/organizations/{client.config.org}/events/',
        {
            'project': client.project_id(),
            'query': query,
            'field': ['id', 'title', 'timestamp', 'release', 'issue.id', 'transaction', 'platform'],
            'sort': '-timestamp',
            'per_page': str(limit),
        },
    )
    return data.get('data', [])


def issue_events(client: SentryClient, issue_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return client.get(f'/api/0/issues/{issue_id}/events/', {'per_page': str(limit)})


def event_detail(client: SentryClient, event_id: str) -> dict[str, Any]:
    return client.get(f'/api/0/projects/{client.config.org}/{client.config.project}/events/{event_id}/')


def resolve_issue_id(client: SentryClient, issue: str) -> str:
    if issue.isdigit():
        return issue
    if '-' in issue:
        short_id = client.get(f'/api/0/organizations/{client.config.org}/shortids/{quote(issue, safe="")}/')
        if not short_id.get('_missing') and short_id.get('groupId'):
            return str(short_id['groupId'])

    issues = issues_for_query(client, issue, limit=5)
    exact = [item for item in issues if item.get('shortId') == issue or item.get('id') == issue]
    if exact:
        return str(exact[0]['id'])
    if len(issues) == 1:
        return str(issues[0]['id'])
    if not issues:
        raise SystemExit(f'No issue matched {issue!r}.')
    raise SystemExit(f'{issue!r} matched multiple issues; use the numeric issue id.')


def compact_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': issue.get('id'),
        'short_id': issue.get('shortId'),
        'title': issue.get('title'),
        'status': issue.get('status'),
        'count': issue.get('count'),
        'users': issue.get('userCount'),
        'first_seen': issue.get('firstSeen'),
        'last_seen': issue.get('lastSeen'),
        'level': issue.get('level'),
        'culprit': issue.get('culprit'),
        'permalink': issue.get('permalink'),
        'metadata': issue.get('metadata'),
    }


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': event.get('id') or event.get('eventID'),
        'title': event.get('title'),
        'timestamp': event.get('timestamp') or event.get('dateCreated'),
        'release': event.get('release'),
        'dist': event.get('dist'),
        'environment': event.get('environment'),
        'platform': event.get('platform'),
        'culprit': event.get('culprit'),
        'issue_id': event.get('issue.id'),
        'transaction': event.get('transaction'),
        'metadata': event.get('metadata'),
    }


def event_tag(event: dict[str, Any], key: str) -> str | None:
    for tag in event.get('tags') or []:
        if tag.get('key') == key:
            return tag.get('value')
    return None


def event_context_summary(event: dict[str, Any]) -> dict[str, Any]:
    contexts = event.get('contexts') or {}
    app = contexts.get('app') or {}
    device = contexts.get('device') or {}
    os_context = contexts.get('os') or {}
    release = event.get('release')
    if isinstance(release, dict):
        release = release.get('version')

    return {
        'id': event.get('eventID') or event.get('id'),
        'title': event.get('title'),
        'time': event.get('dateCreated'),
        'release': release or event_tag(event, 'release'),
        'dist': event.get('dist') or event_tag(event, 'dist'),
        'environment': event.get('environment') or event_tag(event, 'environment'),
        'platform': event.get('platform'),
        'sdk': event.get('sdk'),
        'culprit': event.get('culprit'),
        'app': {
            'id': app.get('app_identifier'),
            'version': app.get('app_version'),
            'build': app.get('app_build'),
            'foreground': app.get('in_foreground'),
        },
        'device': {
            'model': device.get('model'),
            'family': device.get('family'),
            'arch': device.get('arch'),
            'memory_size': device.get('memory_size'),
        },
        'os': {
            'name': os_context.get('name'),
            'version': os_context.get('version'),
            'raw': os_context.get('os'),
        },
    }


def iter_frames(event: dict[str, Any]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for entry in event.get('entries') or []:
        data = entry.get('data') or {}
        if entry.get('type') == 'exception':
            for value in data.get('values') or []:
                stacktrace = value.get('stacktrace') or {}
                frames.extend(stacktrace.get('frames') or [])
        if entry.get('type') == 'threads':
            for value in data.get('values') or []:
                stacktrace = value.get('stacktrace') or {}
                frames.extend(stacktrace.get('frames') or [])
        if entry.get('type') == 'stacktrace':
            frames.extend(data.get('frames') or [])
    return frames


def compact_frame(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        'function': frame.get('function'),
        'filename': frame.get('filename') or frame.get('absPath'),
        'line': frame.get('lineNo'),
        'col': frame.get('colNo'),
        'in_app': frame.get('inApp'),
        'package': frame.get('package'),
    }


def add_issue_table(issues: list[dict[str, Any]], title: str = 'Issues') -> None:
    table = Table(title=title)
    table.add_column('Short ID')
    table.add_column('Group Count', justify='right')
    table.add_column('Users', justify='right')
    table.add_column('Last Seen')
    table.add_column('Title')
    for issue in issues:
        table.add_row(
            str(issue.get('shortId') or issue.get('id') or ''),
            str(issue.get('count') or ''),
            str(issue.get('userCount') or ''),
            str(issue.get('lastSeen') or ''),
            str(issue.get('title') or ''),
        )
    console.print(table)


def add_event_table(events: list[dict[str, Any]], title: str = 'Events') -> None:
    table = Table(title=title)
    table.add_column('Event')
    table.add_column('Time')
    table.add_column('Issue')
    table.add_column('Release')
    table.add_column('Title')
    for event in events:
        table.add_row(
            str(event.get('id') or ''),
            str(event.get('timestamp') or event.get('dateCreated') or ''),
            str(event.get('issue.id') or ''),
            str(event.get('release') or ''),
            str(event.get('title') or ''),
        )
    console.print(table)


def cmd_release(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    require_project(config)
    client = SentryClient(config)
    results = []

    for candidate in release_candidates(args.version, config.bundle_id):
        detail = release_detail(client, candidate)
        files = release_files(client, candidate, args.files)
        issues = issues_for_query(client, f'release:"{candidate}"', args.limit)
        events = discover_events(client, event_query_for_release(candidate), args.events)
        error_events = discover_events(client, event_query_for_release(candidate, errors_only=True), args.events)
        results.append(
            {
                'release': candidate,
                'exists': not detail.get('_missing'),
                'detail': detail,
                'files': files,
                'issues': [compact_issue(issue) for issue in issues],
                'events': [compact_event(event) for event in events],
                'error_events': [compact_event(event) for event in error_events],
            },
        )

    if config.json_output:
        print_json(results)
        return

    for item in results:
        detail = item['detail']
        if item['exists']:
            lines = [
                f'version: {detail.get("version")}',
                f'created: {detail.get("dateCreated")}',
                f'last event: {detail.get("lastEvent")}',
                f'new groups: {detail.get("newGroups")}',
                f'files: {len(item["files"])}',
                f'issues: {len(item["issues"])}',
                f'events: {len(item["events"])}',
            ]
            console.print(Panel('\n'.join(lines), title=f'Release {item["release"]}', expand=False))
        else:
            console.print(Panel('release endpoint returned 404', title=f'Release {item["release"]}', expand=False))

        if item['issues']:
            add_issue_table([restore_issue_keys(issue) for issue in item['issues']], 'Issue groups')
        if item['events']:
            add_event_table([restore_event_keys(event) for event in item['events']], 'Latest events')


def restore_issue_keys(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': issue.get('id'),
        'shortId': issue.get('short_id'),
        'title': issue.get('title'),
        'count': issue.get('count'),
        'userCount': issue.get('users'),
        'lastSeen': issue.get('last_seen'),
    }


def restore_event_keys(event: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': event.get('id'),
        'timestamp': event.get('timestamp'),
        'issue.id': event.get('issue_id'),
        'release': event.get('release'),
        'title': event.get('title'),
    }


def cmd_issue(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    require_project(config)
    client = SentryClient(config)
    issue_id = resolve_issue_id(client, args.issue)
    issue = client.get(f'/api/0/issues/{issue_id}/')
    if issue.get('_missing'):
        raise SystemExit(f'No issue matched {args.issue!r}.')
    events = issue_events(client, issue_id, args.events)
    result = {
        'issue': compact_issue(issue),
        'events': [compact_event(event) for event in events],
    }
    if args.event_details and events:
        result['latest_event'] = event_context_summary(event_detail(client, events[0]['eventID']))
    if config.json_output:
        print_json(result)
        return

    item = result['issue']
    lines = [
        f'{item["short_id"] or item["id"]}: {item["title"]}',
        f'status: {item["status"]}',
        f'count/users: {item["count"]}/{item["users"]}',
        f'first seen: {item["first_seen"]}',
        f'last seen: {item["last_seen"]}',
        f'permalink: {item["permalink"]}',
    ]
    console.print(Panel('\n'.join(lines), title='Issue', expand=False))
    add_event_table([restore_event_keys(event) for event in result['events']], 'Latest events')
    if 'latest_event' in result:
        console.print_json(data=result['latest_event'])


def cmd_event(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    require_project(config)
    client = SentryClient(config)
    event = event_detail(client, args.event)
    if event.get('_missing'):
        raise SystemExit(f'No event matched {args.event!r}.')
    result = event_context_summary(event)
    if args.frames:
        result['frames'] = [compact_frame(frame) for frame in iter_frames(event)]
    if config.raw:
        result = event
    if config.json_output or config.raw:
        print_json(result)
        return

    lines = [
        f'id: {result["id"]}',
        f'time: {result["time"]}',
        f'title: {result["title"]}',
        f'release/dist: {result["release"]}/{result["dist"]}',
        f'environment: {result["environment"]}',
        f'platform: {result["platform"]}',
        f'culprit: {result["culprit"]}',
    ]
    console.print(Panel('\n'.join(lines), title='Event', expand=False))
    console.print_json(data={k: result[k] for k in ['sdk', 'app', 'device', 'os']})
    if args.frames:
        add_frame_table(result['frames'])


def add_frame_table(frames: list[dict[str, Any]]) -> None:
    table = Table(title='Frames')
    table.add_column('In App')
    table.add_column('Function')
    table.add_column('File')
    table.add_column('Line')
    table.add_column('Col')
    for frame in frames:
        table.add_row(
            str(frame.get('in_app')),
            str(frame.get('function') or ''),
            str(frame.get('filename') or ''),
            str(frame.get('line') or ''),
            str(frame.get('col') or ''),
        )
    console.print(table)


def cmd_find(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    require_project(config)
    client = SentryClient(config)
    issues = issues_for_query(client, args.query, args.limit)
    events = discover_events(client, args.query, args.events)
    result = {
        'issues': [compact_issue(issue) for issue in issues],
        'events': [compact_event(event) for event in events],
    }
    if config.json_output:
        print_json(result)
        return
    add_issue_table(issues, 'Matching issue groups')
    add_event_table(events, 'Matching events')


def cmd_sourcemap(args: argparse.Namespace) -> None:
    config = config_from_args(args)
    require_project(config)
    client = SentryClient(config)
    candidates = release_candidates(args.release, config.bundle_id)
    result: dict[str, Any] = {'release_candidates': []}

    for candidate in candidates:
        files = release_files(client, candidate, args.files)
        result['release_candidates'].append(
            {
                'release': candidate,
                'files': [
                    {
                        'name': item.get('name'),
                        'id': item.get('id'),
                        'headers': item.get('headers'),
                        'size': item.get('size'),
                    }
                    for item in files
                ],
            },
        )

    if args.bundle and args.line and args.col:
        result['local_resolve'] = sentry_cli_resolve(args.bundle, args.line, args.col)

    if config.json_output:
        print_json(result)
        return

    for candidate in result['release_candidates']:
        table = Table(title=f'Artifacts for {candidate["release"]}')
        table.add_column('Name')
        table.add_column('Size', justify='right')
        for item in candidate['files']:
            table.add_row(str(item.get('name') or ''), str(item.get('size') or ''))
        console.print(table)

    if 'local_resolve' in result:
        console.print(Panel(result['local_resolve'], title='sentry-cli sourcemaps resolve', expand=False))


def sentry_cli_resolve(bundle: str, line: int, col: int) -> str:
    if not shutil.which('sentry-cli'):
        return 'sentry-cli is not on PATH.'
    command = ['sentry-cli', 'sourcemaps', 'resolve', bundle, str(line), str(col)]
    run = subprocess.run(command, capture_output=True, text=True)
    output = (run.stdout + run.stderr).strip()
    if not output:
        output = f'sentry-cli exited {run.returncode} with no output.'
    return output


def require_project(config: Config) -> None:
    if not config.project:
        raise SystemExit('Could not infer project. Pass --project or run from a repo with package.json name.')


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--base-url', default=argparse.SUPPRESS)
    parser.add_argument('--org', default=argparse.SUPPRESS)
    parser.add_argument('--project', default=argparse.SUPPRESS)
    parser.add_argument('--project-id', default=argparse.SUPPRESS)
    parser.add_argument('--bundle-id', default=argparse.SUPPRESS)
    parser.add_argument('--stats-period', default=argparse.SUPPRESS)
    parser.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    parser.add_argument('--raw', action='store_true', default=argparse.SUPPRESS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Read-only Sentry triage for Nuqayah projects.',
    )
    add_common_args(parser)
    subparsers = parser.add_subparsers(dest='command', required=True)

    release = subparsers.add_parser('release', help='summarize release aliases, issues, and events')
    add_common_args(release)
    release.add_argument('version')
    release.add_argument('--limit', type=int, default=25)
    release.add_argument('--events', type=int, default=10)
    release.add_argument('--files', type=int, default=25)
    release.set_defaults(func=cmd_release)

    issue = subparsers.add_parser('issue', help='summarize an issue group')
    add_common_args(issue)
    issue.add_argument('issue')
    issue.add_argument('--events', type=int, default=5)
    issue.add_argument('--event-details', action='store_true')
    issue.set_defaults(func=cmd_issue)

    event = subparsers.add_parser('event', help='summarize one event')
    add_common_args(event)
    event.add_argument('event')
    event.add_argument('--frames', action='store_true')
    event.set_defaults(func=cmd_event)

    find = subparsers.add_parser('find', help='search recent issues and events')
    add_common_args(find)
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=25)
    find.add_argument('--events', type=int, default=10)
    find.set_defaults(func=cmd_find)

    sourcemap = subparsers.add_parser('sourcemap', help='inspect uploaded artifacts and local source-map resolution')
    add_common_args(sourcemap)
    sourcemap.add_argument('release')
    sourcemap.add_argument('--files', type=int, default=50)
    sourcemap.add_argument('--bundle', help='local generated bundle path for sentry-cli sourcemaps resolve')
    sourcemap.add_argument('--line', type=int)
    sourcemap.add_argument('--col', type=int)
    sourcemap.set_defaults(func=cmd_sourcemap)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except httpx.ConnectError as error:
        raise SystemExit(f'Could not connect to Sentry: {error}') from error
    except httpx.TimeoutException as error:
        raise SystemExit(f'Sentry request timed out: {error}') from error


if __name__ == '__main__':
    main()
