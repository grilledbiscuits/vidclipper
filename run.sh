#!/usr/bin/env bash
# VidClipper launcher.
#
# First run: creates .venv and installs the Python dependencies.
# Every run after that: just starts the app (no network access needed).
#
# Usage: ./run.sh [video-file]
#        VIDCLIPPER_PYTHON=/path/to/python3 ./run.sh    # force an interpreter

set -uo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
REQ="$APP_DIR/requirements.txt"
STAMP="$VENV/.deps-installed"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/vidclipper"
LOG="$LOG_DIR/launch.log"
mkdir -p "$LOG_DIR"

# Started from a terminal, or from the menu? Problems are reported with a
# GUI dialog when there is no terminal to print to.
if [ -t 1 ]; then GUI=0; else GUI=1; fi

log() { echo "[vidclipper] $*" | tee -a "$LOG" >&2; }

fail() {
    local title="$1" body="$2"
    log "$title: ${body//$'\n'/ }"
    if [ "$GUI" = "1" ] && command -v zenity >/dev/null 2>&1; then
        zenity --error --no-wrap --title="$title" \
               --text="$body"$'\n\n'"Details: $LOG" 2>/dev/null
    elif [ "$GUI" = "1" ] && command -v notify-send >/dev/null 2>&1; then
        notify-send "VidClipper: $title" "$body"
    fi
    exit 1
}

# --- ffmpeg -----------------------------------------------------------
missing=""
command -v ffmpeg  >/dev/null 2>&1 || missing="ffmpeg"
command -v ffprobe >/dev/null 2>&1 || missing="${missing:+$missing and }ffprobe"
if [ -n "$missing" ]; then
    fail "ffmpeg not found" \
"VidClipper needs $missing to cut video.

Install it with:
    sudo apt install ffmpeg"
fi

# --- find an interpreter that can build a venv ------------------------
# Checked in order: an explicit override, PATH, then the usual places a
# newer Python lives on Mint/Ubuntu (conda, pyenv, /usr/local).
python_candidates() {
    printf '%s\n' "${VIDCLIPPER_PYTHON:-}" \
        python3.13 python3.12 python3.11 python3.14 python3 \
        "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" \
        "$HOME/miniforge3/bin/python3" "$HOME/mambaforge/bin/python3" \
        /opt/miniconda3/bin/python3 /opt/anaconda3/bin/python3 \
        /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
        /usr/local/bin/python3.11 /usr/local/bin/python3
    ls -1 "$HOME"/.pyenv/versions/*/bin/python3 2>/dev/null
}

resolve() { command -v "$1" 2>/dev/null; }

usable_python() {  # $1 = interpreter, $2 = "with-pip" to require ensurepip
    local exe="$1" mode="$2"
    [ -n "$exe" ] || return 1
    exe="$(resolve "$exe")" || return 1
    [ -x "$exe" ] || return 1
    if [ "$mode" = "with-pip" ]; then
        "$exe" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || return 1
    else
        "$exe" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 || return 1
    fi
    printf '%s\n' "$exe"
}

pick_python() {  # prints "<interpreter> <with-pip|no-pip>"
    local candidate found
    for candidate in $(python_candidates); do
        found="$(usable_python "$candidate" with-pip)" && {
            printf '%s with-pip\n' "$found"; return 0; }
    done
    # No ensurepip anywhere (Debian/Ubuntu split it into python3-venv).
    # A venv can still be built without pip and pip added afterwards.
    for candidate in $(python_candidates); do
        found="$(usable_python "$candidate" no-pip)" && {
            printf '%s no-pip\n' "$found"; return 0; }
    done
    return 1
}

bootstrap_pip() {  # get pip into a --without-pip venv
    local getpip="$LOG_DIR/get-pip.py"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$getpip" >>"$LOG" 2>&1 || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget -q https://bootstrap.pypa.io/get-pip.py -O "$getpip" >>"$LOG" 2>&1 || return 1
    else
        return 1
    fi
    "$PY" "$getpip" >>"$LOG" 2>&1
}

setup_venv() {
    local interpreter="$1" pip_mode="$2"
    rm -rf "$VENV"
    if [ "$pip_mode" = "with-pip" ]; then
        "$interpreter" -m venv "$VENV" >>"$LOG" 2>&1 || return 1
    else
        log "Building the environment without ensurepip; fetching pip separately."
        "$interpreter" -m venv --without-pip "$VENV" >>"$LOG" 2>&1 || return 1
        bootstrap_pip || return 1
    fi
    "$PY" -m pip install --upgrade pip >>"$LOG" 2>&1
    "$PY" -m pip install -r "$REQ" >>"$LOG" 2>&1
}

# --- first-run bootstrap ---------------------------------------------
req_hash="$(md5sum "$REQ" 2>/dev/null | cut -d' ' -f1)"
need_setup=1
if [ -x "$PY" ] && "$PY" -c "import PySide6" >/dev/null 2>&1; then
    if [ "$(cat "$STAMP" 2>/dev/null)" = "$req_hash" ]; then
        need_setup=0
    else
        # The environment works but predates this requirements.txt: try a
        # quick top-up rather than rebuilding from scratch.
        log "Updating dependencies..."
        if "$PY" -m pip install -r "$REQ" >>"$LOG" 2>&1; then
            echo "$req_hash" > "$STAMP"
            need_setup=0
        fi
    fi
fi

if [ "$need_setup" = "1" ]; then
    log "First-time setup: creating the virtual environment and installing dependencies."
    read -r BOOTSTRAP_PY PIP_MODE <<<"$(pick_python)"
    [ -n "${BOOTSTRAP_PY:-}" ] || fail "Python 3.11+ not found" \
"VidClipper needs Python 3.11 or newer with the 'venv' module.

Install it with:
    sudo apt install python3 python3-venv

Or point VidClipper at an interpreter you already have:
    VIDCLIPPER_PYTHON=/path/to/python3 ./run.sh"

    log "Using $BOOTSTRAP_PY ($PIP_MODE)"
    if [ "$GUI" = "1" ] && command -v zenity >/dev/null 2>&1; then
        (
            echo "5";  echo "# Creating the virtual environment..."
            echo "25"; echo "# Downloading PySide6 (about 100 MB, one time only)..."
            setup_venv "$BOOTSTRAP_PY" "$PIP_MODE"
            echo "100"; echo "# Finishing up..."
        ) | zenity --progress --auto-close --no-cancel --width=430 \
                   --title="VidClipper first-time setup" \
                   --text="Preparing VidClipper..." 2>/dev/null
    else
        log "Installing dependencies (PySide6 is ~100 MB; this happens once)..."
        setup_venv "$BOOTSTRAP_PY" "$PIP_MODE"
        tail -n 3 "$LOG" >/dev/null 2>&1
    fi

    if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
        fail "Setup failed" \
"The Python dependencies could not be installed.

Check your internet connection, then run this from a terminal to see the
error in full:
    $APP_DIR/run.sh"
    fi
    echo "$req_hash" > "$STAMP"
    log "Setup complete."
fi

# --- launch -----------------------------------------------------------
cd "$APP_DIR"
log "Starting VidClipper"
exec "$PY" -m vidclipper "$@"
