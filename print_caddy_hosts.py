"""
List every host served by Caddy, grouped by domain.

• Main domain printed in bold.  If there are no sub-domains, that single line is all you see.
• 🔒 when the server listens on :443
• ➡️ plus port list whenever the route contains reverse-proxy upstreams.
• Works with either Caddy JSON layout (`servers` at top level or under apps→http).
"""

from __future__ import annotations
import argparse, json, sys
from collections import defaultdict
from urllib.error import URLError
from urllib.request import urlopen

LOCK, UNLOCK, ARROW = '🔒', '🔓', '➡️'
BOLD = '\033[1m{}\033[0m'  # ANSI bold wrapper


# ############################################################
# ######################### helpers ##########################
# ############################################################
def fetch_cfg(url: str) -> dict:
    with urlopen(url) as r:
        return json.load(r)


def servers(cfg: dict) -> dict:
    if 'servers' in cfg:  # output of `caddy adapt …`
        return cfg['servers']
    return cfg.get('apps', {}).get('http', {}).get('servers', {})


def host_list(route: dict) -> list[str]:
    m = route.get('match', [])
    if isinstance(m, dict):
        m = [m]
    return [h for matcher in m for h in matcher.get('host', [])]


def rp_ports(node) -> set[str]:
    ports = set()
    if isinstance(node, dict):
        if node.get('handler') == 'reverse_proxy':
            ports |= {u['dial'].rsplit(':', 1)[-1] for u in node.get('upstreams', []) if ':' in u.get('dial', '')}
        for v in node.values():
            ports |= rp_ports(v)
    elif isinstance(node, list):
        for v in node:
            ports |= rp_ports(v)
    return ports


def split_host(host: str) -> tuple[str, str]:
    parts = host.split('.')
    if len(parts) < 2:
        return host, ''
    return '.'.join(parts[-2:]), '.'.join(parts[:-2])  # domain, subdomain


# ############################################################
# ######################### main #############################
# ############################################################
ap = argparse.ArgumentParser(description='List Caddy hosts')
ap.add_argument(
    '--url', default='http://localhost:2019/config/', help='Caddy admin API endpoint (default: %(default)s)'
)
args = ap.parse_args()

try:
    cfg = fetch_cfg(args.url)
except URLError as e:
    sys.exit(f'Cannot reach {args.url}: {e.reason}')

grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

for srv in servers(cfg).values():
    has_tls = any(':443' in l or l.endswith('443') for l in srv.get('listen', []))
    lock = LOCK if has_tls else '  '

    for route in srv.get('routes', []):
        ports = sorted(rp_ports(route))
        port_info = f' {ARROW} {", ".join(ports)}' if ports else ''
        for host in host_list(route):
            domain, sub = split_host(host)
            grouped[domain].append((sub or '@', lock, port_info))

if not grouped:
    sys.exit('No hosts found – is this a Caddy HTTP config?')

for domain in sorted(grouped):
    entries = grouped[domain]

    # Is there a bare-domain (“@”) entry?
    bare = next((e for e in entries if e[0] == '@'), None)

    # Header line: always printed, always bold
    if bare:
        lock, port = bare[1], bare[2]
        print(f'{BOLD.format(domain)} {lock}{port}')
    else:
        print(BOLD.format(domain))

    # Sub-domains: print only if at least one real sub exists
    subs = [e for e in entries if e[0] != '@']
    if subs:
        for label, lock, port in sorted(subs, key=lambda x: x[0]):
            print(f'  {lock} {label}{port}')
