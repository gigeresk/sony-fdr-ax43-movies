#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fred Tombs
# Publish a new Windows release:  build/release.sh v1.0.0
# Tags the current commit and pushes it; GitHub Actions then builds the exe on
# Windows and creates the GitHub Release (see .github/workflows/windows-release.yml).
set -euo pipefail
v="${1:-}"
if [[ ! "$v" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: build/release.sh vMAJOR.MINOR.PATCH   (e.g. v1.0.0)"; exit 1
fi
cd "$(git rev-parse --show-toplevel)"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "You have uncommitted changes - commit them first."; exit 1
fi
if git rev-parse -q --verify "refs/tags/$v" >/dev/null; then
  echo "Tag $v already exists."; exit 1
fi
branch="$(git rev-parse --abbrev-ref HEAD)"
git push origin "$branch"
git tag -a "$v" -m "AX43 Movie Adder $v"
git push origin "$v"
echo
echo "Pushed $v. GitHub is building the Windows exe (~5 min)."
if command -v gh >/dev/null; then
  echo "Watch it with:  gh run watch     Release page:  gh release view $v --web"
fi
