#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: zip-ref-patch [--output PATCH_FILE] ZIP_PATH BASE_REV

Create a repo-relative patch by comparing a zip snapshot against `git archive BASE_REV`.

Examples:
  zip-ref-patch ~/Downloads/kunuz.zip 9c6c83a
  zip-ref-patch --output /tmp/kunuz.patch ~/Downloads/kunuz.zip 9c6c83a

Review/apply flow:
  zip-ref-patch --output /tmp/import.patch repo.zip 9c6c83a
  git apply --stat /tmp/import.patch
  git apply --check /tmp/import.patch
  git apply /tmp/import.patch
USAGE
}

fail() {
    echo "$*" >&2
    exit 1
}

find_zip_root() {
    local extracted_dir=$1
    local entry
    local entries=()

    while IFS= read -r entry; do
        entries+=("$entry")
    done < <(find "$extracted_dir" -mindepth 1 -maxdepth 1 ! -name '__MACOSX' | sort)

    if [[ ${#entries[@]} -eq 1 && -d ${entries[0]} ]]; then
        printf '%s\n' "${entries[0]}"
        return
    fi

    printf '%s\n' "$extracted_dir"
}

normalize_patch() {
    local raw_patch=$1

    perl -pe "
        s{^(diff --git )a/before/}{\${1}a/};
        s{ b/after/}{ b/} if /^diff --git /;
        s{^(--- )a/before/}{\${1}a/};
        s{^(\\+\\+\\+ )b/after/}{\${1}b/};
        s{^(rename from )before/}{\${1}};
        s{^(rename to )after/}{\${1}};
        s{^(copy from )before/}{\${1}};
        s{^(copy to )after/}{\${1}};
        s{^(Binary files )a/before/}{\${1}a/};
        s{ and b/after/}{ and b/} if /^Binary files /;
    " "$raw_patch"
}

output_path=''

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output)
            [[ $# -ge 2 ]] || fail 'Missing value for --output'
            output_path=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            fail "Unknown option: $1"
            ;;
        *)
            break
            ;;
    esac
done

[[ $# -eq 2 ]] || {
    usage >&2
    exit 1
}

zip_path=$1
base_rev=$2

[[ -f $zip_path ]] || fail "Zip not found: $zip_path"
command -v unzip >/dev/null || fail 'unzip is required'
git rev-parse --verify "${base_rev}^{commit}" >/dev/null 2>&1 || fail "Unknown git revision: $base_rev"

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/zip-ref-patch.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

mkdir -p "$tmp_dir/export" "$tmp_dir/unzip"

git archive "$base_rev" | tar -x -C "$tmp_dir/export"
unzip -q "$zip_path" -d "$tmp_dir/unzip"

zip_root=$(find_zip_root "$tmp_dir/unzip")

mkdir -p "$tmp_dir/compare"
mv "$tmp_dir/export" "$tmp_dir/compare/before"
mv "$zip_root" "$tmp_dir/compare/after"

raw_patch="$tmp_dir/raw.patch"

set +e
(
    cd "$tmp_dir/compare"
    git diff --no-index --binary --full-index before after > "$raw_patch"
)
diff_status=$?
set -e

if [[ $diff_status -gt 1 ]]; then
    fail 'git diff --no-index failed'
fi

if [[ -n $output_path ]]; then
    mkdir -p "$(dirname "$output_path")"
    normalize_patch "$raw_patch" > "$output_path"
    patch_target=$output_path
else
    normalize_patch "$raw_patch"
    patch_target='stdout'
fi

echo "Compared $zip_path against $base_rev" >&2
echo "Patch output: $patch_target" >&2

if [[ $diff_status -eq 0 ]]; then
    echo 'No differences found' >&2
else
    (
        cd "$tmp_dir/compare"
        git diff --no-index --stat before after || true
    ) >&2
fi
