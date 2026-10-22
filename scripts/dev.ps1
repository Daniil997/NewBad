# Start API on :8000 and the Vite UI on :5173.
# Run from anywhere:
#   .\scripts\dev.ps1

$ErrorActionPreference = "Stop"

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -lt 1) { continue }
        $name = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        if ($value.Length -ge 2) {
            $quote = $value.Substring(0, 1)
            if (($quote -eq '"' -or $quote -eq "'") -and $value.EndsWith($quote)) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try { [Console]::OutputEncoding = $OutputEncoding } catch { }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Import-DotEnv (Join-Path $Root ".env")

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Creating .venv"
    python -m venv (Join-Path $Root ".venv")
    & $Python -m pip install -r (Join-Path $Root "requirements-dev.txt")
}

$UiDir = Join-Path $Root "ui"
if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
    Write-Host "Installing UI dependencies"
    Push-Location $UiDir
    try { npm install } finally { Pop-Location }
}

$Api = Start-Process `
    -FilePath $Python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $Root `
    -PassThru

Write-Host "API:  http://127.0.0.1:8000/docs"
Write-Host "UI:   http://127.0.0.1:5173"
Write-Host "Stop: Ctrl+C"

try {
    Push-Location $UiDir
    npm run dev
} finally {
    Pop-Location
    if ($Api -and -not $Api.HasExited) {
        & taskkill.exe /PID $Api.Id /T /F | Out-Null
    }
}
