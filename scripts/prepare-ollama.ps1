# Prepare the host Ollama used by the Docker app (Windows).
# Sets the user environment Ollama needs on an 8 GB GPU, restarts Ollama and pulls both models.
# Usage (PowerShell, no admin needed): powershell -ExecutionPolicy Bypass -File scripts\prepare-ollama.ps1
$ErrorActionPreference = 'Stop'
$settings = [ordered]@{
    OLLAMA_CONTEXT_LENGTH = '16384'   # design prompts reach ~8.5K tokens; the 8 GB default (4096) truncates them
    OLLAMA_NUM_PARALLEL   = '1'
    OLLAMA_FLASH_ATTENTION = '1'
    OLLAMA_KV_CACHE_TYPE  = 'q8_0'    # keeps the whole main model on an 8 GB GPU
    OLLAMA_KEEP_ALIVE     = '-1'      # no reload after idle periods
}
$exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $exe)) {
    Write-Host 'Ollama가 없습니다. 먼저 설치하세요: winget install Ollama.Ollama'
    exit 1
}
foreach ($key in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($key, $settings[$key], 'User')
    Set-Item "env:$key" $settings[$key]
}
Get-Process | Where-Object { $_.ProcessName -like 'ollama*' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2
Start-Process (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe')
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-WebRequest http://127.0.0.1:11434/api/version -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
}
& $exe pull qwen3:8b-q4_K_M
& $exe pull qwen3.5:4b
& $exe list
Write-Host 'Ollama 준비 완료. 저장소 루트에서 docker compose up -d 를 실행하세요.'
