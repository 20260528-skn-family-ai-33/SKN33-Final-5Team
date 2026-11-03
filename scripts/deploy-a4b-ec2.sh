#!/usr/bin/env bash
# Runs as root on the existing A4B EC2 host via AWS Systems Manager.
set -euo pipefail
umask 077

image=${1:?ECR image required}
public_url=${2:?Public HTTPS URL required}
[[ "$image" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*:[0-9a-f]{40}-[0-9]+-[0-9]+$ ]]
[[ "$public_url" =~ ^https://[a-z0-9.-]+$ ]]
host=${public_url#https://}
app=a4b-jhd-aws

exec 9>/run/a4b-ec2-deploy.lock
flock -n 9
env_file=$(mktemp /run/a4b-deploy-env.XXXXXX)
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d /run/a4b-deploy-docker.XXXXXX)
old_id=
new_id=
cutover=0

cleanup() {
    local result=$?
    trap - EXIT
    if (( result != 0 )); then
        if [[ -n "$new_id" ]]; then
            docker rm -f "$new_id" >/dev/null || result=1
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
mapfile -t mounts < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$old_id" | sed '/^$/d')
[[ ${#mounts[@]} -eq 2 ]]
mount_args=()
for mount in "${mounts[@]}"; do mount_args+=(--volume "$mount"); done

# Docker's env-file format is one assignment per line. Abort before cutover if
# the running container's configuration cannot be represented without changes.
docker inspect -f '{{json .Config.Env}}' "$old_id" | python3 -c '
import json, sys
values = json.load(sys.stdin)
if not all("=" in value and "\n" not in value and "\r" not in value for value in values):
    raise SystemExit("Unsupported container environment format")
if "EASYFLOW_PUBLIC_URL=" + sys.argv[1] not in values or "PORT=3041" not in values:
    raise SystemExit("Running container does not match the expected A4B deployment")
print("\n".join(values))
' "$public_url" > "$env_file"

registry=${image%%/*}
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin "$registry"
docker pull "$image"
new_id=$(docker create --network host --restart unless-stopped --env-file "$env_file" "${mount_args[@]}" "$image")
cutover=1
docker stop "$old_id" >/dev/null
docker rename "$old_id" "$app-previous-${old_id:0:12}"
docker rename "$new_id" "$app"
docker start "$new_id" >/dev/null

for ((attempt=0; attempt<30; attempt++)); do
    if curl -fsS --max-time 4 -H "Host: $host" http://127.0.0.1:3041/ >/dev/null &&
       curl -fsS --max-time 6 "$public_url/api/health" >/dev/null; then
        echo 'A4B homepage and public health check passed.'
        # Keep one stopped rollback container to avoid filling the small EC2 disk.
        while IFS= read -r previous; do
            [[ -z "$previous" || "$previous" == "$app-previous-${old_id:0:12}" ]] && continue
            docker rm "$previous" >/dev/null || echo "Could not remove older rollback container: $previous" >&2
        done < <(docker ps -a --filter status=exited --format '{{.Names}}' | awk '/^a4b-jhd-aws-previous-[0-9a-f]+$/')
        exit 0
    fi
    sleep 2
done
echo 'A4B health checks failed.' >&2
exit 1
