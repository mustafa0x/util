#!/usr/bin/env python
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Any

DEFAULT_MATCH_KEYS = ('id', '_id', 'slug', 'key')
SIMPLE_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')
TOKEN_RE = re.compile(r'\s+|\w+|[^\w\s]+', re.UNICODE)
TAG_RE = re.compile(r'<[^>]+>')


@dataclass
class Counts:
    added: int = 0
    modified: int = 0
    deleted: int = 0
    moved: int = 0

    @property
    def total(self) -> int:
        return self.added + self.modified + self.deleted + self.moved

    def add(self, other: 'Counts') -> None:
        self.added += other.added
        self.modified += other.modified
        self.deleted += other.deleted
        self.moved += other.moved


@dataclass
class DiffNode:
    label: str
    path: str
    kind: str
    old: Any = None
    new: Any = None
    children: list['DiffNode'] = field(default_factory=list)
    is_array: bool = False
    match_key: str | None = None
    match_value: Any = None
    old_index: int | None = None
    new_index: int | None = None
    moved: bool = False
    type_changed: bool = False


class DiffBuilder:
    def __init__(
        self,
        *,
        ignore_keys: set[str],
        ignore_paths: set[str],
        match_keys: tuple[str, ...],
    ) -> None:
        self.ignore_keys = ignore_keys
        self.ignore_paths = ignore_paths
        self.match_keys = match_keys

    def build(self, old: Any, new: Any) -> DiffNode | None:
        return self._diff(old, new, label='$', path='$')

    def _diff(self, old: Any, new: Any, *, label: str, path: str) -> DiffNode | None:
        if path in self.ignore_paths:
            return None

        if old == new:
            return None

        if isinstance(old, dict) and isinstance(new, dict):
            return self._diff_dicts(old, new, label=label, path=path)

        if isinstance(old, list) and isinstance(new, list):
            return self._diff_lists(old, new, label=label, path=path)

        return DiffNode(
            label=label,
            path=path,
            kind='modified',
            old=old,
            new=new,
            type_changed=json_type(old) != json_type(new),
        )

    def _diff_dicts(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        *,
        label: str,
        path: str,
    ) -> DiffNode | None:
        children: list[DiffNode] = []
        ordered_keys = list(old.keys()) + [key for key in new.keys() if key not in old]

        for key in ordered_keys:
            if key in self.ignore_keys:
                continue
            child_path = add_key(path, key)
            if child_path in self.ignore_paths:
                continue

            if key not in new:
                children.append(DiffNode(label=key, path=child_path, kind='deleted', old=old[key]))
            elif key not in old:
                children.append(DiffNode(label=key, path=child_path, kind='added', new=new[key]))
            else:
                child = self._diff(old[key], new[key], label=key, path=child_path)
                if child is not None:
                    children.append(child)

        if not children:
            return None

        return DiffNode(label=label, path=path, kind='group', children=children)

    def _diff_lists(self, old: list[Any], new: list[Any], *, label: str, path: str) -> DiffNode | None:
        match_key = find_match_key(old, new, self.match_keys)
        if match_key is not None:
            return self._diff_lists_by_key(old, new, label=label, path=path, match_key=match_key)
        return self._diff_lists_by_position(old, new, label=label, path=path)

    def _diff_lists_by_key(
        self,
        old: list[dict[str, Any]],
        new: list[dict[str, Any]],
        *,
        label: str,
        path: str,
        match_key: str,
    ) -> DiffNode | None:
        old_by_id = {scalar_key(item[match_key]): item for item in old}
        new_by_id = {scalar_key(item[match_key]): item for item in new}
        old_index_by_id = {scalar_key(item[match_key]): index for index, item in enumerate(old)}
        new_index_by_id = {scalar_key(item[match_key]): index for index, item in enumerate(new)}
        raw_value_by_id = {
            **{scalar_key(item[match_key]): item[match_key] for item in old},
            **{scalar_key(item[match_key]): item[match_key] for item in new},
        }

        children: list[DiffNode] = []

        for new_index, item in enumerate(new):
            item_id = scalar_key(item[match_key])
            raw_id = raw_value_by_id[item_id]
            child_path = add_match(path, match_key, raw_id)
            if child_path in self.ignore_paths:
                continue

            label_text = str(new_index + 1)
            if item_id not in old_by_id:
                children.append(
                    DiffNode(
                        label=label_text,
                        path=child_path,
                        kind='added',
                        new=item,
                        is_array=True,
                        match_key=match_key,
                        match_value=raw_id,
                        new_index=new_index,
                    )
                )
                continue

            old_item = old_by_id[item_id]
            old_index = old_index_by_id[item_id]
            moved = old_index != new_index
            child = self._diff(old_item, item, label=label_text, path=child_path)
            if child is None:
                if moved:
                    children.append(
                        DiffNode(
                            label=label_text,
                            path=child_path,
                            kind='moved',
                            old=old_item,
                            new=item,
                            is_array=True,
                            match_key=match_key,
                            match_value=raw_id,
                            old_index=old_index,
                            new_index=new_index,
                            moved=True,
                        )
                    )
                continue

            child.is_array = True
            child.match_key = match_key
            child.match_value = raw_id
            child.old_index = old_index
            child.new_index = new_index
            child.moved = moved
            children.append(child)

        for old_index, item in enumerate(old):
            item_id = scalar_key(item[match_key])
            if item_id in new_by_id:
                continue
            raw_id = raw_value_by_id[item_id]
            child_path = add_match(path, match_key, raw_id)
            if child_path in self.ignore_paths:
                continue
            children.append(
                DiffNode(
                    label=str(old_index + 1),
                    path=child_path,
                    kind='deleted',
                    old=item,
                    is_array=True,
                    match_key=match_key,
                    match_value=raw_id,
                    old_index=old_index,
                )
            )

        if not children:
            return None

        return DiffNode(
            label=label,
            path=path,
            kind='group',
            children=children,
            is_array=True,
            match_key=match_key,
        )

    def _diff_lists_by_position(
        self,
        old: list[Any],
        new: list[Any],
        *,
        label: str,
        path: str,
    ) -> DiffNode | None:
        old_tokens = [fingerprint(item) for item in old]
        new_tokens = [fingerprint(item) for item in new]
        matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
        children: list[DiffNode] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue

            if tag == 'delete':
                for old_index in range(i1, i2):
                    child_path = add_index(path, old_index)
                    if child_path in self.ignore_paths:
                        continue
                    children.append(
                        DiffNode(
                            label=str(old_index + 1),
                            path=child_path,
                            kind='deleted',
                            old=old[old_index],
                            is_array=True,
                            old_index=old_index,
                        )
                    )
                continue

            if tag == 'insert':
                for new_index in range(j1, j2):
                    child_path = add_index(path, new_index)
                    if child_path in self.ignore_paths:
                        continue
                    children.append(
                        DiffNode(
                            label=str(new_index + 1),
                            path=child_path,
                            kind='added',
                            new=new[new_index],
                            is_array=True,
                            new_index=new_index,
                        )
                    )
                continue

            if tag == 'replace':
                overlap = min(i2 - i1, j2 - j1)

                for offset in range(overlap):
                    old_index = i1 + offset
                    new_index = j1 + offset
                    child_path = add_index(path, new_index)
                    if child_path in self.ignore_paths:
                        continue
                    child = self._diff(
                        old[old_index],
                        new[new_index],
                        label=str(new_index + 1),
                        path=child_path,
                    )
                    if child is not None:
                        child.is_array = True
                        child.old_index = old_index
                        child.new_index = new_index
                        children.append(child)

                for old_index in range(i1 + overlap, i2):
                    child_path = add_index(path, old_index)
                    if child_path in self.ignore_paths:
                        continue
                    children.append(
                        DiffNode(
                            label=str(old_index + 1),
                            path=child_path,
                            kind='deleted',
                            old=old[old_index],
                            is_array=True,
                            old_index=old_index,
                        )
                    )

                for new_index in range(j1 + overlap, j2):
                    child_path = add_index(path, new_index)
                    if child_path in self.ignore_paths:
                        continue
                    children.append(
                        DiffNode(
                            label=str(new_index + 1),
                            path=child_path,
                            kind='added',
                            new=new[new_index],
                            is_array=True,
                            new_index=new_index,
                        )
                    )

        if not children:
            return None

        return DiffNode(label=label, path=path, kind='group', children=children, is_array=True)


class HtmlRenderer:
    def __init__(
        self,
        *,
        title: str,
        old_name: str,
        new_name: str,
        counts: Counts,
        strip_html_tags: bool,
        rtl: bool,
    ) -> None:
        self.title = title
        self.old_name = old_name
        self.new_name = new_name
        self.counts = counts
        self.strip_html_tags = strip_html_tags
        self.rtl = rtl
        self._ids = count(1)

    def render_document(self, root: DiffNode | None) -> str:
        split_html = self.render_split(root)
        counts = self.counts
        title = html.escape(self.title)
        old_name = html.escape(self.old_name)
        new_name = html.escape(self.new_name)
        dir_attr = 'rtl' if self.rtl else 'ltr'

        summary = []
        if counts.added:
            summary.append(f'<span class="badge added">+{counts.added} added</span>')
        if counts.modified:
            summary.append(f'<span class="badge modified">~{counts.modified} changed</span>')
        if counts.deleted:
            summary.append(f'<span class="badge deleted">-{counts.deleted} removed</span>')
        if counts.moved:
            summary.append(f'<span class="badge moved">↕ {counts.moved} moved</span>')
        summary_html = ''.join(summary) or '<span class="badge muted">No changes</span>'

        return f'''<!doctype html>
<html lang="en" dir="{dir_attr}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #fafafa;
  --panel: #ffffff;
  --text: #111827;
  --muted: #6b7280;
  --border: #e5e7eb;
  --green-bg: #e6ffe6;
  --green-fg: #166534;
  --red-bg: #ffe6e6;
  --red-fg: #991b1b;
  --yellow-bg: #fef3c7;
  --yellow-fg: #92400e;
  --purple-bg: #ede9fe;
  --purple-fg: #5b21b6;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text); }}
body {{ font: 14px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.container {{ max-width: 1180px; margin: 0 auto; padding: 18px; }}
.card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; }}
.header {{ padding: 16px 18px; }}
.title {{ margin: 0; font-size: 1.15rem; }}
.subtitle {{ margin-top: 6px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }}
.subtitle code {{ background: #f3f4f6; border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }}
.summary-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
.controls {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
.controls button {{
  border: 1px solid var(--border);
  background: #fff;
  color: var(--text);
  padding: 7px 12px;
  border-radius: 999px;
  cursor: pointer;
  font: inherit;
}}
.controls button:hover {{ background: #f9fafb; }}
.diff-card {{ margin-top: 14px; padding: 18px; overflow: auto; }}
.no-diff {{ color: var(--muted); text-align: center; padding: 24px 8px; }}
.badge {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}}
.badge.added {{ background: var(--green-bg); color: var(--green-fg); }}
.badge.deleted {{ background: var(--red-bg); color: var(--red-fg); }}
.badge.modified {{ background: var(--yellow-bg); color: var(--yellow-fg); }}
.badge.moved {{ background: var(--purple-bg); color: var(--purple-fg); }}
.badge.muted {{ background: #f3f4f6; color: #4b5563; }}
dl, dt, dd {{ display: block; }}
dt {{ font-weight: 700; }}
dd {{ margin: 0 0 0 1.5rem; }}
dd + dt {{ margin-top: 0.2rem; }}
.keyline {{ display: flex; align-items: flex-start; gap: 0.5rem; flex-wrap: wrap; }}
.node-key {{ white-space: pre-wrap; word-break: break-word; }}
.node-meta {{ color: var(--muted); font-size: 12px; font-weight: 500; }}
.node-path {{ color: #9ca3af; font-size: 11px; font-weight: 500; display: none; }}
.show-paths .node-path {{ display: inline; }}
.toggle {{
  border: 0;
  background: transparent;
  padding: 0;
  margin: 0;
  width: 1rem;
  min-width: 1rem;
  color: var(--muted);
  cursor: pointer;
  line-height: 1;
  font-size: 0.95rem;
}}
.toggle.spacer {{ visibility: hidden; cursor: default; }}
.node-children.collapsed {{ display: none; }}
.type-added,
ins,
ins .value-json {{
  background: var(--green-bg);
  padding: 0 0.2rem;
}}
.type-deleted,
del,
del .value-json {{
  background: var(--red-bg);
  padding: 0 0.2rem;
}}
ins, del {{
  text-decoration: none;
  transition: background-color 200ms, padding 200ms;
}}
.seg.insert {{ background: var(--green-bg); }}
.seg.delete {{ background: var(--red-bg); }}
.value-json {{
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.45;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: transparent;
}}
.nil {{ color: var(--muted); font-style: italic; }}
.change-note {{ color: var(--muted); font-size: 12px; }}
.split-row {{
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.5rem;
  margin-top: 0.15rem;
}}
.split-col {{
  white-space: pre-wrap;
  word-break: break-word;
  padding: 0 0.2rem;
  border-radius: 2px;
  min-width: 0;
}}
.split-col.left {{ border-inline-end: 1px solid #eee; }}
.empty {{ color: #999; }}
.footer-note {{ margin-top: 14px; color: var(--muted); font-size: 12px; }}
@media (max-width: 860px) {{
  .container {{ padding: 12px; }}
  .split-row {{ grid-template-columns: 1fr; }}
  .split-col.left {{ border-inline-end: 0; border-bottom: 1px solid #eee; }}
}}
</style>
</head>
<body>
  <div class="container">
    <section class="card header">
      <h1 class="title">{title}</h1>
      <div class="subtitle">
        <span><code>{old_name}</code> → <code>{new_name}</code></span>
        <span>Standalone HTML JSON diff</span>
      </div>
      <div class="summary-row">{summary_html}</div>
      <div class="controls">
        <button type="button" id="expand-all">Expand all</button>
        <button type="button" id="collapse-all">Collapse all</button>
        <button type="button" id="toggle-paths">Show paths</button>
      </div>
    </section>

    <section class="card diff-card diff-root">
      {split_html}
      <div class="footer-note">Generated by json_diff_html.py</div>
    </section>
  </div>

<script>
(() => {{
  const root = document.querySelector('.diff-root');
  const html = document.documentElement;

  function toggleNode(button, expanded) {{
    const target = document.getElementById(button.dataset.target);
    if (!target) return;
    target.classList.toggle('collapsed', !expanded);
    button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    button.textContent = expanded ? '▾' : '▸';
  }}

  root.querySelectorAll('.toggle[data-target]').forEach(button => {{
    button.addEventListener('click', () => {{
      const expanded = button.getAttribute('aria-expanded') !== 'true';
      toggleNode(button, expanded);
    }});
  }});

  document.getElementById('expand-all').addEventListener('click', () => {{
    root.querySelectorAll('.toggle[data-target]').forEach(button => toggleNode(button, true));
  }});

  document.getElementById('collapse-all').addEventListener('click', () => {{
    root.querySelectorAll('.toggle[data-target]').forEach(button => toggleNode(button, false));
  }});

  const pathsBtn = document.getElementById('toggle-paths');
  let pathsVisible = false;
  pathsBtn.addEventListener('click', () => {{
    pathsVisible = !pathsVisible;
    html.classList.toggle('show-paths', pathsVisible);
    pathsBtn.textContent = pathsVisible ? 'Hide paths' : 'Show paths';
  }});
}})();
</script>
</body>
</html>
'''

    def render_split(self, root: DiffNode | None) -> str:
        if root is None:
            return '<div class="no-diff">No diff</div>'
        return self._render_split_node(root, root_level=True)

    def _render_split_node(self, node: DiffNode, *, root_level: bool = False) -> str:
        if node.kind == 'group':
            inner = ''.join(self._render_split_entry(child) for child in node.children)
            if root_level:
                return f'<dl class="diff-tree">{inner}</dl>'
            return self._render_group_entry(node, inner)
        return self._render_split_entry(node)

    def _render_group_entry(self, node: DiffNode, inner_html: str) -> str:
        node_id = f'node-{next(self._ids)}'
        counts = count_changes(node)
        toggle = f'<button type="button" class="toggle" data-target="{node_id}" aria-expanded="true">▾</button>'
        badge_counts = Counts(counts.added, counts.modified, counts.deleted, counts.moved)
        if node.moved and badge_counts.moved:
            badge_counts.moved -= 1
        badges = self._render_counts_badges(badge_counts)
        meta = self._render_meta(node)
        move = self._move_badge(node) if node.moved else ''
        label = html.escape(node.label)
        path = html.escape(node.path)
        return (
            f'<dt class="type-node"><div class="keyline">{toggle}<span class="node-key">{label}</span>'
            f'{meta}{move}{badges}<span class="node-path">{path}</span></div></dt>'
            f'<dd class="type-node"><div id="{node_id}" class="node-children"><dl class="diff-tree">{inner_html}</dl></div></dd>'
        )

    def _render_split_entry(self, node: DiffNode) -> str:
        if node.kind == 'group':
            inner = ''.join(self._render_split_entry(child) for child in node.children)
            return self._render_group_entry(node, inner)

        label_row = self._render_label_row(node, has_children=False)
        body = self._render_split_body(node)
        css_kind = css_kind_for(node)
        return f'<dt class="type-{css_kind}">{label_row}</dt><dd class="type-{css_kind}">{body}</dd>'

    def _render_label_row(self, node: DiffNode, *, has_children: bool) -> str:
        toggle = '<span class="toggle spacer">▾</span>' if not has_children else ''
        label = html.escape(node.label)
        meta = self._render_meta(node)
        move = ''
        if node.kind == 'moved' or node.moved:
            move = self._move_badge(node)
        if node.type_changed:
            move += '<span class="badge modified">type changed</span>'
        path = html.escape(node.path)
        return f'<div class="keyline">{toggle}<span class="node-key">{label}</span>{meta}{move}<span class="node-path">{path}</span></div>'

    def _render_split_body(self, node: DiffNode) -> str:
        if node.kind == 'added':
            return split_row('', f'<ins>{format_value_html(node.new, strip_html_tags=self.strip_html_tags)}</ins>')
        if node.kind == 'deleted':
            return split_row(f'<del>{format_value_html(node.old, strip_html_tags=self.strip_html_tags)}</del>', '')
        if node.kind == 'moved':
            return split_row(
                f'<span class="change-note">position {display_index(node.old_index)}</span>',
                f'<span class="change-note">position {display_index(node.new_index)}</span>',
            )
        if node.kind == 'modified':
            if isinstance(node.old, str) and isinstance(node.new, str):
                _, left_html, right_html = diff_string_html(
                    node.old,
                    node.new,
                    strip_html_tags=self.strip_html_tags,
                )
                return split_row(left_html, right_html)
            return split_row(
                f'<del>{format_value_html(node.old, strip_html_tags=self.strip_html_tags)}</del>',
                f'<ins>{format_value_html(node.new, strip_html_tags=self.strip_html_tags)}</ins>',
            )
        return split_row('', '')

    def _render_meta(self, node: DiffNode) -> str:
        parts: list[str] = []
        if node.match_key is not None and node.match_value is not None:
            raw = path_value(node.match_value)
            parts.append(f'<span class="node-meta">{html.escape(node.match_key)}={html.escape(raw)}</span>')
        return ''.join(parts)

    def _render_counts_badges(self, counts: Counts) -> str:
        parts: list[str] = []
        if counts.added:
            parts.append(f'<span class="badge added">+{counts.added}</span>')
        if counts.modified:
            parts.append(f'<span class="badge modified">~{counts.modified}</span>')
        if counts.deleted:
            parts.append(f'<span class="badge deleted">-{counts.deleted}</span>')
        if counts.moved:
            parts.append(f'<span class="badge moved">↕{counts.moved}</span>')
        return ''.join(parts)

    def _move_badge(self, node: DiffNode) -> str:
        if node.old_index is None or node.new_index is None:
            return '<span class="badge moved">moved</span>'
        return f'<span class="badge moved">{display_index(node.old_index)} → {display_index(node.new_index)}</span>'


def json_type(value: Any) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    return type(value).__name__


def load_json_from_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f'File not found: {path}') from exc
    except json.JSONDecodeError as exc:
        where = f'line {exc.lineno}, column {exc.colno}'
        raise SystemExit(f'Invalid JSON in {path}: {exc.msg} ({where})') from exc


def load_json_from_stdin(separator: str) -> tuple[Any, Any]:
    raw = sys.stdin.read()
    parts = raw.split(separator)
    if len(parts) != 2:
        raise SystemExit(
            'When using --input, provide exactly two JSON documents separated by '
            f'{separator!r}.'
        )
    try:
        return json.loads(parts[0].strip()), json.loads(parts[1].strip())
    except json.JSONDecodeError as exc:
        where = f'line {exc.lineno}, column {exc.colno}'
        raise SystemExit(f'Invalid JSON from stdin: {exc.msg} ({where})') from exc


def find_match_key(old: list[Any], new: list[Any], match_keys: tuple[str, ...]) -> str | None:
    items = [*old, *new]
    if not items:
        return None
    if not all(isinstance(item, dict) for item in items):
        return None

    for key in match_keys:
        if not all(key in item for item in items):
            continue
        old_values = [scalar_key(item[key]) for item in old]
        new_values = [scalar_key(item[key]) for item in new]
        if len(set(old_values)) != len(old_values):
            continue
        if len(set(new_values)) != len(new_values):
            continue
        return key
    return None


def scalar_key(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return fingerprint(value)


def fingerprint(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def add_key(path: str, key: str) -> str:
    if SIMPLE_KEY_RE.match(key):
        return f'{path}.{key}'
    return f'{path}[{json.dumps(key, ensure_ascii=False)}]'


def add_index(path: str, index: int) -> str:
    return f'{path}[{index}]'


def add_match(path: str, key: str, value: Any) -> str:
    return f'{path}[{key}={path_value(value)}]'


def path_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def strip_html_text(text: str) -> str:
    s = str(text).replace('\u00a0', ' ')
    s = s.replace('&nbsp;', ' ')
    s = re.sub(r'</p[^>]*>', '\n', s, flags=re.IGNORECASE)
    s = TAG_RE.sub('', s)
    s = html.unescape(s)
    s = re.sub(r'\n+', '\n', s)
    return s


def format_value_html(value: Any, *, strip_html_tags: bool) -> str:
    if isinstance(value, str):
        text = strip_html_text(value) if strip_html_tags else value
        if text == '':
            return '<span class="empty">""</span>'
        return html.escape(text)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return '<span class="nil">nil</span>'
    if isinstance(value, (int, float)):
        return html.escape(json.dumps(value, ensure_ascii=False))

    pretty = json.dumps(value, ensure_ascii=False, indent=2)
    return f'<pre class="value-json">{html.escape(pretty)}</pre>'


def diff_string_html(old: str, new: str, *, strip_html_tags: bool) -> tuple[str, str, str]:
    old_text = strip_html_text(old) if strip_html_tags else str(old)
    new_text = strip_html_text(new) if strip_html_tags else str(new)
    old_tokens = TOKEN_RE.findall(old_text)
    new_tokens = TOKEN_RE.findall(new_text)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)

    inline_parts: list[str] = []
    left_parts: list[str] = []
    right_parts: list[str] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_piece = ''.join(old_tokens[i1:i2])
        new_piece = ''.join(new_tokens[j1:j2])

        if tag == 'equal':
            escaped = html.escape(old_piece)
            inline_parts.append(f'<span class="seg equal">{escaped}</span>')
            left_parts.append(f'<span class="seg equal">{escaped}</span>')
            right_parts.append(f'<span class="seg equal">{escaped}</span>')
        elif tag == 'delete':
            escaped = html.escape(old_piece)
            inline_parts.append(f'<del class="seg delete">{escaped}</del>')
            left_parts.append(f'<span class="seg delete">{escaped}</span>')
        elif tag == 'insert':
            escaped = html.escape(new_piece)
            inline_parts.append(f'<ins class="seg insert">{escaped}</ins>')
            right_parts.append(f'<span class="seg insert">{escaped}</span>')
        elif tag == 'replace':
            escaped_old = html.escape(old_piece)
            escaped_new = html.escape(new_piece)
            inline_parts.append(f'<del class="seg delete">{escaped_old}</del>')
            inline_parts.append(f'<ins class="seg insert">{escaped_new}</ins>')
            left_parts.append(f'<span class="seg delete">{escaped_old}</span>')
            right_parts.append(f'<span class="seg insert">{escaped_new}</span>')

    return ''.join(inline_parts), ''.join(left_parts), ''.join(right_parts)


def split_row(left_html: str, right_html: str) -> str:
    empty = '<span class="empty">—</span>'
    left = left_html or empty
    right = right_html or empty
    return (
        '<div class="split-row">'
        f'<div class="split-col left">{left}</div>'
        f'<div class="split-col right">{right}</div>'
        '</div>'
    )


def css_kind_for(node: DiffNode) -> str:
    if node.kind == 'modified' and node.type_changed:
        return 'modified'
    return node.kind


def count_changes(node: DiffNode | None) -> Counts:
    counts = Counts()
    if node is None:
        return counts

    if node.kind == 'group':
        for child in node.children:
            counts.add(count_changes(child))
    elif node.kind == 'added':
        counts.added += 1
    elif node.kind == 'deleted':
        counts.deleted += 1
    elif node.kind == 'moved':
        counts.moved += 1
    elif node.kind == 'modified':
        counts.modified += 1

    if node.moved and node.kind != 'moved':
        counts.moved += 1

    return counts


def display_index(index: int | None) -> str:
    if index is None:
        return '—'
    return str(index + 1)


def sanitize_name_for_file(name: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', name.strip())
    cleaned = cleaned.strip('._')
    return cleaned or 'json'


def derive_output_path(args: argparse.Namespace) -> Path:
    if args.out:
        return Path(args.out)
    if args.input:
        return Path('json_diff.html')

    old_name = sanitize_name_for_file(Path(args.file_old).stem if args.file_old else 'old')
    new_name = sanitize_name_for_file(Path(args.file_new).stem if args.file_new else 'new')
    return Path(f'{old_name}__vs__{new_name}.diff.html')


def build_title(args: argparse.Namespace) -> tuple[str, str, str]:
    if args.input:
        old_name = 'old'
        new_name = 'new'
    else:
        old_name = Path(args.file_old).name if args.file_old else 'old'
        new_name = Path(args.file_new).name if args.file_new else 'new'

    title = args.title or f'{old_name} → {new_name}'
    return title, old_name, new_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Generate a standalone HTML diff for two JSON files.',
    )
    parser.add_argument('file_old', nargs='?', help='The old JSON file')
    parser.add_argument('file_new', nargs='?', help='The new JSON file')
    parser.add_argument('-o', '--out', help='Write the HTML diff to this file. Use - for stdout.')
    parser.add_argument(
        '-i',
        '--input',
        action='store_true',
        help='Read two JSON documents from stdin, separated by --separator.',
    )
    parser.add_argument(
        '-s',
        '--separator',
        default='\n---\n',
        help='Separator used with --input. Default: newline, ---, newline.',
    )
    parser.add_argument('--rtl', action='store_true', help='Render the HTML document with dir=rtl.')
    parser.add_argument('--open', action='store_true', help='Open the generated HTML in the default browser.')
    parser.add_argument('--title', help='Custom HTML document title.')
    parser.add_argument(
        '--match-key',
        action='append',
        dest='match_keys',
        help='Array object key used to match items across lists. Repeat to add more keys.',
    )
    parser.add_argument(
        '--ignore-key',
        action='append',
        default=[],
        help='Ignore this key everywhere in the JSON tree. Repeatable.',
    )
    parser.add_argument(
        '--ignore-path',
        action='append',
        default=[],
        help='Ignore this exact JSON path. Example: $.meta.updated_at',
    )
    parser.add_argument(
        '--strip-html-tags',
        action='store_true',
        help='Strip HTML tags from string values before diffing and rendering them.',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.input:
        old_data, new_data = load_json_from_stdin(args.separator)
    else:
        if not args.file_old or not args.file_new:
            raise SystemExit('Please provide both JSON files, or use --input.')
        old_data = load_json_from_file(Path(args.file_old))
        new_data = load_json_from_file(Path(args.file_new))

    match_keys = tuple(args.match_keys) if args.match_keys else DEFAULT_MATCH_KEYS
    builder = DiffBuilder(
        ignore_keys=set(args.ignore_key),
        ignore_paths=set(args.ignore_path),
        match_keys=match_keys,
    )
    root = builder.build(old_data, new_data)
    counts = count_changes(root)
    out_path = derive_output_path(args)
    title, old_name, new_name = build_title(args)

    renderer = HtmlRenderer(
        title=title,
        old_name=old_name,
        new_name=new_name,
        counts=counts,
        strip_html_tags=args.strip_html_tags,
        rtl=args.rtl,
    )
    document = renderer.render_document(root)

    if out_path == Path('-'):
        sys.stdout.write(document)
    else:
        out_path.write_text(document)
        print(out_path)
        if args.open:
            try:
                webbrowser.open(out_path.resolve().as_uri())
            except Exception:
                pass

    return 0 if root is None else 1


if __name__ == '__main__':
    raise SystemExit(main())
