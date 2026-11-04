$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'setup/prepare-ollama.ps1') @args
exit $LASTEXITCODE
