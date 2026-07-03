#!/usr/bin/env bash
# curryflows: environment-precondition dry-run (CANON [O], fail-closed seal gate).
#
# Before a contract is sealed, the seal-gate runs this to verify the contract's
# declared ENVIRONMENT PRECONDITIONS actually hold on a REAL fresh worktree at the
# base ref the worker would launch from -- baseline-green test count, venv/toolchain
# installable, expected drift shape, etc. This catches the failure mode "contract
# assumed an environment precondition that was never verified" at SEAL time (on a
# throwaway worktree), instead of after launching a worker that hits it at STEP-0
# and blocks -- which burns a review round, a worker stop, a contract amendment and
# a human decision each time.
#
# The contract's `preconditions:` field (in the ```yaml fenced block) is a list of
# shell CHECKS; each list item is a command that must exit 0 in the fresh worktree.
# The token $WT in a check is substituted with the throwaway worktree's absolute
# path. Any check exiting non-zero => seal blocked (this script exits 1) and the
# contract goes back to scoping for revision. Nothing here touches the worker's real
# worktree or main; the throwaway worktree is always removed on exit.
#
# This is a subagent/operator action (it creates a worktree and runs commands), not
# a coordinator action (CANON [J]). The coordinator DECIDES to gate on it; a subagent
# EXECUTES it and reports the JSON back.
#
# Usage:
#   precondition-dryrun.sh --project <dir> --base <ref> --contract <file> \
#                          [--name <tag>] [--timeout <s>]
# Exit codes:
#   0  all declared preconditions hold on the fresh worktree
#   1  one or more preconditions failed (details in JSON on stdout)
#   64 usage / input error
#   65 no `preconditions` checks found in the contract (fail-closed: must declare them)
#   70 could not create the throwaway worktree at --base

set -uo pipefail

PROJECT="" BASE="" CONTRACT="" NAME="dryrun-$$" TIMEOUT=600
while [ $# -gt 0 ]; do
  case "$1" in
    --project)  PROJECT="$2"; shift 2;;
    --base)     BASE="$2"; shift 2;;
    --contract) CONTRACT="$2"; shift 2;;
    --name)     NAME="$2"; shift 2;;
    --timeout)  TIMEOUT="$2"; shift 2;;
    *) echo "usage error: unknown arg $1" >&2; exit 64;;
  esac
done
[ -n "$PROJECT" ] && [ -d "$PROJECT" ] || { echo "usage: --project <existing dir>" >&2; exit 64; }
[ -n "$BASE" ] || { echo "usage: --base <ref>" >&2; exit 64; }
[ -n "$CONTRACT" ] && [ -f "$CONTRACT" ] || { echo "usage: --contract <existing file>" >&2; exit 64; }

# jstr: JSON-encode stdin as a string literal (python3 is a hard dep across the skill).
jstr() { python3 -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read()))'; }

# Extract the preconditions checks: `- ` list items under a top-level `preconditions:`
# key inside the ```yaml block (or the whole file if unfenced). Stops at the next
# same-or-less-indented key so sibling fields are not swept in.
mapfile -t CHECKS < <(python3 - "$CONTRACT" <<'PY'
import sys, re
text = open(sys.argv[1], errors="strict").read()
fences = re.findall(r"```ya?ml[^\n]*\n(.*?)```", text, re.DOTALL)
region = "\n".join(fences) if fences else text
in_block, base_indent = False, None
out = []
for raw in region.splitlines():
    if not raw.strip():
        continue
    indent = len(raw) - len(raw.lstrip())
    key = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_\-]*)\s*:", raw)
    if not in_block:
        if key and key.group(1).lower().replace("-", "_") == "preconditions":
            in_block, base_indent = True, indent
        continue
    if key and indent <= base_indent:      # next sibling/parent key -> block ended
        break
    m = re.match(r"^\s*-\s+(.*)$", raw)
    if m:
        v = m.group(1).strip()
        if v and v[0] in "\"'":                 # quoted scalar: take up to matching quote
            q = v[0]
            end = v.find(q, 1)
            v = v[1:end] if end != -1 else v[1:]
        else:                                    # bare scalar: drop a trailing ` # comment`
            v = v.split(" #", 1)[0].rstrip()
        if v:
            out.append(v)
for c in out:
    print(c)
PY
)

if [ "${#CHECKS[@]}" -eq 0 ]; then
  echo '{"all_ok": false, "error": "no `preconditions` checks found in contract (CANON [O] fail-closed: declare them as shell checks)", "results": []}'
  exit 65
fi

WTROOT="${TMPDIR:-/tmp}/cfx-precheck-${NAME}"
cleanup() {
  git -C "$PROJECT" worktree remove --force "$WTROOT" >/dev/null 2>&1
  git -C "$PROJECT" worktree prune >/dev/null 2>&1
}
trap cleanup EXIT

if ! git -C "$PROJECT" worktree add --detach "$WTROOT" "$BASE" >/dev/null 2>&1; then
  echo "{\"all_ok\": false, \"error\": \"could not create throwaway worktree at base\", \"base\": $(printf '%s' "$BASE" | jstr), \"results\": []}"
  exit 70
fi

ALL_OK=1
RESULTS=""
idx=0
for chk in "${CHECKS[@]}"; do
  idx=$((idx + 1))
  cmd="${chk//\$WT/$WTROOT}"
  out="$(cd "$WTROOT" && timeout "$TIMEOUT" bash -c "$cmd" 2>&1)"
  rc=$?
  [ "$rc" -eq 0 ] || ALL_OK=0
  ok=$([ "$rc" -eq 0 ] && echo true || echo false)
  ctext=$(printf '%s' "$chk" | jstr)
  ttext=$(printf '%s' "$out" | tail -c 500 | jstr)
  RESULTS="${RESULTS}${RESULTS:+,}{\"idx\":${idx},\"check\":${ctext},\"exit_code\":${rc},\"ok\":${ok},\"tail\":${ttext}}"
done

allok=$([ "$ALL_OK" -eq 1 ] && echo true || echo false)
echo "{\"all_ok\": ${allok}, \"base\": $(printf '%s' "$BASE" | jstr), \"results\": [${RESULTS}]}"
[ "$ALL_OK" -eq 1 ] || exit 1
exit 0
