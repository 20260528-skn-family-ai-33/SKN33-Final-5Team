#!/usr/bin/env bash
# Run the real release script with isolated host paths and a simulated Docker CLI.
set -euo pipefail
export fixture=${2:?temporary test directory required}
mkdir -p "$fixture/run" "$fixture/state"
sed -e 's|/run/a4b-|"$fixture/run"/a4b-|g' \
    -e 's|^state=/var/lib/a4b-deploy$|state="$fixture/state"|' "$1" >"$fixture/deploy.sh"
if grep -Eq '/run/a4b-|/var/lib/a4b-deploy' "$fixture/deploy.sh"; then exit 1; fi
image="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/a4b:verified-$(printf 'a%.0s' {1..40})"
mock_old_id=$(printf '1%.0s' {1..64})
mock_new_id=$(printf '2%.0s' {1..64})
export mock_old_id mock_new_id
export public_url=https://review.example.test
docker() {
    echo "$*" >>"$fixture"/calls
    case "$1" in
        inspect)
            case "$3" in
                '{{.Id}}') echo "$mock_old_id" ;;
                '{{.HostConfig.NetworkMode}} {{.HostConfig.RestartPolicy.Name}}') echo 'host unless-stopped' ;;
                '{{.State.Status}}') echo "$status" ;;
                '{{.State.FinishedAt}}') echo '2026-11-04T08:00:00.000000000Z' ;;
                *HostConfig.Binds*) printf '%s\n' 'actual-data:/app/data' 'model:/app/ml:ro' ;;
                '{{json .Config.Env}}') printf '["EASYFLOW_PUBLIC_URL=%s","PORT=3041","A4B_STORAGE=%s"]\n' "$public_url" "$store" ;;
                '{{.Name}}') cat "$fixture"/oldname ;;
                *) return 91 ;;
            esac ;;
        image|stop|ps) return 0 ;;
        volume) echo "$mock_old_id" ;;
        run)
            if [[ "$*" == *'ml.svm56.bundle_cli check --probe'* ]]; then
                [[ "$failure" != svm ]]
            else
                [[ "$failure" != preflight ]]
            fi ;;
        create) echo "$mock_new_id" ;;
        start) [[ "$failure" != start || "$2" != "$mock_new_id" ]] ;;
        rm) return 0 ;;
        rename) if [[ "$2" == "$mock_old_id" ]]; then echo "/$3" >"$fixture"/oldname; fi ;;
        *) return 92 ;;
    esac
}
curl() { [[ "$failure" != health ]]; }
sleep() { :; }
# The test env file stands in for the root-owned EC2 configuration.
stat() { printf '0 600\n'; }
export -f docker curl sleep stat
absent() { if grep "$@"; then return 1; fi; }
check() {
    export status=$1 store=$2 failure=$3
    printf 'A4B_STORAGE=%s\n' "$4" >"$fixture/state/env-overrides"
    chmod 600 "$fixture/state/env-overrides"
    echo /a4b-jhd-aws >"$fixture"/oldname
    : >"$fixture"/calls
    rc=0
    bash "$fixture/deploy.sh" "$image" "$public_url" production "${7:-}" >"$fixture"/result 2>&1 || rc=$?
    if [[ "$5" == ok ]]; then [[ $rc == 0 ]] || { cat "$fixture"/result; return 1; }
    else [[ $rc != 0 ]] || return 1; fi
    cat "$fixture"/calls > "$fixture/$6"
    echo "passed: $6"
}
check running sqlite none postgres blocked running-sqlite
absent -E '^(run |stop |start )' "$fixture"/running-sqlite
check exited sqlite none postgres ok offline-cutover
grep -q -- '--sqlite-stopped-at=2026-' "$fixture"/offline-cutover
grep -q -- "^start $mock_new_id" "$fixture"/offline-cutover
absent -q -- "^stop .*${mock_old_id}$" "$fixture"/offline-cutover
check exited sqlite preflight postgres blocked bad-receipt
check exited sqlite none postgres ok fresh-cutover a4b-fresh-test
grep -q '^run .*--volume a4b-fresh-test:/app/data .*--sqlite-stopped-at=' "$fixture"/fresh-cutover
grep -q '^create .*--volume a4b-fresh-test:/app/data ' "$fixture"/fresh-cutover
absent -E '^(run |create ).*actual-data:/app/data' "$fixture"/fresh-cutover
absent -E '^(create |stop |start |rename )' "$fixture"/bad-receipt
check exited sqlite health postgres blocked failed-cutover
absent -q -- "^start $mock_old_id" "$fixture"/failed-cutover
grep -q -- "^rename $mock_old_id a4b-jhd-aws$" "$fixture"/failed-cutover
check exited postgres none postgres blocked unrelated-stopped
check running postgres none postgres ok normal-deploy
grep -q -- "^stop --time 60 $mock_old_id$" "$fixture"/normal-deploy
grep -q -- '^create .*--stop-timeout 60 ' "$fixture"/normal-deploy
absent -q -- '--sqlite-stopped-at' "$fixture"/normal-deploy
grep -q -- '^run .*--network none .*--workdir /app/Backend .*ml.svm56.bundle_cli check --probe' "$fixture"/normal-deploy
check running postgres svm postgres blocked bad-svm
absent -E '^(create |stop |start |rename )' "$fixture"/bad-svm
grep -q -- 'SVM preflight failed before container replacement' "$fixture"/result
check running postgres start postgres blocked normal-rollback
grep -q -- "^start $mock_old_id" "$fixture"/normal-rollback
