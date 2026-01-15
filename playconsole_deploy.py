#!/usr/bin/env -S uv --quiet run --script
# /// script
# dependencies = [
#   "google-api-python-client>=2.0.0",
#   "google-auth>=2.0.0",
# ]
# ///

import argparse
import json
import sys
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


ANDROIDPUBLISHER_SCOPE = 'https://www.googleapis.com/auth/androidpublisher'


def _load_service_account_json(value: str) -> dict:
    """
    Accepts:
      - "-" to read JSON from stdin
      - a path to a JSON file
      - a raw JSON string
    """
    if value == '-':
        raw = sys.stdin.read()
        return json.loads(raw)

    p = Path(value)
    if p.exists() and p.is_file():
        return json.loads(p.read_text())

    return json.loads(value)


def _parse_release_files(values: list[str]) -> list[Path]:
    files: list[Path] = []
    for v in values:
        for part in v.split(','):
            part = part.strip()
            if not part:
                continue
            p = Path(part)
            if not p.exists():
                raise FileNotFoundError(part)
            files.append(p)

    if not files:
        raise ValueError('No release files provided.')

    return files


def _upload_resumable(request) -> dict:
    # googleapiclient resumable upload loop
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def upload_to_play(
    *,
    service_account_info: dict,
    package_name: str,
    release_files: list[Path],
    track: str,
) -> tuple[str, list[int]]:
    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=[ANDROIDPUBLISHER_SCOPE],
    )
    service = build('androidpublisher', 'v3', credentials=creds, cache_discovery=False)

    edit = service.edits().insert(packageName=package_name, body={}).execute()
    edit_id = edit['id']

    version_codes: list[int] = []

    for file_path in release_files:
        suffix = file_path.suffix.lower()
        print(f'Uploading: {file_path}')

        if suffix == '.aab':
            media = MediaFileUpload(
                str(file_path),
                mimetype='application/octet-stream',
                resumable=True,
            )
            req = (
                service.edits()
                .bundles()
                .upload(
                    packageName=package_name,
                    editId=edit_id,
                    media_body=media,
                )
            )
            res = _upload_resumable(req)
            version_codes.append(int(res['versionCode']))

        elif suffix == '.apk':
            media = MediaFileUpload(
                str(file_path),
                mimetype='application/vnd.android.package-archive',
                resumable=True,
            )
            req = (
                service.edits()
                .apks()
                .upload(
                    packageName=package_name,
                    editId=edit_id,
                    media_body=media,
                )
            )
            res = _upload_resumable(req)
            version_codes.append(int(res['versionCode']))

        else:
            raise ValueError(f'Unsupported file type: {file_path} (expected .aab or .apk)')

    # Keep it simple: push a single "completed" release to the chosen track.
    service.edits().tracks().update(
        packageName=package_name,
        editId=edit_id,
        track=track,
        body={
            'track': track,
            'releases': [
                {
                    'status': 'completed',
                    'versionCodes': [str(vc) for vc in version_codes],
                }
            ],
        },
    ).execute()

    committed = service.edits().commit(packageName=package_name, editId=edit_id).execute()
    committed_id = committed.get('id', edit_id)

    return committed_id, version_codes


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Upload APK/AAB to Google Play (Publishing API edits).')

    p.add_argument(
        '--service-account-json-plain-text',
        '--serviceAccountJsonPlainText',
        dest='service_account_json_plain_text',
        required=True,
        help='Service account JSON string, path to a JSON file, or "-" to read from stdin.',
    )
    p.add_argument(
        '--package-name',
        '--packageName',
        dest='package_name',
        required=True,
        help='Android package name (applicationId), e.g. com.nuqayah.rawy',
    )
    p.add_argument(
        '--release-files',
        '--releaseFiles',
        dest='release_files',
        required=True,
        nargs='+',
        help='One or more APK/AAB paths (can also be comma-separated).',
    )
    p.add_argument(
        '--track',
        required=True,
        help='Track name, e.g. internal, alpha, beta, production.',
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    service_account_info = _load_service_account_json(args.service_account_json_plain_text)
    release_files = _parse_release_files(args.release_files)

    try:
        edit_id, version_codes = upload_to_play(
            service_account_info=service_account_info,
            package_name=args.package_name,
            release_files=release_files,
            track=args.track,
        )
    except HttpError as e:
        # Best-effort: surface API error details without getting fancy
        msg = str(e)
        try:
            if getattr(e, 'content', None):
                msg = e.content.decode(errors='replace')
        except Exception:
            pass
        print(f'Google API error:\n{msg}', file=sys.stderr)
        return 2
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1

    print(f'Uploaded versionCodes: {", ".join(str(v) for v in version_codes)}')
    print(f'Committed editId: {edit_id}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
