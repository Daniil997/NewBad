[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Подготовка Python-зависимостей =="
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { $Python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $Python) { throw "Python 3.11+ не найден. Установите Python и добавьте его в PATH." }
if (-not (Test-Path ".venv")) {
    & $Python.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Не удалось создать .venv" }
}
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) { throw "Не найден Python виртуального окружения: $VenvPython" }
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Не удалось обновить pip" }
& $VenvPython -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить Python-зависимости" }

Write-Host "`n== Установка frontend-зависимостей =="
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm не найден. Установите Node.js LTS вместе с npm." }
Push-Location (Join-Path $Root "ui")
npm ci
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm ci завершился с ошибкой" }
Pop-Location

Write-Host "`n== Python-тесты =="
& $VenvPython -m pytest -v
if ($LASTEXITCODE -ne 0) { throw "Python-тесты завершились с ошибкой" }

Write-Host "`n== Проверка TypeScript и сборка UI =="
Push-Location (Join-Path $Root "ui")
npm run build
$BuildExit = $LASTEXITCODE
Pop-Location
if ($BuildExit -ne 0) { throw "Сборка UI завершилась с ошибкой" }

Write-Host "`nВсе проверки завершились успешно." -ForegroundColor Green
