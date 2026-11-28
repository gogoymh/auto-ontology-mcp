#!/usr/bin/env bash
# Check that every version tag has a GitHub Release and that the next tag gets one automatically.
# Usage: scripts/check_releases.sh published   # every local v* tag is on origin and has a Release
#        scripts/check_releases.sh workflow    # HEAD carries release.yml, so a new v* tag from here releases itself
#        scripts/check_releases.sh latest      # the newest tag is marked Latest on GitHub
#        scripts/check_releases.sh main        # origin/main carries release.yml
#        scripts/check_releases.sh tagged vX   # tag vX on origin points at a commit with that version and release.yml
#        scripts/check_releases.sh auto vX     # the Release for vX was created by the release workflow
#        scripts/check_releases.sh glama       # the Glama score badge rates maintenance A
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
  main)
    git fetch -q origin main
    git cat-file -e "origin/main:$wf" 2>/dev/null || { echo "origin/main has no $wf"; exit 1; }
    echo "ok: origin/main carries $wf"
    ;;
  tagged)
    tag=${2:?tag required}
    sha=$(git ls-remote --tags origin "refs/tags/$tag^{}" | cut -f1)
    [ -n "$sha" ] || sha=$(git ls-remote --tags origin "refs/tags/$tag" | cut -f1)
    [ -n "$sha" ] || { echo "tag $tag is not on origin"; exit 1; }
    git cat-file -e "$sha^{commit}" 2>/dev/null || git fetch -q origin "refs/tags/$tag:refs/tags/$tag"
    git cat-file -e "$sha:$wf" 2>/dev/null || { echo "$tag ($sha) has no $wf"; exit 1; }
    pkg=$(git show "$sha:src/auto_ontology_mcp/__init__.py" | sed -n 's/^__version__ = "\(.*\)"/\1/p')
    proj=$(git show "$sha:pyproject.toml" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
    [ "v$pkg" = "$tag" ] && [ "v$proj" = "$tag" ] || { echo "$tag has __version__ $pkg, pyproject $proj"; exit 1; }
    echo "ok: $tag -> $sha with version $pkg and $wf"
    ;;
  auto)
    tag=${2:?tag required}
    author=$(gh release view "$tag" --json author --jq .author.login) || { echo "no Release for $tag"; exit 1; }
    case "$author" in github-actions*|app/github-actions) ;; *) echo "Release $tag was created by $author, not the workflow"; exit 1 ;; esac
    run=$(gh run list --workflow release.yml --branch "$tag" --json conclusion --jq '.[0].conclusion')
    [ "$run" = success ] || { echo "release workflow run for $tag: ${run:-none}"; exit 1; }
    echo "ok: $tag released by $author (workflow $run)"
    ;;
  glama)
    desc=$(curl -fsSL "https://glama.ai/mcp/servers/gogoymh/auto-ontology-mcp/badges/score.svg?t=$(date +%s)" | sed -n 's:.*<desc>\(.*\)</desc>.*:\1:p')
    grep -q 'maintenance rated A' <<<"$desc" || { echo "Glama: $desc"; exit 1; }
    echo "ok: $desc"
    ;;
  *) echo "usage: $0 published|workflow|latest|main|tagged vX|auto vX|glama" >&2; exit 2 ;;
esac
