#!/usr/bin/env bash
# Installs the game-studio and grill-me skills and the studio-* agents into Codex.
# Symlinks into this repository by default, so git pull updates the studio at once.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  install-codex.sh --user                 into ~/.agents/skills and ~/.codex/agents
  install-codex.sh --project <DIR>        into <DIR>/.agents/skills and <DIR>/.codex/agents
Options:
  --copy        copy instead of symlinking (when .agents/.codex are committed to the game)
  --force       replace existing files with the same names
  --uninstall   remove what this script installed
EOF
}

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_src="$repo_dir/plugins/game-studio/skills"
agents_src="$repo_dir/codex/agents"

scope=""
target_dir=""
mode="link"
force=0
uninstall=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) scope="user"; shift ;;
    --project) scope="project"; target_dir="${2:?--project needs a path}"; shift 2 ;;
    --copy) mode="copy"; shift ;;
    --force) force=1; shift ;;
    --uninstall) uninstall=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$scope" in
  user)
    skills_dst="$HOME/.agents/skills"
    agents_dst="${CODEX_HOME:-$HOME/.codex}/agents"
    ;;
  project)
    [[ -d "$target_dir" ]] || { echo "No such folder: $target_dir" >&2; exit 2; }
    target_dir="$(cd "$target_dir" && pwd)"
    skills_dst="$target_dir/.agents/skills"
    agents_dst="$target_dir/.codex/agents"
    ;;
  *) usage >&2; exit 2 ;;
esac

install_item() {
  local source="$1" destination="$2"
  if [[ -e "$destination" || -L "$destination" ]]; then
    if [[ -L "$destination" && "$(readlink "$destination")" == "$source" ]]; then
      echo "already installed: $destination"
      return
    fi
    if [[ $force -eq 0 ]]; then
      echo "taken: $destination (run with --force to replace it)" >&2
      return 1
    fi
    rm -rf "$destination"
  fi
  if [[ "$mode" == "copy" ]]; then
    cp -R "$source" "$destination"
  else
    ln -s "$source" "$destination"
  fi
  echo "installed: $destination"
}

remove_item() {
  local destination="$1"
  if [[ -e "$destination" || -L "$destination" ]]; then
    rm -rf "$destination"
    echo "removed: $destination"
  fi
}

items=()
for skill in game-studio grill-me; do
  items+=("$skills_src/$skill|$skills_dst/$skill")
done
for agent in "$agents_src"/studio-*.toml; do
  items+=("$agent|$agents_dst/$(basename "$agent")")
done

if [[ $uninstall -eq 1 ]]; then
  for item in "${items[@]}"; do
    remove_item "${item#*|}"
  done
  exit 0
fi

mkdir -p "$skills_dst" "$agents_dst"
status=0
for item in "${items[@]}"; do
  install_item "${item%%|*}" "${item#*|}" || status=1
done

if [[ "$scope" == "project" && "$mode" == "link" ]]; then
  cat <<EOF

The symlinks point to $repo_dir and work only on this machine.
Do not commit them to the game: add .agents/skills/game-studio, .agents/skills/grill-me
and .codex/agents/studio-*.toml to .gitignore, or install with --copy.
EOF
fi
echo
echo "A new Codex session will see the \$game-studio skill and the studio-* agents."
exit $status
