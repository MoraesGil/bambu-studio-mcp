#!/bin/bash
# Slice one preserved Bambu project with the selected installed macOS CLI.
set -euo pipefail
umask 077
usage() { printf '%s\n' "$*" >&2; exit 64; }
[[ $# == 3 || $# == 4 ]] || usage 'Usage: slice.sh INPUT.3mf PLATE OUTPUT.gcode [--allow-newer-file]'
if [[ $# == 4 && "$4" != --allow-newer-file ]]; then
    usage 'Only --allow-newer-file is accepted as fourth argument'
fi
source_path=$1 plate=$2 output_path=$3
[[ "$plate" =~ ^[1-9][0-9]*$ ]] || usage 'PLATE must be a positive integer (1-based)'
case "$source_path" in *.3mf|*.3MF) ;; *) usage 'Input must be a .3mf file' ;; esac
case "$output_path" in *.gcode|*.GCODE) ;; *) usage 'Output must be a .gcode file' ;; esac
[[ -f "$source_path" && -r "$source_path" ]] || usage 'Input is not a readable file'
source_path="$(cd "$(dirname "$source_path")" && pwd -P)/$(basename "$source_path")"
output_directory=$(cd "$(dirname "$output_path")" && pwd -P) || usage 'Output directory must exist'
output_path="$output_directory/$(basename "$output_path")"
[[ ! -e "$output_path" && ! -L "$output_path" ]] || usage 'Output already exists'
application=${BAMBU_APP:-/Applications/BambuStudio.app}
executable="$application/Contents/MacOS/BambuStudio"
[[ -x "$executable" ]] || usage "Bambu Studio executable unavailable: $executable"
executable="$(cd "$(dirname "$executable")" && pwd -P)/$(basename "$executable")"
time_limit=${BAMBU_TIMEOUT_SECONDS:-1800}
[[ "$time_limit" =~ ^[1-9][0-9]*$ ]] || usage 'BAMBU_TIMEOUT_SECONDS must be a positive integer'
command -v xmllint >/dev/null || usage 'xmllint is required to validate the native Bambu project marker'

# Stage beside the destination for atomic, no-overwrite hard-link publication.
staging=$(mktemp -d "$output_directory/.bambu-slice.XXXXXXXX")
native_pid='' watchdog_pid=''
cleanup() {
    [[ -z "$native_pid" ]] || kill -KILL -- "-$native_pid" 2>/dev/null || true
    [[ -z "$watchdog_pid" ]] || kill -KILL -- "-$watchdog_pid" 2>/dev/null || true
    rm -rf "$staging"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$staging/data" "$staging/output" "$staging/tmp"
cp "$source_path" "$staging/input.3mf"
# Bambu's importer ignores saved settings without this exact Application prefix.
marker=$(unzip -p "$staging/input.3mf" 3D/3dmodel.model | xmllint --nonet --xpath \
    'string(/*[local-name()="model"]/*[local-name()="metadata"][@name="Application"])' - 2>/dev/null) || usage 'Input must be a complete native Bambu Studio project'
case "$marker" in BambuStudio-*) ;; *) usage 'Input is not recognized as a Bambu Studio project; native import would ignore its saved settings' ;; esac
export LC_ALL=C TMPDIR="$staging/tmp/" TMP="$staging/tmp" TEMP="$staging/tmp"
unset DISPLAY WAYLAND_DISPLAY BAMBU_ORCA_ALLOW_NEWER_FILE
argv=("$executable")
# allow-newer-file is a BOOLEAN in Bambu's PrintConfig, with no numeric argument.
[[ $# != 4 ]] || argv+=(--allow-newer-file)
argv+=(--slice "$plate" --outputdir "$staging/output" --datadir "$staging/data" "$staging/input.3mf")
printf 'Bambu Studio executable: %s\n' "$executable" >&2
cd "$staging"
# Job control gives each background job a process group without a setsid helper.
set -m
"${argv[@]}" &
native_pid=$!
(
    sleep "$time_limit"
    : > "$staging/timed-out"
    kill -KILL -- "-$native_pid" 2>/dev/null || true
) &
watchdog_pid=$!
code=0
wait "$native_pid" || code=$?
native_pid=''
kill -KILL -- "-$watchdog_pid" 2>/dev/null || true
wait "$watchdog_pid" 2>/dev/null || true
watchdog_pid=''
set +m
if [[ -f "$staging/timed-out" ]]; then
    printf 'Native Bambu slice timed out after %s seconds\n' "$time_limit" >&2
    exit 124
fi
[[ "$code" == 0 ]] || exit "$code"
generated="$staging/output/plate_$plate.gcode"
[[ -f "$generated" && -s "$generated" && ! -L "$generated" ]] || usage "Native CLI produced no plate_$plate.gcode"
if ! /bin/link "$generated" "$output_path"; then
    printf 'Could not publish G-code without overwriting: %s\n' "$output_path" >&2
    exit 73
fi
printf 'G-code: %s\n' "$output_path" >&2
