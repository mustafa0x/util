#!/usr/bin/env -S uv --quiet run --script
# /// script
# requires-python = ">=3.12"
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
IMAGE_TYPE_ALIASES = {
    'feature-graphic': 'featureGraphic',
    'featureGraphic': 'featureGraphic',
    'icon': 'icon',
    'large-tablet-screenshots': 'tenInchScreenshots',
    'phone-screenshots': 'phoneScreenshots',
    'phoneScreenshots': 'phoneScreenshots',
    'seven-inch-screenshots': 'sevenInchScreenshots',
    'sevenInchScreenshots': 'sevenInchScreenshots',
    'tablet-screenshots': 'sevenInchScreenshots',
    'ten-inch-screenshots': 'tenInchScreenshots',
    'tenInchScreenshots': 'tenInchScreenshots',
    'tv-banner': 'tvBanner',
    'tv-screenshots': 'tvScreenshots',
    'tvBanner': 'tvBanner',
    'tvScreenshots': 'tvScreenshots',
    'wear-screenshots': 'wearScreenshots',
    'wearScreenshots': 'wearScreenshots',
}
SUPPORTED_IMAGE_SUFFIXES = {'.jpeg', '.jpg', '.png'}


def _load_service_account_json(value: str) -> dict:
    if value == '-':
        return json.loads(sys.stdin.read())

    path = Path(value)
    if path.exists() and path.is_file():
        return json.loads(path.read_text())

    return json.loads(value)


def _parse_release_files(values: list[str]) -> list[Path]:
    files: list[Path] = []
    for value in values:
        for part in value.split(','):
            part = part.strip()
            if not part:
                continue
            path = Path(part)
            if not path.exists():
                raise FileNotFoundError(part)
            if not path.is_file():
                raise ValueError(f'Not a file: {part}')
            files.append(path)

    return files


def _image_mimetype(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == '.png':
        return 'image/png'
    if suffix in {'.jpg', '.jpeg'}:
        return 'image/jpeg'
    raise ValueError(f'Unsupported image type: {path}')


def _normalize_image_type(name: str) -> str:
    image_type = IMAGE_TYPE_ALIASES.get(name)
    if image_type is None:
        supported = ', '.join(sorted(IMAGE_TYPE_ALIASES))
        raise ValueError(f'Unsupported image type folder: {name}. Supported names: {supported}')
    return image_type


def _collect_image_jobs(images_root: Path) -> list[tuple[str, str, list[Path]]]:
    if not images_root.exists():
        raise FileNotFoundError(images_root)
    if not images_root.is_dir():
        raise ValueError(f'Images root is not a directory: {images_root}')

    jobs: list[tuple[str, str, list[Path]]] = []

    for language_dir in sorted(path for path in images_root.iterdir() if path.is_dir()):
        for image_type_dir in sorted(path for path in language_dir.iterdir() if path.is_dir()):
            image_type = _normalize_image_type(image_type_dir.name)
            files = sorted(
                path
                for path in image_type_dir.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            )
            if not files:
                continue
            jobs.append((language_dir.name, image_type, files))

    return jobs


def _upload_resumable(request) -> dict:
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def _build_service(service_account_info: dict):
    credentials = Credentials.from_service_account_info(
        service_account_info,
        scopes=[ANDROIDPUBLISHER_SCOPE],
    )
    return build('androidpublisher', 'v3', credentials=credentials, cache_discovery=False)


def _upload_release_files(service, *, package_name: str, edit_id: str, release_files: list[Path]) -> list[int]:
    version_codes: list[int] = []

    for file_path in release_files:
        suffix = file_path.suffix.lower()
        print(f'Uploading release file: {file_path}')

        if suffix == '.aab':
            request = service.edits().bundles().upload(
                packageName=package_name,
                editId=edit_id,
                media_body=MediaFileUpload(
                    str(file_path),
                    mimetype='application/octet-stream',
                    resumable=True,
                ),
            )
            response = _upload_resumable(request)
            version_codes.append(int(response['versionCode']))
            continue

        if suffix == '.apk':
            request = service.edits().apks().upload(
                packageName=package_name,
                editId=edit_id,
                media_body=MediaFileUpload(
                    str(file_path),
                    mimetype='application/vnd.android.package-archive',
                    resumable=True,
                ),
            )
            response = _upload_resumable(request)
            version_codes.append(int(response['versionCode']))
            continue

        raise ValueError(f'Unsupported release file: {file_path} (expected .aab or .apk)')

    return version_codes


def _sync_images(service, *, package_name: str, edit_id: str, images_root: Path) -> int:
    jobs = _collect_image_jobs(images_root)
    if not jobs:
        raise ValueError(f'No images found under {images_root}')

    uploaded_count = 0

    for language, image_type, files in jobs:
        print(f'Syncing {language}/{image_type}: replacing with {len(files)} image(s)')
        service.edits().images().deleteall(
            packageName=package_name,
            editId=edit_id,
            language=language,
            imageType=image_type,
        ).execute()

        for file_path in files:
            request = service.edits().images().upload(
                packageName=package_name,
                editId=edit_id,
                language=language,
                imageType=image_type,
                media_body=MediaFileUpload(
                    str(file_path),
                    mimetype=_image_mimetype(file_path),
                    resumable=True,
                ),
            )
            _upload_resumable(request)
            uploaded_count += 1

    return uploaded_count


def apply_play_edit(
    *,
    service_account_info: dict,
    package_name: str,
    release_files: list[Path],
    track: str | None,
    images_root: Path | None,
) -> tuple[str, list[int], int]:
    if not release_files and images_root is None:
        raise ValueError('Provide at least one of --release-files or --images-root.')
    if release_files and not track:
        raise ValueError('--track is required when uploading release files.')

    service = _build_service(service_account_info)
    edit = service.edits().insert(packageName=package_name, body={}).execute()
    edit_id = edit['id']

    version_codes: list[int] = []
    uploaded_images = 0

    if release_files:
        version_codes = _upload_release_files(
            service,
            package_name=package_name,
            edit_id=edit_id,
            release_files=release_files,
        )
        service.edits().tracks().update(
            packageName=package_name,
            editId=edit_id,
            track=track,
            body={
                'track': track,
                'releases': [
                    {
                        'status': 'completed',
                        'versionCodes': [str(version_code) for version_code in version_codes],
                    }
                ],
            },
        ).execute()

    if images_root is not None:
        uploaded_images = _sync_images(
            service,
            package_name=package_name,
            edit_id=edit_id,
            images_root=images_root,
        )

    committed = service.edits().commit(packageName=package_name, editId=edit_id).execute()
    return committed.get('id', edit_id), version_codes, uploaded_images


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Upload APK/AAB files and sync listing images with the Google Play Developer API.',
    )
    parser.add_argument(
        '--service-account-json-plain-text',
        '--serviceAccountJsonPlainText',
        dest='service_account_json_plain_text',
        required=True,
        help='Service account JSON string, path to a JSON file, or "-" to read from stdin.',
    )
    parser.add_argument(
        '--package-name',
        '--packageName',
        dest='package_name',
        required=True,
        help='Android package name (applicationId), for example com.example.app.',
    )
    parser.add_argument(
        '--release-files',
        '--releaseFiles',
        dest='release_files',
        nargs='*',
        default=[],
        help='One or more APK/AAB paths. Items may also be comma-separated.',
    )
    parser.add_argument(
        '--track',
        help='Track name, for example internal, alpha, beta, or production.',
    )
    parser.add_argument(
        '--images-root',
        '--imagesRoot',
        dest='images_root',
        help=(
            'Root directory for listing images. Expected layout: '
            '<root>/<language>/<imageType>/*.(png|jpg|jpeg). '
            'Example imageType values: phoneScreenshots, sevenInchScreenshots, tenInchScreenshots.'
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    service_account_info = _load_service_account_json(args.service_account_json_plain_text)
    release_files = _parse_release_files(args.release_files)
    images_root = Path(args.images_root) if args.images_root else None

    try:
        edit_id, version_codes, uploaded_images = apply_play_edit(
            service_account_info=service_account_info,
            package_name=args.package_name,
            release_files=release_files,
            track=args.track,
            images_root=images_root,
        )
    except HttpError as error:
        message = str(error)
        try:
            if getattr(error, 'content', None):
                message = error.content.decode(errors='replace')
        except Exception:
            pass
        print(f'Google API error:\n{message}', file=sys.stderr)
        return 2
    except Exception as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1

    if version_codes:
        print(f'Uploaded versionCodes: {", ".join(str(value) for value in version_codes)}')
    if uploaded_images:
        print(f'Uploaded images: {uploaded_images}')
    print(f'Committed editId: {edit_id}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
