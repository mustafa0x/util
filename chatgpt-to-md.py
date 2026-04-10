#!/usr/bin/env python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Entry:
    node_id: str
    role: str
    author_name: str | None
    content_type: str
    created_at: float | None
    hidden: bool
    body: str
    language: str | None
    attachments: list[dict]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Convert a ChatGPT conversation export JSON into Markdown.'
    )
    parser.add_argument(
        'input_path',
        type=Path,
        nargs='?',
        help='Path to the exported conversation JSON (reads from stdin when omitted)',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        help='Output Markdown path (defaults to the input path with a .md suffix)',
    )
    parser.add_argument(
        '--include-hidden',
        action='store_true',
        help='Include visually hidden system/model/context messages',
    )
    parser.add_argument(
        '--include-reasoning',
        action='store_true',
        help='Include reasoning recap and thoughts entries',
    )
    parser.add_argument(
        '--artifacts',
        choices=['omit', 'include'],
        default='omit',
        help='Omit or include internal code/tool execution artifacts',
    )
    parser.add_argument(
        '--assistant-text',
        choices=['final', 'all'],
        default='final',
        help='Keep only the final assistant text reply per turn or all assistant text entries',
    )
    parser.add_argument(
        '--user-messages',
        choices=['include', 'omit'],
        default='include',
        help='Include or omit user messages in the transcript',
    )
    parser.add_argument(
        '--timestamps',
        choices=['omit', 'include'],
        default='omit',
        help='Omit or include timestamps in the transcript',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export, source_label = load_export(args.input_path)
    output_path = resolve_output_path(args.output, args.input_path, export)
    node_ids = get_active_path(export)
    entries = build_entries(
        export,
        node_ids,
        args.include_hidden,
        args.include_reasoning,
        args.artifacts,
    )
    markdown = render_markdown(
        export,
        source_label,
        entries,
        args.include_hidden,
        args.include_reasoning,
        args.artifacts,
        args.assistant_text,
        args.user_messages,
        args.timestamps,
    )
    output_path.write_text(markdown)
    print(f'Wrote {output_path}')


def load_export(input_path: Path | None) -> tuple[dict, str]:
    if input_path is not None:
        return json.loads(input_path.read_text()), str(input_path)

    if sys.stdin.isatty():
        raise SystemExit('No input path provided and stdin is empty. Pipe JSON in, e.g. `pbpaste | ...`')

    raw = sys.stdin.read().strip()
    if not raw:
        raise SystemExit('No JSON content received on stdin')

    return json.loads(raw), 'stdin'


def resolve_output_path(output: Path | None, input_path: Path | None, export: dict) -> Path:
    if output is not None:
        return output.expanduser()

    if input_path is not None:
        return input_path.with_suffix('.md')

    downloads = Path.home() / 'Downloads'
    title = str(export.get('title') or '')
    slug = slugify_title(title) or 'conversation'
    return downloads / f'{slug}.md'


def slugify_title(title: str) -> str:
    normalized = unicodedata.normalize('NFKD', title)
    ascii_only = normalized.encode('ascii', 'ignore').decode().lower()
    slug = re.sub(r'[^a-z0-9]+', '-', ascii_only).strip('-')
    return re.sub(r'-{2,}', '-', slug)


def get_active_path(export: dict) -> list[str]:
    mapping = export.get('mapping') or {}
    current_node = export.get('current_node')
    if current_node and current_node in mapping:
        return walk_to_root(mapping, current_node)

    leaves = [node_id for node_id, node in mapping.items() if not node.get('children')]
    if not leaves:
        raise ValueError('Conversation export has no leaf nodes')

    best_leaf = max(
        leaves,
        key=lambda node_id: (mapping[node_id].get('message') or {}).get('create_time') or 0,
    )
    return walk_to_root(mapping, best_leaf)


def walk_to_root(mapping: dict, node_id: str) -> list[str]:
    node_ids: list[str] = []
    current = node_id
    while current:
        node_ids.append(current)
        current = (mapping[current] or {}).get('parent')
    node_ids.reverse()
    return node_ids


def build_entries(
    export: dict,
    node_ids: list[str],
    include_hidden: bool,
    include_reasoning: bool,
    artifact_mode: str,
) -> list[Entry]:
    mapping = export.get('mapping') or {}
    entries: list[Entry] = []

    for node_id in node_ids:
        node = mapping[node_id]
        message = node.get('message')
        if not message:
            continue

        content = message.get('content') or {}
        metadata = message.get('metadata') or {}
        content_type = content.get('content_type') or 'unknown'
        hidden = bool(metadata.get('is_visually_hidden_from_conversation'))

        if hidden and not include_hidden:
            continue
        if content_type in {'thoughts', 'reasoning_recap'} and not include_reasoning:
            continue
        if artifact_mode == 'omit' and is_artifact_content_type(content_type):
            continue

        entry = Entry(
            node_id=node_id,
            role=(message.get('author') or {}).get('role') or 'unknown',
            author_name=(message.get('author') or {}).get('name'),
            content_type=content_type,
            created_at=message.get('create_time'),
            hidden=hidden,
            body=render_content_body(content, metadata),
            language=detect_language(content_type, content),
            attachments=metadata.get('attachments') or [],
        )

        if should_skip(entry):
            continue

        entries.append(entry)

    return entries


def render_content_body(content: dict, metadata: dict) -> str:
    content_type = content.get('content_type')

    if content_type == 'text':
        return join_parts(content.get('parts') or [])

    if content_type == 'user_editable_context':
        sections: list[str] = []
        user_profile = (content.get('user_profile') or '').strip()
        user_instructions = (content.get('user_instructions') or '').strip()
        if user_profile:
            sections.append('#### User Profile\n\n' + user_profile)
        if user_instructions:
            sections.append('#### User Instructions\n\n' + user_instructions)
        return '\n\n'.join(sections)

    if content_type == 'model_editable_context':
        sections: list[str] = []
        model_set_context = (content.get('model_set_context') or '').strip()
        if model_set_context:
            sections.append('#### Model Context\n\n' + model_set_context)
        repository = content.get('repository')
        if repository:
            sections.append('#### Repository\n\n' + json.dumps(repository, indent=2, ensure_ascii=False))
        repo_summary = content.get('repo_summary')
        if repo_summary:
            sections.append('#### Repo Summary\n\n' + json.dumps(repo_summary, indent=2, ensure_ascii=False))
        structured_context = content.get('structured_context')
        if structured_context:
            sections.append(
                '#### Structured Context\n\n'
                + json.dumps(structured_context, indent=2, ensure_ascii=False)
            )
        return '\n\n'.join(sections)

    if content_type == 'code':
        return (content.get('text') or '').rstrip()

    if content_type == 'execution_output':
        return (content.get('text') or '').rstrip()

    if content_type == 'tether_browsing_display':
        lines: list[str] = []
        command = metadata.get('command')
        if command:
            lines.append(f'Command: {command}')
        summary = (content.get('summary') or '').strip()
        result = (content.get('result') or '').strip()
        tether_id = content.get('tether_id')
        assets = content.get('assets')
        if summary:
            lines.append('')
            lines.append('Summary:')
            lines.append(summary)
        if result:
            lines.append('')
            lines.append('Result:')
            lines.append(result)
        if tether_id:
            lines.append('')
            lines.append(f'Tether ID: {tether_id}')
        if assets:
            lines.append('')
            lines.append('Assets:')
            lines.append(json.dumps(assets, indent=2, ensure_ascii=False))
        return '\n'.join(lines).strip()

    if content_type == 'thoughts':
        blocks: list[str] = []
        for index, thought in enumerate(content.get('thoughts') or [], start=1):
            parts: list[str] = []
            summary = (thought.get('summary') or '').strip()
            body = (thought.get('content') or '').strip()
            if summary:
                parts.append(f'#### Thought {index}: {summary}')
            if body:
                parts.append(body)
            if parts:
                blocks.append('\n\n'.join(parts))
        return '\n\n'.join(blocks)

    if content_type == 'reasoning_recap':
        return (content.get('content') or '').strip()

    return json.dumps(content, indent=2, ensure_ascii=False)


def should_skip(entry: Entry) -> bool:
    if entry.attachments:
        return False
    return not entry.body.strip()


def detect_language(content_type: str, content: dict) -> str | None:
    if content_type == 'code':
        language = (content.get('language') or '').strip()
        return '' if language == 'unknown' else language
    if content_type in {'execution_output', 'tether_browsing_display', 'model_editable_context'}:
        return 'text'
    return None


def render_markdown(
    export: dict,
    source_label: str,
    entries: list[Entry],
    include_hidden: bool,
    include_reasoning: bool,
    artifact_mode: str,
    assistant_text_mode: str,
    user_message_mode: str,
    timestamp_mode: str,
) -> str:
    lines: list[str] = []
    title = export.get('title') or 'Conversation'
    lines.append(f'# {title}')
    lines.append('')
    lines.append('- Source JSON: `' + source_label + '`')
    lines.append('- Conversation ID: `' + str(export.get('conversation_id') or 'unknown') + '`')
    if export.get('default_model_slug'):
        lines.append(f"- Default model: `{export['default_model_slug']}`")
    github_repos = collect_selected_github_repos(export)
    if github_repos:
        lines.append('- GitHub repos: ' + ', '.join(f'`{repo}`' for repo in github_repos))
    if timestamp_mode == 'include' and export.get('create_time'):
        lines.append(f"- Created: {format_timestamp(export['create_time'])}")
    if timestamp_mode == 'include' and export.get('update_time'):
        lines.append(f"- Updated: {format_timestamp(export['update_time'])}")
    lines.append(f'- Included hidden messages: `{"yes" if include_hidden else "no"}`')
    lines.append(f'- Included reasoning: `{"yes" if include_reasoning else "no"}`')
    lines.append(f'- Artifact mode: `{artifact_mode}`')
    lines.append(f'- Assistant text mode: `{assistant_text_mode}`')
    lines.append(f'- User messages: `{user_message_mode}`')
    lines.append(f'- Timestamp mode: `{timestamp_mode}`')
    lines.append('')

    prelude, turns = split_turns(entries)
    prelude = prune_stream_fragments(prelude)
    turns = [
        normalize_turn_entries(
            prune_stream_fragments(turn_entries),
            assistant_text_mode,
            user_message_mode,
        )
        for turn_entries in turns
    ]

    if prelude:
        lines.append('## Context')
        lines.append('')
        lines.extend(render_entry_block_list(prelude, timestamp_mode))

    for index, turn_entries in enumerate(turns, start=1):
        lines.append(f'## Turn {index}')
        lines.append('')
        lines.extend(render_entry_block_list(turn_entries, timestamp_mode))

    return '\n'.join(lines).rstrip() + '\n'


def split_turns(entries: list[Entry]) -> tuple[list[Entry], list[list[Entry]]]:
    prelude: list[Entry] = []
    turns: list[list[Entry]] = []
    current_turn: list[Entry] | None = None

    for entry in entries:
        if entry.role == 'user' and entry.content_type != 'user_editable_context':
            if current_turn:
                turns.append(current_turn)
            current_turn = [entry]
            continue

        if current_turn is None:
            prelude.append(entry)
            continue

        current_turn.append(entry)

    if current_turn:
        turns.append(current_turn)

    return prelude, turns


def render_entry_block_list(entries: list[Entry], timestamp_mode: str) -> list[str]:
    lines: list[str] = []
    counters: dict[tuple[str, str], int] = {}

    for entry in entries:
        key = (entry.role, entry.content_type)
        counters[key] = counters.get(key, 0) + 1
        lines.extend(render_entry(entry, counters[key], timestamp_mode))

    return lines


def collapse_assistant_text_entries(entries: list[Entry], assistant_text_mode: str) -> list[Entry]:
    if assistant_text_mode == 'all':
        return entries

    final_assistant_text_index: int | None = None
    for index, entry in enumerate(entries):
        if entry.role == 'assistant' and entry.content_type == 'text':
            final_assistant_text_index = index

    if final_assistant_text_index is None:
        return entries

    filtered: list[Entry] = []
    for index, entry in enumerate(entries):
        if entry.role == 'assistant' and entry.content_type == 'text' and index != final_assistant_text_index:
            continue
        filtered.append(entry)

    return filtered


def normalize_turn_entries(
    entries: list[Entry],
    assistant_text_mode: str,
    user_message_mode: str,
) -> list[Entry]:
    filtered = collapse_assistant_text_entries(entries, assistant_text_mode)
    if user_message_mode == 'omit':
        filtered = [entry for entry in filtered if entry.role != 'user']
    return filtered


def prune_stream_fragments(entries: list[Entry]) -> list[Entry]:
    filtered: list[Entry] = []

    for index, entry in enumerate(entries):
        if not is_superseded_text_fragment(entry, entries[index + 1 :]):
            filtered.append(entry)

    return filtered


def is_superseded_text_fragment(entry: Entry, later_entries: list[Entry]) -> bool:
    if entry.content_type != 'text':
        return False

    current_text = entry.body.strip()
    if not current_text:
        return True

    later_texts = [
        later.body.strip()
        for later in later_entries
        if later.role == entry.role and later.content_type == 'text' and later.body.strip()
    ]
    if not later_texts:
        return False

    if len(current_text) <= 3:
        return True

    if '\n' in current_text or len(current_text) > 200:
        return False

    return any(later_text.startswith(current_text) and later_text != current_text for later_text in later_texts)


def render_entry(entry: Entry, index: int, timestamp_mode: str) -> list[str]:
    if entry.content_type == 'text':
        return render_text_entry(entry, timestamp_mode)

    if entry.content_type == 'code':
        return render_details_entry(entry, f'{role_label(entry)} code {index}', timestamp_mode)

    if entry.content_type == 'execution_output':
        return render_details_entry(entry, f'{role_label(entry)} output {index}', timestamp_mode)

    if entry.content_type == 'tether_browsing_display':
        return render_details_entry(entry, f'{role_label(entry)} browsing {index}', timestamp_mode)

    if entry.content_type == 'user_editable_context':
        return render_details_entry(entry, 'User context', timestamp_mode, force_open=True)

    if entry.content_type == 'model_editable_context':
        return render_details_entry(entry, 'Model context', timestamp_mode)

    if entry.content_type in {'thoughts', 'reasoning_recap'}:
        return render_details_entry(entry, f'{role_label(entry)} reasoning {index}', timestamp_mode)

    return render_details_entry(entry, f'{role_label(entry)} {entry.content_type} {index}', timestamp_mode)


def render_text_entry(entry: Entry, timestamp_mode: str) -> list[str]:
    heading = f'### {role_label(entry)}'
    if timestamp_mode == 'include' and entry.created_at:
        heading += f' · {format_timestamp(entry.created_at)}'

    lines = [heading, '']
    if entry.body:
        lines.append(entry.body)
        lines.append('')

    lines.extend(render_attachment_lines(entry.attachments))
    if entry.attachments:
        lines.append('')

    return lines


def render_details_entry(
    entry: Entry,
    summary: str,
    timestamp_mode: str,
    force_open: bool = False,
) -> list[str]:
    if timestamp_mode == 'include' and entry.created_at:
        summary += f' · {format_timestamp(entry.created_at)}'

    open_attr = ' open' if force_open else ''
    lines = [f'<details{open_attr}>', f'<summary>{summary}</summary>', '']

    if entry.body:
        lines.append(fenced_block(entry.body, entry.language))
        lines.append('')

    lines.extend(render_attachment_lines(entry.attachments))
    if entry.attachments:
        lines.append('')

    lines.append('</details>')
    lines.append('')
    return lines


def render_attachment_lines(attachments: list[dict]) -> list[str]:
    if not attachments:
        return []

    names = [attachment.get('name') or attachment.get('id') or 'attachment' for attachment in attachments]
    return ['- Attachments: ' + ', '.join(f'`{name}`' for name in names)]


def role_label(entry: Entry) -> str:
    if entry.role == 'tool' and entry.author_name:
        return f'Tool `{entry.author_name}`'
    return entry.role.replace('_', ' ').title()


def join_parts(parts: list) -> str:
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    return '\n\n'.join(cleaned)


def collect_selected_github_repos(export: dict) -> list[str]:
    repos: list[str] = []
    seen: set[str] = set()

    for node_id in get_active_path(export):
        message = (export.get('mapping') or {}).get(node_id, {}).get('message') or {}
        metadata = message.get('metadata') or {}
        for repo in metadata.get('selected_github_repos') or []:
            if repo not in seen:
                seen.add(repo)
                repos.append(repo)

    return repos


def is_artifact_content_type(content_type: str) -> bool:
    return content_type in {'code', 'execution_output', 'tether_browsing_display'}


def format_timestamp(timestamp: float) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')


def fenced_block(text: str, language: str | None) -> str:
    fence_size = max(3, longest_backtick_run(text) + 1)
    fence = '`' * fence_size
    info_string = language or ''
    return f'{fence}{info_string}\n{text}\n{fence}'


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
