#!/bin/bash
# Caption Guru - macOS / Linux installer (from source, any branch or tag)
#
# Assumes nothing is installed. Downloads into ~/.caption-guru:
#   - uv (Python manager) -> Python 3.12, private to this install
#   - Node.js LTS (portable), only to build the caption display
#   - the Caption Guru source for the branch/tag you choose
# then builds the display, installs the desktop app into a venv, and puts a
# "Caption Guru" folder of launchers on your Desktop. No sudo needed.
#
# Usage (Terminal):
#   curl -fsSL https://github.com/jonathan-annett/caption-guru/releases/download/installer/install.sh | bash
#   bash install.sh --ref feat/sherpa-onnx-streaming     # skip the menu
#   bash install.sh --uninstall
# Environment overrides: CAPTION_GURU_REF, CAPTION_GURU_HOME
#
# Written for the bash 3.2 that ships with macOS: no associative arrays, mapfile, etc.

set -euo pipefail

OWNER=jonathan-annett
REPO=caption-guru
PYTHON_VERSION=3.12
NODE_MAJOR=22
PORT=8765

REF="${CAPTION_GURU_REF:-}"
INSTALL_DIR="${CAPTION_GURU_HOME:-$HOME/.caption-guru}"
UNINSTALL=0
OPEN_FOLDER=1

while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:?--ref needs a branch, tag or commit}"; shift 2 ;;
    --ref=*) REF="${1#--ref=}"; shift ;;
    --dir) INSTALL_DIR="${2:?}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --no-open) OPEN_FOLDER=0; shift ;;
    -h|--help) sed -n '2,17p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

TOOLS="$INSTALL_DIR/tools"
APP_DIR="$INSTALL_DIR/app"
VENV="$INSTALL_DIR/venv"
WEB_DIR="$INSTALL_DIR/web"
DOWNLOADS="$INSTALL_DIR/downloads"
LAUNCHERS="$INSTALL_DIR/launchers"

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) PLATFORM=mac ;;
  Linux) PLATFORM=linux ;;
  *) echo "This installer is for macOS and Linux. On Windows use CaptionGuru-Setup.cmd." >&2; exit 1 ;;
esac
case "$ARCH" in
  arm64|aarch64) CPU=arm64 ;;
  x86_64|amd64) CPU=x64 ;;
  *) echo "Unsupported CPU: $ARCH" >&2; exit 1 ;;
esac

desktop_dir() {
  local d=""
  if [ "$PLATFORM" = linux ] && command -v xdg-user-dir >/dev/null 2>&1; then
    d="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  fi
  [ -n "$d" ] && [ "$d" != "$HOME" ] || d="$HOME/Desktop"
  echo "$d"
}
DESKTOP_FOLDER="$(desktop_dir)/Caption Guru"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    \033[33m! %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mInstallation failed: %s\033[0m\n' "$*" >&2; exit 1; }

download() { # url out
  info "downloading $1"
  curl -fL --retry 3 --retry-delay 2 -A caption-guru-installer -sS -o "$2.part" "$1" && mv "$2.part" "$2"
}

github_api() { # path -> json on stdout
  local auth=()
  [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
  curl -fsSL -A caption-guru-installer -H "Accept: application/vnd.github+json" \
    ${auth[@]+"${auth[@]}"} "https://api.github.com/repos/$OWNER/$REPO/$1"
}

json_names() { # pull "name" values out of a GitHub list response, in order
  grep -o '"name": *"[^"]*"' | sed 's/.*"name": *"\([^"]*\)"/\1/'
}

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

remove_launchers() {
  if [ -d "$DESKTOP_FOLDER" ]; then
    # Only our own launchers; never anything else the user put in the folder.
    find "$DESKTOP_FOLDER" -maxdepth 1 \( -name '*.command' -o -name '*.desktop' -o -name '*.webloc' \) -delete
    rmdir "$DESKTOP_FOLDER" 2>/dev/null || true
  fi
  if [ "$PLATFORM" = linux ]; then
    rm -f "$HOME/.local/share/applications"/caption-guru*.desktop
  fi
}

open_path() {
  if [ "$PLATFORM" = mac ]; then open "$1" 2>/dev/null || true
  elif command -v xdg-open >/dev/null 2>&1; then (xdg-open "$1" >/dev/null 2>&1 &) || true
  fi
}

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  step "Uninstalling Caption Guru from $INSTALL_DIR"
  remove_launchers
  rm -rf "$INSTALL_DIR"
  info "Removed. Downloaded speech models are kept in $HOME/.cache/caption-guru and"
  info "$HOME/.cache/huggingface; delete those too if you want the space back."
  if [ "$PLATFORM" = mac ]; then
    info "Saved settings: ~/Library/Application Support/CaptionGuru"
  else
    info "Saved settings: ~/.config/caption-guru"
  fi
  exit 0
fi

printf '\n  \033[32mCaption Guru - %s installer\033[0m\n' "$( [ "$PLATFORM" = mac ] && echo macOS || echo Linux )"
info "Installs into: $INSTALL_DIR"

command -v curl >/dev/null 2>&1 || die "curl is needed (install it with your package manager)."
command -v tar >/dev/null 2>&1 || die "tar is needed."

# ---------------------------------------------------------------- choose branch / tag
# Reading the answer from /dev/tty keeps the menu working under `curl ... | bash`.
if [ -z "$REF" ]; then
  choices="main"
  if branches="$(github_api 'branches?per_page=50' 2>/dev/null)"; then
    choices="$choices
$(printf '%s\n' "$branches" | json_names | grep -vx main || true)"
    # (the 'installer' tag only holds this installer, not an app version)
    tags="$(github_api 'tags?per_page=6' 2>/dev/null | json_names | grep -vx installer || true)"
    [ -n "$tags" ] && choices="$choices
$tags"
  else
    warn "Couldn't list branches from GitHub. You can still type one."
  fi
  echo
  echo "  Which version do you want to install?"
  i=0
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    i=$((i + 1))
    if [ $i -eq 1 ]; then printf '   [%d] %s   (stable - default)\n' $i "$c"
    else printf '   [%d] %s\n' $i "$c"; fi
  done <<EOF
$choices
EOF
  echo
  ans=""
  if [ -r /dev/tty ]; then
    printf '  Enter a number, or type a branch/tag/commit name [1]: '
    read -r ans </dev/tty || ans=""
  fi
  ans="$(printf '%s' "$ans" | tr -d '[:space:]')"
  if [ -z "$ans" ]; then REF=main
  elif printf '%s' "$ans" | grep -qx '[0-9][0-9]*' && [ "$ans" -ge 1 ] && [ "$ans" -le "$i" ]; then
    REF="$(printf '%s\n' "$choices" | grep -v '^$' | sed -n "${ans}p")"
  else REF="$ans"; fi
fi
info "Version: $REF"

mkdir -p "$INSTALL_DIR" "$TOOLS" "$DOWNLOADS"

# ---------------------------------------------------------------- system checks
step "Checking your system"
if [ "$PLATFORM" = linux ]; then
  if ldconfig -p 2>/dev/null | grep -q 'libportaudio\.so'; then
    info "PortAudio: present"
  else
    warn "PortAudio (microphone library) not found. Live captions need it:"
    warn "  Debian/Ubuntu: sudo apt install libportaudio2"
    warn "  Fedora:        sudo dnf install portaudio"
    warn "  Arch:          sudo pacman -S portaudio"
    warn "Continuing; demo mode works without it."
  fi
  info "Linux shows captions in Chrome/Chromium kiosk mode (or any browser)."
else
  info "macOS $(sw_vers -productVersion 2>/dev/null || echo '') on $ARCH"
fi

# ---------------------------------------------------------------- uv + Python
step "Setting up Python $PYTHON_VERSION (via uv)"
UV="$TOOLS/uv/uv"
if [ ! -x "$UV" ]; then
  case "$PLATFORM-$CPU" in
    mac-arm64) uvt=aarch64-apple-darwin ;;
    mac-x64) uvt=x86_64-apple-darwin ;;
    linux-arm64) uvt=aarch64-unknown-linux-gnu ;;
    linux-x64) uvt=x86_64-unknown-linux-gnu ;;
  esac
  download "https://github.com/astral-sh/uv/releases/latest/download/uv-$uvt.tar.gz" "$DOWNLOADS/uv.tar.gz"
  rm -rf "$TOOLS/uv" && mkdir -p "$TOOLS/uv"
  tar -xzf "$DOWNLOADS/uv.tar.gz" -C "$TOOLS/uv" --strip-components 1
fi
# Keep uv's Python + cache inside our folder so uninstall is just "delete the folder".
export UV_PYTHON_INSTALL_DIR="$TOOLS/python"
export UV_CACHE_DIR="$INSTALL_DIR/cache/uv"
export UV_PYTHON_PREFERENCE=only-managed
export UV_NO_PROGRESS=1
"$UV" python install "$PYTHON_VERSION"

# ---------------------------------------------------------------- Node.js
step "Setting up Node.js (to build the caption display)"
NODE_BIN=""
if command -v node >/dev/null 2>&1; then
  v="$(node -v 2>/dev/null | sed 's/^v//')"
  if [ "${v%%.*}" -ge 20 ] 2>/dev/null; then NODE_BIN="$(dirname "$(command -v node)")"; info "Using installed Node $v"; fi
fi
if [ -z "$NODE_BIN" ]; then
  NODE_DIR="$TOOLS/node"
  if [ ! -x "$NODE_DIR/bin/node" ]; then
    base="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
    os=$( [ "$PLATFORM" = mac ] && echo darwin || echo linux )
    sums="$(curl -fsSL "$base/SHASUMS256.txt")" || die "couldn't reach nodejs.org"
    line="$(printf '%s\n' "$sums" | grep -E "  node-v[0-9.]+-$os-$CPU\.tar\.gz$" | head -1)"
    [ -n "$line" ] || die "couldn't find a Node.js download for $os-$CPU"
    file="${line##* }"; want="${line%% *}"
    download "$base/$file" "$DOWNLOADS/$file"
    [ "$(sha256 "$DOWNLOADS/$file")" = "$want" ] || die "Node.js download checksum mismatch"
    rm -rf "$NODE_DIR" && mkdir -p "$NODE_DIR"
    tar -xzf "$DOWNLOADS/$file" -C "$NODE_DIR" --strip-components 1
  fi
  NODE_BIN="$NODE_DIR/bin"
  info "Node $("$NODE_BIN/node" -v)"
fi
export PATH="$NODE_BIN:$PATH"
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
export COREPACK_HOME="$INSTALL_DIR/cache/corepack"
COREPACK="$NODE_BIN/corepack"
[ -x "$COREPACK" ] || COREPACK="$(command -v corepack)" || die "corepack not found (comes with Node.js)"

# ---------------------------------------------------------------- source
step "Downloading Caption Guru '$REF'"
commit="$(github_api "commits/$REF" 2>/dev/null | grep -o '"sha": *"[0-9a-f]\{40\}"' | head -1 | grep -o '[0-9a-f]\{40\}' || true)"
[ -n "$commit" ] || warn "Couldn't resolve '$REF' via the GitHub API; trying a direct download."
got=0
for url in ${commit:+"https://github.com/$OWNER/$REPO/archive/$commit.tar.gz"} \
           "https://github.com/$OWNER/$REPO/archive/refs/heads/$REF.tar.gz" \
           "https://github.com/$OWNER/$REPO/archive/refs/tags/$REF.tar.gz"; do
  if download "$url" "$DOWNLOADS/source.tar.gz" 2>/dev/null; then got=1; break; fi
done
[ "$got" = 1 ] || die "No branch, tag or commit called '$REF' was found in $OWNER/$REPO."

rm -rf "$INSTALL_DIR/app-new" && mkdir -p "$INSTALL_DIR/app-new"
tar -xzf "$DOWNLOADS/source.tar.gz" -C "$INSTALL_DIR/app-new" --strip-components 1
[ -f "$INSTALL_DIR/app-new/desktop/pyproject.toml" ] || die "'$REF' doesn't contain the desktop app (desktop/pyproject.toml)."
rm -rf "$APP_DIR" && mv "$INSTALL_DIR/app-new" "$APP_DIR"

# ---------------------------------------------------------------- frontend
step "Building the caption display (frontend)"
(cd "$APP_DIR" && "$COREPACK" pnpm install --frozen-lockfile && "$COREPACK" pnpm --filter @captions/display build)
[ -f "$APP_DIR/packages/display/dist/index.html" ] || die "Frontend build didn't produce packages/display/dist/index.html"
rm -rf "$WEB_DIR" && cp -R "$APP_DIR/packages/display/dist" "$WEB_DIR"
info "Cleaning up build tools' node_modules"
rm -rf "$APP_DIR/node_modules" "$APP_DIR"/packages/*/node_modules

# ---------------------------------------------------------------- Python app
step "Installing the desktop app and speech engines (this is the big one)"
pyproject="$APP_DIR/desktop/pyproject.toml"
has_extra() { grep -q "^$1 *=" "$pyproject"; }
extras="server,audio,asr"
# pywebview's native window needs GTK/Qt system packages on Linux; there the app
# uses Chrome kiosk / the browser instead, like the Linux release build.
[ "$PLATFORM" = mac ] && has_extra desktop && extras="$extras,desktop"
HAS_STREAMING=0
has_extra streaming && { extras="$extras,streaming"; HAS_STREAMING=1; }
[ "$PLATFORM-$CPU" = mac-arm64 ] && has_extra mlx && extras="$extras,mlx"
info "extras: $extras"
rm -rf "$VENV"
"$UV" venv --python "$PYTHON_VERSION" "$VENV"
(cd "$APP_DIR" && "$UV" pip install --python "$VENV/bin/python" -e "./desktop[$extras]")
CAPTIONS="$VENV/bin/captions"
[ -x "$CAPTIONS" ] || die "Install finished but $CAPTIONS is missing."

# ---------------------------------------------------------------- launchers
step "Creating launchers"
short="${commit:0:7}"; [ -n "$short" ] || short="?"
cat >"$INSTALL_DIR/version.txt" <<EOF
ref=$REF
commit=$commit
installed=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

# Keep a copy of this script for the uninstaller (and for re-running).
self="$INSTALL_DIR/install.sh"
if [ -f "${BASH_SOURCE[0]:-}" ]; then cp "${BASH_SOURCE[0]}" "$self"
else download "https://github.com/$OWNER/$REPO/releases/download/installer/install.sh" "$self" || true; fi
chmod +x "$self" 2>/dev/null || true

cat >"$INSTALL_DIR/run.sh" <<EOF
#!/bin/bash
# Caption Guru launcher ($REF @ $short). Extra arguments go to 'captions serve'.
printf '\\033]0;Caption Guru [$REF @ $short]\\007'
echo "Caption Guru  -  version: $REF ($short)"
echo "Close this window (or press Ctrl+C) to stop captions."
echo
"$CAPTIONS" serve --web "$WEB_DIR" "\$@"
rc=\$?
if [ \$rc -ne 0 ]; then
  echo
  echo "Caption Guru exited with an error. Copy the text above into your bug report."
  read -r -p "Press Enter to close." _ </dev/tty || true
fi
exit \$rc
EOF
chmod +x "$INSTALL_DIR/run.sh"

remove_launchers
rm -rf "$LAUNCHERS" && mkdir -p "$LAUNCHERS"

add_launcher() { # name, args, description
  local name="$1" args="$2" desc="$3"
  if [ "$PLATFORM" = mac ]; then
    # .command files open in Terminal on double-click.
    printf '#!/bin/bash\nexec "%s" %s\n' "$INSTALL_DIR/run.sh" "$args" >"$LAUNCHERS/$name.command"
    chmod +x "$LAUNCHERS/$name.command"
  else
    cat >"$LAUNCHERS/$name.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=$desc
Exec="$INSTALL_DIR/run.sh" $args
Terminal=true
Icon=accessories-text-editor
Categories=AudioVideo;
EOF
    chmod +x "$LAUNCHERS/$name.desktop"
  fi
}

add_launcher "Caption Guru" "" "Live captions ($REF)"
add_launcher "Caption Guru (windowed)" "--windowed" "Live captions in a window"
add_launcher "Caption Guru (demo - no mic)" "--demo --windowed" "Scripted captions for testing the display"
[ "$HAS_STREAMING" = 1 ] && add_launcher "Caption Guru (streaming - Nemotron)" "--engine sherpa-onnx" \
  "True word-by-word streaming engine (first run downloads ~460 MB)"

if [ -f "$WEB_DIR/control.html" ]; then
  if [ "$PLATFORM" = mac ]; then
    cat >"$LAUNCHERS/Caption Guru Settings.webloc" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>URL</key><string>http://127.0.0.1:$PORT/control.html</string></dict></plist>
EOF
  else
    cat >"$LAUNCHERS/Caption Guru Settings.desktop" <<EOF
[Desktop Entry]
Type=Link
Name=Caption Guru Settings
URL=http://127.0.0.1:$PORT/control.html
Icon=preferences-system
EOF
  fi
fi

if [ "$PLATFORM" = mac ]; then
  printf '#!/bin/bash\nbash "%s" --uninstall --dir "%s"\nread -r -p "Press Enter to close." _\n' "$self" "$INSTALL_DIR" \
    >"$LAUNCHERS/Uninstall Caption Guru.command"
  chmod +x "$LAUNCHERS/Uninstall Caption Guru.command"
else
  cat >"$LAUNCHERS/Uninstall Caption Guru.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Uninstall Caption Guru
Exec=bash "$self" --uninstall --dir "$INSTALL_DIR"
Terminal=true
Icon=edit-delete
EOF
  chmod +x "$LAUNCHERS/Uninstall Caption Guru.desktop"
fi

# The same launchers in a 'Caption Guru' folder on the Desktop (and, on Linux,
# in the applications menu).
mkdir -p "$DESKTOP_FOLDER"
cp "$LAUNCHERS"/* "$DESKTOP_FOLDER"/
if [ "$PLATFORM" = linux ]; then
  mkdir -p "$HOME/.local/share/applications"
  for f in "$LAUNCHERS"/*.desktop; do
    slug="$(basename "$f" .desktop | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9-' | tr -s '-')"
    slug="${slug#caption-guru}"; slug="${slug#-}"
    cp "$f" "$HOME/.local/share/applications/caption-guru${slug:+-$slug}.desktop"
  done
  # GNOME only runs desktop-folder launchers that are marked trusted.
  if command -v gio >/dev/null 2>&1; then
    for f in "$DESKTOP_FOLDER"/*.desktop; do gio set "$f" metadata::trusted true 2>/dev/null || true; done
  fi
fi

# ---------------------------------------------------------------- done
step "Done!"
info "Installed $REF ($short) into $INSTALL_DIR"
info "The launchers are in the 'Caption Guru' folder on your Desktop."
[ "$HAS_STREAMING" = 1 ] && info "This version includes the streaming engine: use 'Caption Guru (streaming - Nemotron)'."
[ -f "$WEB_DIR/control.html" ] && info "A Settings window opens alongside the captions (or use 'Caption Guru Settings' while it runs)."
[ "$PLATFORM" = mac ] && info "The first time, macOS asks to let Terminal use the microphone - allow it."
info "The first run downloads a speech model, so give it a minute."
info "To try another branch later, just run the installer again."
[ "$OPEN_FOLDER" = 1 ] && open_path "$DESKTOP_FOLDER"
exit 0
