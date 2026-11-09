#!/bin/sh
# Install the official release into a persistent, isolated uv tool environment.
set -eu

version=0.6.0rc1
wheel_name="retulo-${version}-py3-none-any.whl"
release_url="https://github.com/TheStack-ai/retulo/releases/download/v${version}"
asset_dir=
uv_path=
bin_dir=
dry_run=0
usage() {
    cat <<'EOF'
Install Retulo on macOS or Linux. No source checkout or Python setup is needed.

Usage: sh install.sh [--asset-dir DIR] [--uv PATH] [--bin-dir DIR] [--dry-run]
  --asset-dir DIR  Use a downloaded wheel and SHA256SUMS from this directory.
  --uv PATH       Use this uv executable instead of discovering/installing uv.
  --bin-dir DIR   Install the retulo command here (absolute directory).
  --dry-run       Show the plan without downloads or changes.
  --help          Show this help.

Shell profiles and agent settings are never changed by this installer.
EOF
}
fail() { printf 'Retulo install: %s\n' "$*" >&2; exit 1; }
while [ "$#" -gt 0 ]; do
    case "$1" in
        --asset-dir|--uv|--bin-dir)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail "Missing value for $1"
            case "$1" in
                --asset-dir) asset_dir=$2 ;;
                --uv) uv_path=$2 ;;
                --bin-dir) bin_dir=$2 ;;
            esac
            shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done
case "$(uname -s)" in Darwin|Linux) ;; *) fail 'This installer supports macOS and Linux.' ;; esac
if [ -n "$bin_dir" ]; then
    case "$bin_dir" in /*) ;; *) fail '--bin-dir must be absolute.' ;; esac
    export UV_TOOL_BIN_DIR="$bin_dir"
fi
if [ "$dry_run" -eq 1 ]; then
    printf 'Retulo %s\nRelease: %s\n' "$version" "$release_url"
    printf 'Verify SHA256SUMS, then uv tool install --python 3.11 <verified wheel> in a persistent environment.\n'
    printf 'Use existing uv or install uv privately; allow uv to obtain Python if needed.\n'
    printf 'No project, agent settings, shell profiles or task data will be changed.\n'
    exit 0
fi
[ -n "${HOME:-}" ] || fail 'HOME must be set.'
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/retulo-install.XXXXXXXX")
trap 'rm -rf "$work_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fetch() {
    command -v curl >/dev/null 2>&1 || fail 'curl is required.'
    curl --proto '=https' --tlsv1.2 -fsSL --retry 2 --connect-timeout 20 "$1" -o "$2"
}
if [ -n "$asset_dir" ]; then
    [ -f "$asset_dir/$wheel_name" ] && [ -f "$asset_dir/SHA256SUMS" ] || fail 'Release wheel or SHA256SUMS is missing.'
    cp "$asset_dir/$wheel_name" "$work_dir/$wheel_name"
    cp "$asset_dir/SHA256SUMS" "$work_dir/SHA256SUMS"
else
    fetch "$release_url/$wheel_name" "$work_dir/$wheel_name"
    fetch "$release_url/SHA256SUMS" "$work_dir/SHA256SUMS"
fi
expected=$(awk -v name="$wheel_name" '$2 == name {print $1}' "$work_dir/SHA256SUMS")
[ "${#expected}" -eq 64 ] || fail 'Missing, duplicate or invalid wheel checksum.'
case "$expected" in *[!0-9a-f]*) fail 'Invalid wheel checksum.' ;; esac
if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$work_dir/$wheel_name" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
    actual=$(shasum -a 256 "$work_dir/$wheel_name" | awk '{print $1}')
else
    fail 'sha256sum or shasum is required.'
fi
[ "$actual" = "$expected" ] || fail 'Wheel checksum mismatch; nothing was installed.'
if [ -z "$uv_path" ]; then
    if command -v uv >/dev/null 2>&1; then
        uv_path=$(command -v uv)
    else
        uv_home="${XDG_DATA_HOME:-$HOME/.local/share}/retulo/bootstrap/uv"
        uv_path="$uv_home/uv"
        if [ ! -x "$uv_path" ]; then
            printf 'Installing uv privately in %s\n' "$uv_home"
            fetch 'https://astral.sh/uv/install.sh' "$work_dir/install-uv.sh"
            UV_UNMANAGED_INSTALL="$uv_home" sh "$work_dir/install-uv.sh"
        fi
    fi
fi
[ -x "$uv_path" ] || fail 'The uv executable is unavailable.'
printf 'Installing verified Retulo %s in a persistent tool environment…\n' "$version"
# Deliberately omit --force: uv must refuse to overwrite an unrelated executable.
"$uv_path" --no-config tool install --python 3.11 "$work_dir/$wheel_name"
tool_bin=$("$uv_path" --no-config tool dir --bin)
"$tool_bin/retulo" --version
printf '\nInstalled: %s/retulo\n' "$tool_bin"
case ":${PATH:-}:" in
    *":$tool_bin:"*) ;;
    *) printf 'Add this directory to PATH before using retulo, for example:\n'
       printf '  export PATH="%s:$PATH"\n' "$tool_bin" ;;
esac
printf '\nConnect an existing project:\n  retulo setup codex --project /absolute/project --apply\n'
printf '  retulo setup claude --project /absolute/project --apply\n'
