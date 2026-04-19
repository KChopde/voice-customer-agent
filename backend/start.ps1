# Start the voice-agent FastAPI server with env vars loaded from .env
# Usage:   .\start.ps1
#          .\start.ps1 -NoReload      # disable autoreload (slightly less CPU)

param(
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# 1. Load .env if it exists (KEY=VALUE format, # comments OK)
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    Write-Host "Loading env vars from .env" -ForegroundColor DarkGray
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $idx = $line.IndexOf("=")
            $name = $line.Substring(0, $idx).Trim()
            $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
            if ($name) {
                [Environment]::SetEnvironmentVariable($name, $value, "Process")
            }
        }
    }
} else {
    Write-Host "(no .env file found - using defaults; copy .env.example to .env to customise)" -ForegroundColor DarkGray
}

# 2. Pick the right Python interpreter (project venv if present)
$venvPython = Join-Path $here ".voice\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = Join-Path $here ".venv\Scripts\python.exe"
}
if (-not (Test-Path $venvPython)) {
    Write-Host "No venv found at .voice or .venv. Falling back to system 'python'." -ForegroundColor Yellow
    $venvPython = "python"
}

# 3. Show a quick status banner
Write-Host ""
Write-Host "Voice Customer Support Agent" -ForegroundColor Cyan
Write-Host "  Python : $venvPython"
Write-Host "  Twilio : $(if ($env:TWILIO_AUTH_TOKEN) { 'auth token set (signature check ON)' } else { 'no auth token (signature check OFF - dev only)' })"
Write-Host "  Telegram: $(if ($env:TELEGRAM_BOT_TOKEN) { 'configured' } else { 'not configured' })"
Write-Host "  URL    : http://localhost:8000"
Write-Host ""

# 4. Run uvicorn
$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000")
if (-not $NoReload) { $uvicornArgs += "--reload" }

& $venvPython @uvicornArgs
