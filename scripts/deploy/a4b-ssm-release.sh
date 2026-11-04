#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
payload=$({
    printf 'A4B_FRONTEND_HELPER=$(printf %%s %q | base64 -d)\n' "$(base64 < "$root/a4b-frontend-release.py" | tr -d '\n')"
    cat "$root/deploy-a4b-frontend.sh" "$root/deploy-a4b-ec2.sh"
} | base64 | tr -d '\n')
printf -v args ' %q' "$@"
bash "$root/a4b-ssm-command.sh" "printf '%s' '$payload' | base64 -d | bash -s --$args"
