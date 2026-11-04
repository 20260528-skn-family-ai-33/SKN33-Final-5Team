#!/usr/bin/env bash
# Sourced by deploy-a4b-ec2.sh on the EC2 host.
frontend() {
    local container=$1
    shift
    docker exec -i "$container" python3 - "$@" <<< "$A4B_FRONTEND_HELPER"
}

deploy_frontend() {
    local commit=${image##*:frontend-}
    local release="/app/Frontend/a4b-frontend-$commit"
    local artifact staging
    frontend "$old_id" recover
    if ! docker exec "$old_id" test -f "$release/index.html"; then
        pull_image "$image"
        artifact=$(docker create "$image" /unused)
        staging=$(docker exec "$old_id" mktemp -d /app/Frontend/.a4b-staging.XXXXXX)
        if ! docker cp "$artifact:/dist/." - | docker cp - "$old_id:$staging/"; then
            docker exec "$old_id" rm -rf "$staging"
            docker rm "$artifact" >/dev/null
            return 1
        fi
        docker rm "$artifact" >/dev/null
        docker exec "$old_id" mv -T "$staging" "$release"
        docker image rm "$image" >/dev/null 2>&1 || true
    fi
    frontend_pending=$old_id
    frontend "$old_id" activate "$commit"
    verify_health "$commit"
    frontend "$old_id" "finish-$mode"
    frontend_pending=
    echo "Frontend $commit deployed without restarting the backend."
}
