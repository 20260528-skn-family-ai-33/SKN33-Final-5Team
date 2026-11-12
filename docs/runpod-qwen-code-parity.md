# Qwen serving source and deployment policy

GitHub `scripts/runpod` is the source of truth for the deployed Qwen scripts.
AWS runs the backend request-building code; it does not run these model-serving
scripts. Compare each deployment with its corresponding source files.

## Authentication

The gateway requires its configured Bearer token by default. Only the explicit
environment value `A4B_API_AUTH_REQUIRED=0` disables that check. The existing
RunPod deployment uses this value; retain it in the deployment launcher when
updating the source to preserve its current access policy. Do not hard-code the
deployment's open-access choice in the shared gateway or startup script.
The gateway token environment variable remains required in both modes.

## Warmup

Warmup preloads only the final664 and previous 9B models with `think=false`,
`shift=false`, `truncate=false`, 32768 context and indefinite residency.
It does not send a user prompt or change subsequent requests' thinking options.
Explicit runtime checks reject incomplete loads, errors, unexpected models,
digest mismatches, CPU offload, and incompatible context lengths.

## Deployment verification

Follow the running supervisor's command line: the historical
`/workspace/a4b/app/scripts/runpod/start-final.sh` may be a launcher that delegates
to a separate release directory. Compare the actual serving directory, not just
the historical directory. Keep the deployment entry point and parser source
selection separate from the shared code.

The parity set is `start-final.sh`, `runpod-ollama-proxy.py`,
`serving_request_policy.py`, `final_model_profile.py`, `warm-final.py`,
`check-final-ready.py`, `check-gateway-ready.py`, and
`verify-resident-models.py`. The last file is a verification utility; installing
it does not invoke it or generate model requests.

Before activation, retain a rollback copy and compare the existing source and
process state to the inspected deployment. After activation, verify the pinned
runtime version, both model digests, GPU residency, 32768 contexts, readiness,
and the configured authentication behavior. A stopped Pod must first become
available before deployment and verification can be completed.
