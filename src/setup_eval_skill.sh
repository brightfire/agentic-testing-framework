#!/usr/bin/env bash
# setup_eval_skill.sh — Set up an eval environment for a single skill version.
#
# Extracts a skill from a git ref, creates a suffixed copy in eval-skills/,
# and rewrites the SKILL.md name field to match the suffixed directory.
#
# Usage:
#   setup_eval_skill.sh --skill-dir <abs-path> --hash <commit-hash> --label <version-label>
#
# Output: prints the suffixed directory path to stdout (logs go to stderr).
# Exit: 0 on success, 1 on error.

set -euo pipefail

# --- Parse args ---
SKILL_DIR=""
HASH=""
LABEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill-dir)
      SKILL_DIR="$2"; shift 2 ;;
    --hash)
      HASH="$2"; shift 2 ;;
    --label)
      LABEL="$2"; shift 2 ;;
    *)
      echo "Error: unknown argument '$1'" >&2
      exit 1 ;;
  esac
done

# --- Validate required args ---
if [[ -z "$SKILL_DIR" || -z "$HASH" || -z "$LABEL" ]]; then
  echo "Error: --skill-dir, --hash, and --label are all required" >&2
  exit 1
fi

# --- Resolve repo root from skill-dir ---
REPO_ROOT=$(cd "$SKILL_DIR" && git rev-parse --show-toplevel 2>&1) || {
  echo "Error: failed to find git repo root from '$SKILL_DIR'" >&2
  exit 1
}

# --- Compute relative skill path ---
SKILL_REL=$(cd "$SKILL_DIR" && git rev-parse --show-prefix 2>&1) || {
  echo "Error: failed to compute relative skill path" >&2
  exit 1
}
# Strip trailing slash
SKILL_REL="${SKILL_REL%/}"

if [[ -z "$SKILL_REL" ]]; then
  echo "Error: skill-dir is at repo root, cannot determine relative path" >&2
  exit 1
fi

# --- Get skill name from SKILL.md frontmatter at the given hash ---
SKILL_NAME=$(git -C "$REPO_ROOT" show "${HASH}:${SKILL_REL}/SKILL.md" 2>/dev/null | \
  grep '^name:' | head -1 | sed 's/^name:[[:space:]]*//; s/^"//; s/"$//; s/^'"'"'//; s/'"'"'$//') || {
  echo "Error: failed to read SKILL.md from ${HASH}:${SKILL_REL}/SKILL.md" >&2
  exit 1
}

if [[ -z "$SKILL_NAME" ]]; then
  echo "Error: no 'name:' field found in SKILL.md at ${HASH}:${SKILL_REL}/SKILL.md" >&2
  exit 1
fi

# --- Normalize label: [a-z0-9-] ---
LABEL_NORMALIZED=$(echo "$LABEL" | tr '/' '-' | tr '[:upper:]' '[:lower:]' | tr -d '._')

# --- Short hash via git rev-parse --short (variable length; matches git default) ---
SHORT_HASH=$(git -C "$REPO_ROOT" rev-parse --short "$HASH")

# --- Create suffixed directory via mktemp ---
EVAL_SKILLS_DIR="$HOME/.openclaw/workspace/eval-skills"
mkdir -p "$EVAL_SKILLS_DIR"

SUFFIXED_DIR=$(mktemp -d "${EVAL_SKILLS_DIR}/${SKILL_NAME}-${LABEL_NORMALIZED}-${SHORT_HASH}-XXXX") || {
  echo "Error: failed to create suffixed directory" >&2
  exit 1
}

# mktemp uses mixed case — normalize to lowercase for OpenClaw [a-z0-9-] charset
LOWER_DIR=$(echo "$SUFFIXED_DIR" | tr 'A-Z' 'a-z')
mv "$SUFFIXED_DIR" "$LOWER_DIR" 2>/dev/null && SUFFIXED_DIR="$LOWER_DIR"

# --- Extract skill files via git archive, stripping the relative path prefix ---
# Count path components in SKILL_REL to determine --strip-components
COMPONENT_COUNT=$(echo "$SKILL_REL" | tr '/' '\n' | wc -l)

git -C "$REPO_ROOT" archive "$HASH" -- "${SKILL_REL}/" | \
  tar -x --strip-components="$COMPONENT_COUNT" -C "$SUFFIXED_DIR/" || {
  echo "Error: failed to extract skill files from git archive" >&2
  rm -rf "$SUFFIXED_DIR"
  exit 1
}

# --- Rewrite name: and force user-invocable: true — scoped to the frontmatter block ---
# The eval prompt prefix invokes the staged copy via /skill <name>; skills that are
# not user-invocable get no slash-command spec, so the prefix cannot resolve. Staged
# eval copies are unique-named and ephemeral, so forcing this is safe.
# Both rewrites are scoped to the YAML frontmatter (between the leading `---` line and
# its closing `---`): a naive whole-file sed would also rewrite column-zero `name:` /
# `user-invocable:` lines in the body (e.g. inside fenced YAML examples), silently
# diverging the staged copy from the source under test.
SUFFIXED_NAME=$(basename "$SUFFIXED_DIR")
awk -v suffix="$SUFFIXED_NAME" '
  BEGIN { infm = 0; seen_inv = 0 }
  NR == 1 && $0 == "---" { infm = 1; print; next }
  infm && $0 == "---" { if (!seen_inv) print "user-invocable: true"; infm = 0; print; next }
  infm && $0 ~ /^user[-_]invocable:/ { print "user-invocable: true"; seen_inv = 1; next }
  infm && $0 ~ /^name:/ { print "name: " suffix; next }
  { print }
' "$SUFFIXED_DIR/SKILL.md" > "$SUFFIXED_DIR/SKILL.md.awk-tmp" && mv "$SUFFIXED_DIR/SKILL.md.awk-tmp" "$SUFFIXED_DIR/SKILL.md" || {
  echo "Error: failed to rewrite SKILL.md frontmatter (name / user-invocable)" >&2
  rm -rf "$SUFFIXED_DIR"
  exit 1
}

# --- Output the suffixed directory path to stdout ---
echo "$SUFFIXED_DIR"
