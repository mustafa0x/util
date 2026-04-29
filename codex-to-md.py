#!/usr/bin/env python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Message:
    role: str
    text: str
    timestamp: str | None
    phase: str | None


@dataclass
class ToolCall:
    call_id: str | None
    name: str
    arguments: str
    timestamp: str | None
    output: str | None = None


@dataclass
class Turn:
    user_messages: list[Message] = field(default_factory=list)
    assistant_messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Session:
    source_label: str
    source_path: Path | None
    meta: dict
    turns: list[Turn]
    prelude: list[Message]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Convert a Codex session JSONL into clean Markdown.')
    parser.add_argument(
        'input',
        nargs='?',
        help='Path to a Codex rollout JSONL or a session id to find under ~/.codex/sessions (reads from stdin when omitted)',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        help='Output Markdown path (default: resolved JSONL path with .md suffix, or ~/Downloads/codex-session.md for stdin)',
    )
    parser.add_argument(
        '--assistant-messages',
        choices=['final', 'all'],
        default='final',
        help='Keep only the final assistant reply per turn or every visible assistant message (default: final)',
    )
    parser.add_argument(
        '--developer',
        choices=['omit', 'include'],
        default='omit',
        help='Omit or include developer/system instruction messages (default: omit)',
    )
    parser.add_argument(
        '--context',
        choices=['omit', 'include'],
        default='omit',
        help='Omit or include Codex-injected AGENTS/skill/environment context blocks (default: omit)',
    )
    parser.add_argument(
        '--tools',
        choices=['omit', 'summary', 'full'],
        default='omit',
        help='Omit tools, include compact command summaries, or include full tool output (default: omit)',
    )
    parser.add_argument(
        '--timestamps',
        choices=['omit', 'include'],
        default='omit',
        help='Omit or include timestamps on message headings (default: omit)',
    )
    parser.add_argument(
        '--meta',
        choices=['minimal', 'full'],
        default='minimal',
        help='Write a short session header or full session_meta JSON (default: minimal)',
    )
    parser.add_argument(
        '--reveal',
        dest='reveal',
        action='store_true',
        help='Reveal the output file in Finder using `open -R` (default: on)',
    )
    parser.add_argument(
        '--no-reveal',
        dest='reveal',
        action='store_false',
        help='Do not reveal the output file in Finder',
    )
    parser.set_defaults(reveal=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session = load_session(args.input)
    output_path = resolve_output_path(args.output, session.source_path)
    markdown = render_markdown(
        session,
        args.assistant_messages,
        args.developer,
        args.context,
        args.tools,
        args.timestamps,
        args.meta,
    )
    output_path.write_text(markdown)
    print(f'Wrote {output_path}')
    if args.reveal:
        reveal_in_finder(output_path)


def load_session(input_value: str | None) -> Session:
    if input_value is not None:
        input_path = resolve_input_path(input_value)
        return parse_jsonl(input_path.read_text().splitlines(), str(input_path), input_path)

    if sys.stdin.isatty():
        raise SystemExit('No input path provided and stdin is empty. Pipe JSONL in, e.g. `pbpaste | ...`')

    raw = sys.stdin.read().splitlines()
    if not raw:
        raise SystemExit('No JSONL content received on stdin')

    return parse_jsonl(raw, 'stdin', None)


def resolve_input_path(input_value: str) -> Path:
    path = Path(input_value).expanduser()
    if path.exists():
        if not path.is_file():
            raise SystemExit(f'Input path is not a file: {path}')
        return path

    if looks_like_path(input_value):
        raise SystemExit(f'Input path not found: {path}')

    return find_session_jsonl(input_value)


def looks_like_path(input_value: str) -> bool:
    return (
        input_value.endswith('.jsonl')
        or input_value.startswith(('.', '~'))
        or '/' in input_value
        or '\\' in input_value
    )


def find_session_jsonl(session_id: str) -> Path:
    if not session_id:
        raise SystemExit('Session id is empty')

    sessions_root = Path.home() / '.codex' / 'sessions'
    if not sessions_root.exists():
        raise SystemExit(f'Codex sessions directory not found: {sessions_root}')

    paths = list(sessions_root.rglob('*.jsonl'))
    matches = sorted(path for path in paths if session_id in path.name)
    if not matches:
        matches = sorted(path for path in paths if session_meta_id_matches(path, session_id))
    if not matches:
        raise SystemExit(f'No Codex session JSONL found for `{session_id}` under {sessions_root}')
    if len(matches) > 1:
        options = '\n'.join(f'- {path}' for path in matches[:20])
        more = f'\n... and {len(matches) - 20} more' if len(matches) > 20 else ''
        raise SystemExit(f'Codex session id `{session_id}` matched multiple JSONL files:\n{options}{more}')
    return matches[0]


def session_meta_id_matches(path: Path, session_id: str) -> bool:
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                return False
            if item.get('type') != 'session_meta':
                return False
            meta_id = str((item.get('payload') or {}).get('id') or '')
            return meta_id == session_id or meta_id.startswith(session_id)
    return False


def parse_jsonl(lines: list[str], source_label: str, source_path: Path | None) -> Session:
    meta: dict = {}
    turns: list[Turn] = []
    current_turn: Turn | None = None
    prelude: list[Message] = []
    calls_by_id: dict[str, ToolCall] = {}

    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        if item.get('type') == 'session_meta':
            meta = item.get('payload') or {}
            continue

        if item.get('type') != 'response_item':
            continue

        payload = item.get('payload') or {}
        payload_type = payload.get('type')

        if payload_type == 'message':
            message = extract_message(payload, item.get('timestamp'))
            if not message:
                continue
            if message.role == 'user':
                if is_injected_context_message(message):
                    if current_turn is None:
                        prelude.append(message)
                    else:
                        current_turn.user_messages.append(message)
                    continue
                if current_turn and not turn_is_empty(current_turn):
                    turns.append(current_turn)
                current_turn = Turn(user_messages=[message])
            elif current_turn is None:
                prelude.append(message)
            else:
                current_turn.assistant_messages.append(message)
            continue

        if payload_type == 'function_call' and current_turn is not None:
            call = ToolCall(
                call_id=payload.get('call_id'),
                name=payload.get('name') or 'tool',
                arguments=payload.get('arguments') or '',
                timestamp=item.get('timestamp'),
            )
            current_turn.tool_calls.append(call)
            if call.call_id:
                calls_by_id[call.call_id] = call
            continue

        if payload_type == 'function_call_output':
            call_id = payload.get('call_id')
            if call_id in calls_by_id:
                calls_by_id[call_id].output = payload.get('output') or ''

    if current_turn and not turn_is_empty(current_turn):
        turns.append(current_turn)

    return Session(
        source_label=source_label,
        source_path=source_path,
        meta=meta,
        turns=turns,
        prelude=prelude,
    )


def extract_message(payload: dict, fallback_timestamp: str | None) -> Message | None:
    role = payload.get('role') or 'unknown'
    parts: list[str] = []

    for part in payload.get('content') or []:
        part_type = part.get('type')
        if part_type in {'input_text', 'output_text'}:
            text = (part.get('text') or '').strip()
            if text:
                parts.append(text)
        elif part_type in {'input_image', 'local_image'}:
            label = part.get('path') or part.get('image_url') or 'image'
            parts.append(f'[{part_type}: {label}]')

    text = '\n\n'.join(parts).strip()
    if not text:
        return None

    return Message(
        role=role,
        text=text,
        timestamp=payload.get('timestamp') or fallback_timestamp,
        phase=payload.get('phase'),
    )


def turn_is_empty(turn: Turn) -> bool:
    return not turn.user_messages and not turn.assistant_messages and not turn.tool_calls


def resolve_output_path(output: Path | None, source_path: Path | None) -> Path:
    if output is not None:
        return output.expanduser()
    if source_path is not None:
        return source_path.with_suffix('.md')
    return Path.home() / 'Downloads' / 'codex-session.md'


def reveal_in_finder(path: Path) -> None:
    try:
        subprocess.run(
            ['open', '-R', str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def render_markdown(
    session: Session,
    assistant_mode: str,
    developer_mode: str,
    context_mode: str,
    tool_mode: str,
    timestamp_mode: str,
    meta_mode: str,
) -> str:
    lines: list[str] = ['# Codex Session', '']
    lines.extend(render_meta(session, meta_mode))
    lines.append('')

    prelude = [
        message
        for message in session.prelude
        if should_include_message(message, developer_mode, context_mode)
    ]
    if prelude:
        lines.append('## Context')
        lines.append('')
        for message in prelude:
            lines.extend(render_message(message, timestamp_mode))

    visible_index = 1
    for turn in session.turns:
        messages = visible_turn_messages(turn, assistant_mode, developer_mode, context_mode)
        if not messages and (tool_mode == 'omit' or not turn.tool_calls):
            continue

        lines.append(f'## Turn {visible_index}')
        lines.append('')
        visible_index += 1
        for message in messages:
            lines.extend(render_message(message, timestamp_mode))
        if tool_mode != 'omit' and turn.tool_calls:
            lines.extend(render_tools(turn.tool_calls, tool_mode))

    return '\n'.join(lines).rstrip() + '\n'


def render_meta(session: Session, meta_mode: str) -> list[str]:
    meta = session.meta
    lines = [f'- Source JSONL: `{session.source_label}`']
    if meta.get('id'):
        lines.append(f"- Session ID: `{meta['id']}`")
    if meta.get('cwd'):
        lines.append(f"- CWD: `{meta['cwd']}`")
    if meta.get('model'):
        lines.append(f"- Model: `{meta['model']}`")
    if meta.get('cli_version'):
        lines.append(f"- CLI version: `{meta['cli_version']}`")
    if meta.get('timestamp'):
        lines.append(f"- Started: {format_timestamp(meta['timestamp'])}")
    if meta_mode == 'full':
        lines.extend(['', '<details>', '<summary>Full session metadata</summary>', ''])
        lines.append(fenced_block(json.dumps(meta, indent=2, ensure_ascii=False), 'json'))
        lines.extend(['', '</details>'])
    return lines


def visible_turn_messages(
    turn: Turn,
    assistant_mode: str,
    developer_mode: str,
    context_mode: str,
) -> list[Message]:
    messages = [
        *[
            message
            for message in turn.user_messages
            if should_include_message(message, developer_mode, context_mode)
        ],
        *assistant_messages(turn.assistant_messages, assistant_mode),
    ]
    return [
        message
        for message in messages
        if should_include_message(message, developer_mode, context_mode)
    ]


def should_include_message(message: Message, developer_mode: str, context_mode: str) -> bool:
    if message.role in {'developer', 'system'} and developer_mode == 'omit':
        return False
    if context_mode == 'omit' and is_injected_context_message(message):
        return False
    return message.role in {'user', 'assistant', 'developer', 'system'}


def is_injected_context_message(message: Message) -> bool:
    text = message.text.lstrip()
    return (
        text.startswith('# AGENTS.md instructions for ')
        or text.startswith('<skill>\n<name>')
        or text.startswith('<skills_instructions>')
        or text.startswith('<environment_context>')
        or text.startswith('<permissions instructions>')
        or text.startswith('<collaboration_mode>')
        or text.startswith('<apps_instructions>')
        or text.startswith('<plugins_instructions>')
        or text.startswith('<turn_aborted>')
    )


def assistant_messages(messages: list[Message], mode: str) -> list[Message]:
    if mode == 'all':
        return messages

    final_messages = [message for message in messages if message.phase == 'final_answer']
    if final_messages:
        return [final_messages[-1]]
    return messages[-1:] if messages else []


def render_message(message: Message, timestamp_mode: str) -> list[str]:
    heading = f'### {role_label(message.role)}'
    if timestamp_mode == 'include' and message.timestamp:
        heading += f' - {format_timestamp(message.timestamp)}'
    return [heading, '', message.text, '']


def render_tools(calls: list[ToolCall], mode: str) -> list[str]:
    lines = ['### Tools', '']
    for index, call in enumerate(calls, start=1):
        title = f'{index}. `{call.name}`'
        if mode == 'summary':
            summary = summarize_call(call)
            lines.append(f'- {title}: {summary}')
            continue

        lines.extend(['<details>', f'<summary>{title}</summary>', ''])
        if call.arguments:
            lines.append('Arguments:')
            lines.append('')
            lines.append(fenced_block(pretty_arguments(call.arguments), 'json'))
            lines.append('')
        if call.output:
            lines.append('Output:')
            lines.append('')
            lines.append(fenced_block(call.output.rstrip(), 'text'))
            lines.append('')
        lines.extend(['</details>', ''])

    if mode == 'summary':
        lines.append('')
    return lines


def summarize_call(call: ToolCall) -> str:
    args = parse_arguments(call.arguments)
    if call.name == 'exec_command' and isinstance(args, dict):
        cmd = one_line(str(args.get('cmd') or ''))
        return f'`{truncate(cmd, 160)}`'
    if call.name == 'write_stdin' and isinstance(args, dict):
        return f"write to session `{args.get('session_id') or 'unknown'}`"
    return truncate(one_line(pretty_arguments(call.arguments)), 180)


def parse_arguments(arguments: str) -> object:
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return arguments


def pretty_arguments(arguments: str) -> str:
    parsed = parse_arguments(arguments)
    if isinstance(parsed, str):
        return parsed
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def one_line(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def truncate(text: str, length: int) -> str:
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + '...'


def role_label(role: str) -> str:
    return role.replace('_', ' ').title()


def format_timestamp(timestamp: str) -> str:
    value = timestamp.rstrip('Z') + '+00:00' if timestamp.endswith('Z') else timestamp
    try:
        return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S UTC')
    except ValueError:
        return timestamp


def fenced_block(text: str, language: str | None) -> str:
    fence_size = max(3, longest_backtick_run(text) + 1)
    fence = '`' * fence_size
    return f'{fence}{language or ""}\n{text}\n{fence}'


def longest_backtick_run(text: str) -> int:
    longest = 0
    current = 0
    for char in text:
        if char == '`':
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


if __name__ == '__main__':
    main()
