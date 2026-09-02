#!/usr/bin/env bash
# Concatenates the section parts into the single review page.
set -euo pipefail
cd "$(dirname "$0")"
cat parts/*.html > crumbs-five-directions.html
echo "built crumbs-five-directions.html ($(wc -c < crumbs-five-directions.html) bytes)"
