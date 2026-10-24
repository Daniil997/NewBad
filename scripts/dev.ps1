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
if (-not (Test-Path -LiteralPath $Python)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python не найден. Установите Python 3.11+ и добавьте его в PATH."
    }
    Write-Host "Creating .venv"
    python -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Python)) {
        throw "Не удалось создать виртуальное окружение Python."
    }
}

& $Python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw "Проекту необходим Python 3.11+." }

# An existing virtual environment may be missing dependencies after git pull.
& $Python -c "import fastapi, uvicorn, pydantic, httpx, yaml, duckdb, numpy, fitz, rank_bm25"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing missing Python dependencies"
    & $Python -m pip install -r (Join-Path $Root "requirements-dev.txt")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить Python-зависимости." }
}

# Prevent starting the frontend against an API with a syntax error.
& $Python -m compileall -q (Join-Path $Root "app") (Join-Path $Root "config")
if ($LASTEXITCODE -ne 0) { throw "В Python-коде обнаружена синтаксическая ошибка. API не запущен." }

$UiDir = Join-Path $Root "ui"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm не найден. Установите Node.js LTS."
}
if (-not (Test-Path (Join-Path $UiDir "node_modules"))) {
    Write-Host "Installing UI dependencies"
    Push-Location $UiDir
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "Не удалось установить UI-зависимости." }
    } finally { Pop-Location }
}

# Run the API in its own visible PowerShell window. Keeping that window open
# makes Python import/startup errors visible instead of only showing Vite's
# ECONNREFUSED proxy error in the UI console.
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$RootForCommand = $Root.Replace("'", "''")
$PythonForCommand = $Python.Replace("'", "''")
$ApiCommand = @'
Set-Location -LiteralPath '{0}'
$Host.UI.RawUI.WindowTitle = 'NewBad API logs'
& '{1}' -u -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "NewBad API завершился с ошибкой (код $LASTEXITCODE)." -ForegroundColor Red
}
'@
$ApiCommand = $ApiCommand.Replace("{0}", $RootForCommand).Replace("{1}", $PythonForCommand)
$EncodedApiCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($ApiCommand))
$Api = Start-Process `
    -FilePath $PowerShell `
    -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $EncodedApiCommand) `
    -WorkingDirectory $Root `
    -WindowStyle Normal `
    -PassThru

# Launch Vite only after the API has answered its health endpoint.
$ApiReady = $false
Write-Host "Checking API health..."
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
        if ($health.status -eq "ok") {
            $ApiReady = $true
            break
        }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ApiReady) {
    Write-Host "API не отвечает. Проверьте отдельное окно 'NewBad API logs'." -ForegroundColor Red
    throw "API не прошёл проверку /api/health; интерфейс не запущен."
}

Write-Host "API:  http://127.0.0.1:8000/docs"
Write-Host "UI:   http://127.0.0.1:5173"
Write-Host "Stop: Ctrl+C"

# Open the browser only once Vite is actually serving its page.
$BrowserJob = Start-Job -ArgumentList "http://127.0.0.1:5173/" -ScriptBlock {
    param([string]$Url)
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Start-Process $Url
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
}

try {
    Push-Location $UiDir
    npm run dev
} finally {
    Pop-Location
    if ($BrowserJob) {
        Stop-Job -Job $BrowserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $BrowserJob -Force -ErrorAction SilentlyContinue
    }
    if ($Api -and -not $Api.HasExited) {
        & taskkill.exe /PID $Api.Id /T /F | Out-Null
    }
}
