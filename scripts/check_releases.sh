#!/usr/bin/env bash
# Check that every version tag has a GitHub Release and that the next tag gets one automatically.
# Usage: scripts/check_releases.sh published   # every local v* tag is on origin and has a Release
#        scripts/check_releases.sh workflow    # HEAD carries release.yml, so a new v* tag from here releases itself
#        scripts/check_releases.sh latest      # the newest tag is marked Latest on GitHub
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
wf=.github/workflows/release.yml

case "${1:-}" in
  published)
    remote=$(git ls-remote --tags origin 'v*' | sed 's|.*refs/tags/||; s|\^{}$||' | sort -u)
    releases=$(gh release list --limit 100 --json tagName --jq '.[].tagName')
    fail=0
    for t in $(git tag -l 'v*'); do
      grep -qx "$t" <<<"$remote" || { echo "tag $t is not on origin"; fail=1; continue; }
      grep -qx "$t" <<<"$releases" || { echo "tag $t has no GitHub Release"; fail=1; continue; }
      echo "ok $t"
    done
    exit $fail
    ;;
  workflow)
    git cat-file -e "HEAD:$wf" || { echo "HEAD has no $wf"; exit 1; }
    body=$(git show "HEAD:$wf")
    grep -Eq '^\s+- "v\*"' <<<"$body" || { echo "$wf does not trigger on v* tags"; exit 1; }
    grep -q 'gh release create "${GITHUB_REF_NAME}"' <<<"$body" || { echo "$wf does not create a release"; exit 1; }
    grep -q 'contents: write' <<<"$body" || { echo "$wf lacks contents: write"; exit 1; }
    # Run the workflow's own version guard against the next patch tag to show it passes only when versions match.
    pkg=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/auto_ontology_mcp/__init__.py)
    proj=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
    [ "$pkg" = "$proj" ] || { echo "__version__ $pkg differs from pyproject $proj"; exit 1; }
    guard=$(awk '/TAG_VERSION=/{f=1} f&&/^      - /{exit} f' <<<"$body" | sed 's/^ *//' | grep -v PKG_VERSION=)
    for tag in "v$pkg:0" "v0.0.0-not-this:1"; do
      name=${tag%:*}; want=${tag##*:}
      set +e
      ( GITHUB_REF_NAME=$name; PKG_VERSION=$pkg; eval "$guard" ) >/dev/null 2>&1
      got=$?
      set -e
      [ $((got != 0)) = "$want" ] || { echo "version guard gave $got for $name"; exit 1; }
    done
    last=$(git tag -l 'v*' --sort=-v:refname | head -1)
    echo "ok: a tag from HEAD runs $wf; guard accepts v$pkg; newest tag is $last"
    ;;
  latest)
    newest=$(git tag -l 'v*' --sort=-v:refname | head -1)
    latest=$(gh release view --json tagName --jq .tagName)
    [ "$newest" = "$latest" ] || { echo "Latest release is $latest, newest tag is $newest"; exit 1; }
    echo "ok: $latest is Latest"
    ;;
  *) echo "usage: $0 published|workflow|latest" >&2; exit 2 ;;
esac
