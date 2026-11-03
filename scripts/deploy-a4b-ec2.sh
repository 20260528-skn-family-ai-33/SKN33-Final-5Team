#!/usr/bin/env bash
# Runs as root on the existing A4B EC2 host via AWS Systems Manager.
set -euo pipefail
umask 077

image=${1:?ECR image or restore required}
public_url=${2:?Public HTTPS URL required}
mode=${3:-production}
[[ "$mode" == production || "$mode" == test ]]
[[ "$image" == restore || "$image" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*:(verified-[0-9a-f]{40}|[0-9a-f]{40}-[0-9]+-[0-9]+)$ ]]
[[ "$public_url" =~ ^https://[a-z0-9.-]+$ ]]
host=${public_url#https://}
app=a4b-jhd-aws

exec 9>/run/a4b-ec2-deploy.lock
flock -n 9
state=/var/lib/a4b-deploy
install -d -m 0700 "$state"
baseline_file="$state/before-test"
baseline=
if [[ -f "$baseline_file" ]]; then
    read -r baseline < "$baseline_file"
    [[ "$baseline" =~ ^[0-9a-f]{64}$ ]]
fi
env_file=$(mktemp /run/a4b-deploy-env.XXXXXX)
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d /run/a4b-deploy-docker.XXXXXX)
old_id=
new_id=
cutover=0
restore_name=

cleanup() {
    local result=$?
    trap - EXIT
    if (( result != 0 )); then
        if [[ -n "$new_id" ]]; then
            if [[ -n "$restore_name" ]]; then
                if (( cutover )); then
                    docker stop "$new_id" >/dev/null || result=1
                    docker rename "$new_id" "$restore_name" || result=1
                fi
            else
                docker rm -f "$new_id" >/dev/null || result=1
            fi
        fi
        if (( cutover )); then
            if [[ $(docker inspect -f '{{.Name}}' "$old_id") != "/$app" ]]; then
                docker rename "$old_id" "$app" || result=1
            fi
            if docker start "$old_id" >/dev/null; then
                echo 'Previous A4B container restarted.' >&2
            else
                echo 'Could not restart the previous A4B container.' >&2
            fi
        fi
    fi
    rm -f "$env_file" "$DOCKER_CONFIG/config.json"
    rmdir "$DOCKER_CONFIG" || true
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

old_id=$(docker inspect -f '{{.Id}}' "$app")
[[ $(docker inspect -f '{{.State.Running}} {{.HostConfig.NetworkMode}} {{.HostConfig.RestartPolicy.Name}}' "$old_id") == 'true host unless-stopped' ]]
if [[ "$image" == restore ]]; then
    if [[ -z "$baseline" ]]; then
        echo 'No saved pre-test version to restore.' >&2
        exit 1
    fi
    if [[ "$baseline" == "$old_id" ]]; then
        curl -fsS --max-time 6 "$public_url/api/health" >/dev/null
        rm -f "$baseline_file"
        echo 'The pre-test version is already running.'
        exit 0
    fi
    restore_name="$app-previous-${baseline:0:12}"
    [[ $(docker inspect -f '{{.Name}} {{.State.Running}}' "$baseline") == "/$restore_name false" ]]
    new_id=$baseline
else
    mapfile -t mounts < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$old_id" | sed '/^$/d')
    [[ ${#mounts[@]} -eq 2 ]]
    mount_args=()
    for mount in "${mounts[@]}"; do mount_args+=(--volume "$mount"); done

    # Docker's env-file format is one assignment per line. Abort before cutover if
    # the running container's configuration cannot be represented without changes.
    # $state/env-overrides (root-only, KEY=VALUE per line) adds or replaces settings, e.g.
    # A4B_STORAGE=postgres after the business-data migration (README "업무 데이터 저장소").
    overrides="$state/env-overrides"
    if [[ -e "$overrides" && $(stat -c '%u %a' "$overrides") != '0 600' ]]; then
        echo "$overrides must be owned by root with mode 600." >&2
        exit 1
    fi
    docker inspect -f '{{json .Config.Env}}' "$old_id" | python3 -c '
import json, os, re, sys
values = json.load(sys.stdin)
if not all("=" in value and "\n" not in value and "\r" not in value for value in values):
    raise SystemExit("Unsupported container environment format")
if "EASYFLOW_PUBLIC_URL=" + sys.argv[1] not in values or "PORT=3041" not in values:
    raise SystemExit("Running container does not match the expected A4B deployment")
settings = dict(value.split("=", 1) for value in values)
if os.path.exists(sys.argv[2]):
    for line in open(sys.argv[2], encoding="utf-8").read().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in ("EASYFLOW_PUBLIC_URL", "PORT"):
            raise SystemExit("Unsupported line in env-overrides: " + key)
        settings[key] = value
print("\n".join(key + "=" + value for key, value in settings.items()))
' "$public_url" "$overrides" > "$env_file"

    if ! docker image inspect "$image" >/dev/null 2>&1; then
        registry=${image%%/*}
        aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$registry"
        docker pull "$image"
    fi
    # The new image checks its own settings and connects to the business-data DB (schema
    # version, row-level security, privileges) before the running container is stopped.
    # A missing A4B_STORAGE=postgres or an unmigrated RDS stops the deploy here.
    if ! docker run --rm --network host --env-file "$env_file" "${mount_args[@]}" "$image" node Backend/api/build-server/index.mjs --preflight; then
        echo 'Preflight failed; the running A4B container was left unchanged.' >&2
        exit 1
    fi
    new_id=$(docker create --network host --restart unless-stopped --env-file "$env_file" "${mount_args[@]}" "$image")
    if [[ "$mode" == test && -z "$baseline" ]]; then
        printf '%s\n' "$old_id" > "$baseline_file.tmp"
        mv -f "$baseline_file.tmp" "$baseline_file"
        baseline=$old_id
    fi
fi
cutover=1
docker stop "$old_id" >/dev/null
docker rename "$old_id" "$app-previous-${old_id:0:12}"
docker rename "$new_id" "$app"
docker start "$new_id" >/dev/null

for ((attempt=0; attempt<30; attempt++)); do
    if curl -fsS --max-time 4 -H "Host: $host" http://127.0.0.1:3041/ >/dev/null &&
       curl -fsS --max-time 6 "$public_url/api/health" >/dev/null; then
        echo 'A4B homepage and public health check passed.'
        if [[ "$image" == restore || "$mode" == production ]]; then
            rm -f "$baseline_file"
            baseline=
        fi
        # Keep the immediate rollback container and any pre-test baseline.
        while IFS= read -r previous; do
            [[ -z "$previous" || "$previous" == "$app-previous-${old_id:0:12}" || "$previous" == "$app-previous-${baseline:0:12}" ]] && continue
            previous_image=$(docker inspect -f '{{.Config.Image}}' "$previous") || continue
            if ! docker rm "$previous" >/dev/null; then
                echo "Could not remove older rollback container: $previous" >&2
                continue
            fi
            # Docker refuses removal when a container still references the image.
            if [[ "$previous_image" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*:verified-[0-9a-f]{40}$ ]]; then
                docker image rm "$previous_image" >/dev/null 2>&1 || true
            fi
        done < <(docker ps -a --filter status=exited --format '{{.Names}}' | awk '/^a4b-jhd-aws-previous-[0-9a-f]+$/')
        exit 0
    fi
    sleep 2
done
echo 'A4B health checks failed.' >&2
exit 1
