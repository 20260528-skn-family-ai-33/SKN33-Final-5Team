#!/usr/bin/env bash
set -euo pipefail
command=${1:?SSM command required}
[[ "$INSTANCE_ID" =~ ^i-[0-9a-f]+$ ]]
params=$(jq -cn --arg command "$command" '{commands:[$command],executionTimeout:["900"]}')
id=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
    --parameters "$params" --comment "A4B ${GITHUB_RUN_ID:-deployment}" --query Command.CommandId --output text)
echo "SSM command $id" >&2
for ((attempt=0; attempt<110; attempt++)); do
    if status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE_ID" --query Status --output text 2>&1); then
        case "$status" in
            Success)
                aws ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE_ID" --query StandardOutputContent --output text
                exit 0 ;;
            Pending|InProgress|Delayed|Cancelling) ;;
            *)
                aws ssm get-command-invocation --command-id "$id" --instance-id "$INSTANCE_ID" --query StandardErrorContent --output text >&2
                echo "SSM command failed: $status" >&2
                exit 1 ;;
        esac
    elif [[ "$status" != *InvocationDoesNotExist* ]]; then
        echo "$status" >&2
        exit 1
    fi
    sleep 10
done
echo "SSM command did not finish: $id" >&2
exit 1
