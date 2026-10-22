# Ручной запуск API и интерфейса. Вызывается из start-manual.bat.
$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try { [Console]::OutputEncoding = $OutputEncoding } catch { }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host ""
Write-Host "Ручная проверка NewBad"
Write-Host "Интерфейс: http://127.0.0.1:5173"
Write-Host "API:       http://127.0.0.1:8000/docs"
Write-Host "Режим берётся из .env. Остановка: Ctrl+C в этом окне."
Write-Host ""
Write-Host "В сервисе: компания, карточка, источник и страница, затем вопрос."
Write-Host "Для U_26030 в .env нужно PROFILE_SOURCE=case."
Write-Host "В demo компании называются U-BORROWER, U-SAVER и U-PEER."
Write-Host "Вставка новости: блок «Вставить свою новость», кнопка «Разобрать»."
Write-Host ""

$busy = $false
$client = New-Object System.Net.Sockets.TcpClient
try {
    $client.Connect("127.0.0.1", 8000)
    $busy = $true
} catch {
    $busy = $false
} finally {
    $client.Dispose()
}

if ($busy) {
    Write-Host "Порт 8000 уже занят. Этот запуск не стартует, иначе интерфейс попадёт в старый процесс."
    Write-Host "Если это контейнер: docker stop newbad-demo"
    Write-Host ""
    Read-Host "Нажмите Enter"
    exit 1
}

Start-Process "http://127.0.0.1:5173/"
& (Join-Path $PSScriptRoot "dev.ps1")
Write-Host ""
Read-Host "Нажмите Enter"
