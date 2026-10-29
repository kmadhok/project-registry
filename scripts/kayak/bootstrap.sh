#!/usr/bin/env bash
# Manual, idempotent setup of a kayak-style build host user account.
#
#   scripts/kayak/bootstrap.sh [--email <address>] [--root <dir>]
#
# Sets a missing git identity, points ~/.nvm/current at nvm's default node,
# links node/npm/npx/codex into ~/.local/bin, adds PATH entries for login
# shells, SSH non-interactive shells, and future systemd user sessions, and
# creates ~/scratch and ~/runs. Every step prints OK (already right), CHANGED
# (fixed now), or WARN (left alone; needs a human). Existing files are backed
# up to <file>.bak.<timestamp> before they are edited and never overwritten
# when they are real files rather than links this script manages.
#
# --root <dir> (or KAYAK_BOOTSTRAP_ROOT) treats <dir> as $HOME and isolates git
# to <dir>/.gitconfig, so tests never touch the real account. Nothing here
# installs packages, starts services, or changes a running systemd manager.
set -u

NAME_DEFAULT="Kanu Madhok"
BLOCK_BEGIN="# >>> kayak-bootstrap PATH >>>"
BLOCK_END="# <<< kayak-bootstrap PATH <<<"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WARNINGS=0
BACKED_UP=""

ok() { echo "OK      $*"; }
changed() { echo "CHANGED $*"; }
warn() { echo "WARN    $*"; WARNINGS=$((WARNINGS + 1)); }

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

backup() {
  # Copy an existing file aside before the first edit; links are left as is.
  # Once per file per run, so a second edit cannot clobber the original copy.
  local file="$1" dest n=0
  [[ -f "$file" && ! -L "$file" ]] || return 0
  [[ " $BACKED_UP " == *" $file "* ]] && return 0
  dest="$file.bak.$STAMP"
  while [[ -e "$dest" ]]; do n=$((n + 1)); dest="$file.bak.$STAMP.$n"; done
  cp -p "$file" "$dest"
  BACKED_UP="$BACKED_UP $file"
  echo "        backup: $dest"
}

setup_git() {
  local name email
  name="$(git config --global --get user.name 2>/dev/null || true)"
  if [[ -n "$name" ]]; then
    ok "git user.name is '$name'"
  else
    backup "$GITCONFIG"
    git config --global user.name "$NAME_DEFAULT"
    changed "git user.name set to '$NAME_DEFAULT'"
  fi
  email="$(git config --global --get user.email 2>/dev/null || true)"
  if [[ -n "$email" ]]; then
    ok "git user.email is '$email'"
  elif [[ -n "$EMAIL" ]]; then
    backup "$GITCONFIG"
    git config --global user.email "$EMAIL"
    changed "git user.email set to '$EMAIL'"
  else
    warn "git user.email is unset; rerun with --email <address> or GIT_EMAIL=<address>"
  fi
}

resolve_alias() {
  # Print the installed versions/node/<dir> name an nvm alias resolves to.
  local want="$1" depth="${2:-0}" versions="$NVM_DIR/versions/node" match
  [[ $depth -gt 8 ]] && return 1
  case "$want" in
    node|stable) want="v" ;;
    v*|[0-9]*) [[ "$want" == v* ]] || want="v$want" ;;
    *)
      [[ -f "$NVM_DIR/alias/$want" ]] || return 1
      resolve_alias "$(tr -d '[:space:]' < "$NVM_DIR/alias/$want")" $((depth + 1))
      return
      ;;
  esac
  [[ -d "$versions" ]] || return 1
  if [[ -d "$versions/$want" ]]; then
    echo "$want"
    return
  fi
  # A partial version (v22, v22.11) picks the newest installed match.
  match="$(find "$versions" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | grep -E "^${want//./\\.}(\.|$)" | sort -V | tail -n 1)"
  [[ "$want" == v ]] && match="$(find "$versions" -mindepth 1 -maxdepth 1 -type d \
    -name 'v*' -printf '%f\n' | sort -V | tail -n 1)"
  [[ -n "$match" ]] && echo "$match"
}

setup_nvm() {
  local current="$NVM_DIR/current" default version target
  if [[ ! -d "$NVM_DIR" ]]; then
    warn "nvm not found at $NVM_DIR; node/npm/npx/codex links skipped"
    return 1
  fi
  if [[ ! -f "$NVM_DIR/alias/default" ]]; then
    warn "nvm has no default alias ($NVM_DIR/alias/default); run 'nvm alias default <version>'"
    return 1
  fi
  default="$(tr -d '[:space:]' < "$NVM_DIR/alias/default")"
  if ! version="$(resolve_alias "$default")" || [[ -z "$version" ]]; then
    warn "nvm default '$default' does not match an installed version"
    return 1
  fi
  target="$NVM_DIR/versions/node/$version"
  if [[ -L "$current" && "$(readlink "$current")" == "$target" ]]; then
    ok "$current -> $version"
  elif [[ -L "$current" || ! -e "$current" ]]; then
    ln -sfn "$target" "$current"
    changed "$current -> $version (nvm default '$default')"
  else
    warn "$current is a real file or directory; left alone"
    return 1
  fi
}

link_tools() {
  local bin="$HOME/.local/bin" tool source link
  mkdir -p "$bin"
  for tool in node npm npx codex; do
    source="$NVM_DIR/current/bin/$tool"
    link="$bin/$tool"
    if [[ ! -e "$source" ]]; then
      warn "$tool is not installed under $NVM_DIR/current/bin; $link not linked"
    elif [[ -L "$link" && "$(readlink "$link")" == "$source" ]]; then
      ok "$link -> $source"
    elif [[ -L "$link" || ! -e "$link" ]]; then
      ln -sfn "$source" "$link"
      changed "$link -> $source"
    else
      warn "$link is a real file; left alone (remove it to use the nvm $tool)"
    fi
  done
}

path_block() {
  cat <<EOF
$BLOCK_BEGIN
# Managed by project-registry scripts/kayak/bootstrap.sh. Placed above any
# interactive-only guard so ssh commands and unattended shells see the tools.
for _kayak_dir in "\$HOME/.nvm/current/bin" "\$HOME/.local/bin" "\$HOME/bin"; do
  case ":\$PATH:" in *":\$_kayak_dir:"*) ;; *) PATH="\$_kayak_dir:\$PATH" ;; esac
done
unset _kayak_dir
export PATH
$BLOCK_END
EOF
}

strip_block() {
  awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip' "$1"
}

block_line() { grep -nxF "$BLOCK_BEGIN" "$1" 2>/dev/null | head -n 1 | cut -d: -f1; }

ensure_block() {
  # Keep exactly one current PATH block, before the file's first `return`
  # (bashrc's interactive guard) when there is one.
  local file="$1" label="$2" at guard tmp
  # Edit a dotfiles-managed symlink's target rather than replacing the link.
  [[ -L "$file" ]] && file="$(readlink -f "$file")"
  at="$(block_line "$file")"
  guard="$(grep -nE '^[^#]*\breturn\b' "$file" 2>/dev/null | head -n 1 | cut -d: -f1)"
  if [[ -n "$at" ]] && { [[ -z "$guard" ]] || (( at < guard )); } \
    && diff -q <(awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" \
      '$0 == b { on = 1 } on { print } $0 == e { exit }' "$file") <(path_block) >/dev/null; then
    ok "$label PATH block in $file"
    return
  fi
  tmp="$(mktemp "$file.kayak.XXXXXX")"
  if [[ -e "$file" ]]; then
    backup "$file"
    { path_block; echo; strip_block "$file"; } > "$tmp"
    chmod --reference="$file" "$tmp" 2>/dev/null || true
  else
    path_block > "$tmp"
  fi
  mv "$tmp" "$file"
  changed "$label PATH block in $file"
}

setup_shells() {
  local login="$HOME/.profile"
  # bash reads the first of these that exists and ignores .profile after it.
  if [[ -f "$HOME/.bash_profile" ]]; then login="$HOME/.bash_profile"
  elif [[ -f "$HOME/.bash_login" ]]; then login="$HOME/.bash_login"
  fi
  ensure_block "$login" "login-shell"
  ensure_block "$HOME/.bashrc" "ssh/non-interactive"
}

setup_systemd_env() {
  # Read by the systemd user manager when it next starts; this script never
  # pushes the value into a running manager.
  local dir="$HOME/.config/environment.d" file want
  file="$dir/50-kayak-path.conf"
  want="# Managed by project-registry scripts/kayak/bootstrap.sh
PATH=$HOME/bin:$HOME/.local/bin:$HOME/.nvm/current/bin:\${PATH:-/usr/local/bin:/usr/bin:/bin}"
  mkdir -p "$dir"
  if [[ -f "$file" && "$(cat "$file")" == "$want" ]]; then
    ok "systemd user PATH in $file"
    return
  fi
  backup "$file"
  printf '%s\n' "$want" > "$file"
  changed "systemd user PATH in $file (applies to the next user-manager start)"
}

setup_dirs() {
  local dir
  for dir in "$HOME/scratch" "$HOME/runs"; do
    if [[ -d "$dir" ]]; then
      ok "directory $dir"
    elif [[ -e "$dir" ]]; then
      warn "$dir exists and is not a directory; left alone"
    else
      mkdir -p "$dir"
      changed "directory $dir"
    fi
  done
}

main() {
  local root="${KAYAK_BOOTSTRAP_ROOT:-}"
  EMAIL="${GIT_EMAIL:-}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
      --email=*) EMAIL="${1#--email=}"; shift ;;
      --root) root="${2:?--root needs a value}"; shift 2 ;;
      --root=*) root="${1#--root=}"; shift ;;
      -h|--help) usage; return 0 ;;
      *) echo "bootstrap: unknown argument: $1" >&2; usage >&2; return 2 ;;
    esac
  done
  if [[ -n "$root" ]]; then
    mkdir -p "$root"
    HOME="$(cd "$root" && pwd)"
    export HOME
    export GIT_CONFIG_GLOBAL="$HOME/.gitconfig" GIT_CONFIG_NOSYSTEM=1
    NVM_DIR="$HOME/.nvm"
  else
    NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  fi
  GITCONFIG="${GIT_CONFIG_GLOBAL:-$HOME/.gitconfig}"
  echo "kayak bootstrap for HOME=$HOME"

  setup_git
  if setup_nvm; then
    link_tools
  fi
  setup_shells
  setup_systemd_env
  setup_dirs

  echo "done: $WARNINGS warning(s)"
  return 0
}

main "$@"
