#!/usr/bin/env bash
# Runs as root on the existing A4B EC2 host via AWS Systems Manager.
set -euo pipefail
umask 077

image=${1:?ECR image or restore required}
public_url=${2:?Public HTTPS URL required}
mode=${3:-production}
fresh_volume=${4:-}
[[ "$mode" == production || "$mode" == test ]]
[[ "$image" == restore || "$image" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*:((verified|backend|frontend)-[0-9a-f]{40}|[0-9a-f]{40}-[0-9]+-[0-9]+)$ ]]
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
old_status=
restore_name=
frontend_pending=

cleanup() {
    local result=$?
    trap - EXIT
    if (( result != 0 )); then
        if [[ -n "$frontend_pending" ]]; then
            frontend "$frontend_pending" recover || result=1
        fi
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
            if [[ "$old_status" == running ]]; then
                docker start "$old_id" >/dev/null || result=1
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

pull_image() {
    local ref=$1 registry
    if ! docker image inspect "$ref" >/dev/null 2>&1; then
        registry=${ref%%/*}
        aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$registry"
        docker pull "$ref"
    fi
}

verify_health() {
    local commit=${1:-} home
    for ((attempt=0; attempt<30; attempt++)); do
        if home=$(curl -fsS --max-time 4 -H "Host: $host" http://127.0.0.1:3041/) &&
           { [[ -z "$commit" ]] || [[ "$home" == *"a4b-release=$commit"* ]]; } &&
           { [[ -z "$commit" ]] || { home=$(curl -fsS --max-time 6 "$public_url/?a4b-release=$commit") &&
              [[ "$home" == *"a4b-release=$commit"* ]]; }; } &&
           curl -fsS --max-time 6 "$public_url/api/health" >/dev/null; then
            echo 'A4B homepage and public health check passed.'
            return 0
        fi
        sleep 2
    done
    echo 'A4B health checks failed.' >&2
    return 1
}

old_id=$(docker inspect -f '{{.Id}}' "$app")
[[ $(docker inspect -f '{{.HostConfig.NetworkMode}} {{.HostConfig.RestartPolicy.Name}}' "$old_id") == 'host unless-stopped' ]]
old_status=$(docker inspect -f '{{.State.Status}}' "$old_id")
if [[ -n "$fresh_volume" ]]; then
    [[ "$mode" == production && "$old_status" == exited && "$image" != restore && "$image" != *:frontend-* ]]
    [[ "$fresh_volume" =~ ^a4b-fresh-[a-z0-9-]+$ ]]
    [[ $(docker volume inspect -f '{{index .Labels "a4b.cutover.source"}}' "$fresh_volume") == "$old_id" ]]
    [[ -z $(docker ps -q --filter "volume=$fresh_volume") ]]
fi
if [[ "$old_status" != running ]]; then
    [[ "$old_status" == exited && "$mode" == production && "$image" != restore && "$image" != *:frontend-* ]] ||
        { echo 'Only a stopped production backend can enter storage cutover.' >&2; exit 1; }
fi
if [[ "$image" == *:frontend-* ]]; then
    deploy_frontend
    exit 0
fi
if [[ "$image" == restore ]]; then
    if [[ -z "$baseline" || "$baseline" == "$old_id" ]] &&
       [[ $(frontend "$old_id" probe-baseline) == yes ]]; then
        frontend_pending=$old_id
        frontend "$old_id" restore
        verify_health
        frontend "$old_id" finish-restore
        frontend_pending=
        rm -f "$baseline_file"
        echo 'Restored the pre-test frontend without restarting the backend.'
        exit 0
    fi
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
    replaced=0
    for mount in "${mounts[@]}"; do
        if [[ -n "$fresh_volume" && "$mount" =~ ^[^:]+:/app/data(:rw)?$ ]]; then
            mount="$fresh_volume:/app/data"
            ((replaced+=1))
        fi
        mount_args+=(--volume "$mount")
    done
    [[ -z "$fresh_volume" || "$replaced" == 1 ]]

    # Docker's env-file format is one assignment per line. Abort before cutover if
    # the running container's configuration cannot be represented without changes.
    # $state/env-overrides (root-only, KEY=VALUE per line) adds or replaces settings, e.g.
    # A4B_STORAGE=postgres after the business-data migration (docs/business-data-storage.md).
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
previous_store = settings.get("A4B_STORAGE") or "sqlite"
if os.path.exists(sys.argv[2]):
    for line in open(sys.argv[2], encoding="utf-8").read().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in ("EASYFLOW_PUBLIC_URL", "PORT"):
            raise SystemExit("Unsupported line in env-overrides: " + key)
        settings[key] = value
switching = previous_store == "sqlite" and settings.get("A4B_STORAGE") == "postgres"
if switching and sys.argv[3] != "exited":
    raise SystemExit("Stop the SQLite container, back up its volume and complete the import before cutover.")
if not switching and sys.argv[3] == "exited":
    raise SystemExit("A stopped container can only be replaced during SQLite to PostgreSQL cutover.")
print("\n".join(key + "=" + value for key, value in settings.items()))
' "$public_url" "$overrides" "$old_status" > "$env_file"

    pull_image "$image"
    # During the first cutover the source stays offline. Reject an import that
    # started before the last SQLite writer stopped, including after a rollback.
    preflight=(--preflight)
    if [[ "$old_status" == exited ]]; then
        stopped_at=$(docker inspect -f '{{.State.FinishedAt}}' "$old_id")
        preflight+=("--sqlite-stopped-at=$stopped_at")
    fi
    if ! docker run --rm --network host --env-file "$env_file" "${mount_args[@]}" "$image" node Backend/api/build-server/index.mjs "${preflight[@]}"; then
        echo 'Preflight failed before container replacement.' >&2
        exit 1
    fi
    new_id=$(docker create --network host --restart unless-stopped --env-file "$env_file" "${mount_args[@]}" "$image")
    if [[ "$image" == *:backend-* ]]; then
        docker cp -L "$old_id:/app/Frontend/dist-app/." - | docker cp - "$new_id:/app/Frontend/dist-app/"
    fi
    if [[ "$mode" == test && -z "$baseline" ]]; then
        printf '%s\n' "$old_id" > "$baseline_file.tmp"
        mv -f "$baseline_file.tmp" "$baseline_file"
        baseline=$old_id
    fi
fi
cutover=1
if [[ "$old_status" == running ]]; then docker stop "$old_id" >/dev/null; fi
docker rename "$old_id" "$app-previous-${old_id:0:12}"
docker rename "$new_id" "$app"
docker start "$new_id" >/dev/null

if [[ "$image" == restore && $(frontend "$new_id" probe-baseline) == yes ]]; then
    frontend_pending=$new_id
    frontend "$new_id" restore
fi
verify_health
if [[ -n "$frontend_pending" ]]; then
    frontend "$frontend_pending" finish-restore
    frontend_pending=
fi
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
    if [[ "$previous_image" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*:(verified|backend)-[0-9a-f]{40}$ ]]; then
        docker image rm "$previous_image" >/dev/null 2>&1 || true
    fi
done < <(docker ps -a --filter status=exited --format '{{.Names}}' | awk '/^a4b-jhd-aws-previous-[0-9a-f]+$/')
