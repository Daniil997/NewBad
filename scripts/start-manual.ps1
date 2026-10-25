# Ручной запуск, остановка и перезапуск. Вызывается из start-manual.bat.
param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try { [Console]::OutputEncoding = $OutputEncoding } catch { }

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Get-ListeningPids {
    param([int]$Port)
    $found = New-Object System.Collections.Generic.List[int]
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+(LISTENING|ПРОСЛУШИВАНИЕ)\s+(\d+)\s*$") {
            $procId = [int]$Matches[2]
            if ($procId -gt 0 -and -not $found.Contains($procId)) {
                [void]$found.Add($procId)
            }
        }
    }
    return $found
}

function Test-PortOpen {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Get-OurServerRootPid {
    param($Info, [int]$Port)
    if (-not $Info) { return 0 }
    if ($Port -eq 5173 -and [string]$Info.Name -match 'node' -and [string]$Info.CommandLine -match 'vite') {
        return [int]$Info.ProcessId
    }
    if ($Port -ne 8000 -or [string]$Info.Name -notmatch 'python') { return 0 }
    # With --reload the listener is a multiprocessing child. Stop the uvicorn
    # supervisor (and venv launcher), so no process retains the listening socket.
    $serverRoot = 0
    $current = $Info
    for ($depth = 0; $depth -lt 5 -and $current; $depth++) {
        if ([string]$current.Name -notmatch 'python') { break }
        if ([string]$current.CommandLine -match 'uvicorn' -and [string]$current.CommandLine -match 'app\.main:app') {
            $serverRoot = [int]$current.ProcessId
        }
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($current.ParentProcessId)" -ErrorAction SilentlyContinue
    }
    return $serverRoot
}

function Test-DockerProxy {
    param($Info)
    if (-not $Info) { return $false }
    return [string]$Info.Name -match 'docker|wslrelay'
}

function Stop-NewBad {
    $stopped = $false
    $foreign = @()
    $docker = $false
    foreach ($port in @(8000, 5173)) {
        foreach ($procId in (Get-ListeningPids $port)) {
            $info = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
            $serverRoot = Get-OurServerRootPid $info $port
            if ($serverRoot -gt 0) {
                & taskkill.exe /PID $serverRoot /T /F | Out-Null
                Write-Host "Остановлен порт $port."
                $stopped = $true
            } elseif (Test-DockerProxy $info) {
                $docker = $true
            } else {
                $label = if ($info -and $info.Name) { [string]$info.Name } else { "pid $procId" }
                $foreign += "$label, порт $port"
            }
        }
    }
    if ($docker) {
        $container = ""
        try {
            $container = (& docker ps --filter "name=newbad-demo" --format "{{.Names}}" 2>$null | Out-String).Trim()
        } catch {
            $container = ""
        }
        if ($container -match 'newbad-demo') {
            & docker stop newbad-demo | Out-Null
            Write-Host "Контейнер newbad-demo остановлен."
            $stopped = $true
        } else {
            $foreign += "Docker, порт 8000"
        }
    }
    foreach ($item in $foreign) {
        Write-Host "Этот процесс скрипт не останавливает: $item"
    }
    if (-not $stopped -and $foreign.Count -eq 0) {
        Write-Host "Серверы NewBad не запущены."
    }
    return ($foreign.Count -eq 0)
}

function Wait-PortsFree {
    $deadline = (Get-Date).AddSeconds(8)
    do {
        $busy = (Get-ListeningPids 8000).Count -gt 0 -or (Get-ListeningPids 5173).Count -gt 0
        if (-not $busy) { return $true }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Show-Banner {
    Write-Host ""
    Write-Host "Ручная проверка NewBad"
    Write-Host "Интерфейс: http://127.0.0.1:5173"
    Write-Host "API:       http://127.0.0.1:8000/docs"
    Write-Host "Режим берётся из .env."
    Write-Host "Остановка: пункт 2 в этом файле или Ctrl+C в окне запуска."
    Write-Host ""
    Write-Host "В сервисе: войти компанией, открыть карточку, проверить источник и задать вопрос."
    Write-Host "Для компаний кейса в .env нужно PROFILE_SOURCE=case."
    Write-Host "Своя новость: блок «Разобрать свою новость»."
    Write-Host "Режим модели: «Настройки разработки» на главной или на входе."
    Write-Host ""
}

function Start-NewBad {
    if ((Test-PortOpen 8000) -or (Test-PortOpen 5173)) {
        Write-Host "Порт 8000 или 5173 уже занят. Запуск пропущен, чтобы не попасть в старый процесс."
        Write-Host "Выберите пункт 3, если это сервер NewBad и его нужно перезапустить."
        Write-Host ""
        Read-Host "Нажмите Enter"
        exit 1
    }
    & (Join-Path $PSScriptRoot "dev.ps1")
    Write-Host ""
    Read-Host "Нажмите Enter"
}

function Read-Action {
    while ($true) {
        Write-Host "1 — запустить"
        Write-Host "2 — остановить"
        Write-Host "3 — перезапустить"
        Write-Host "0 — выход"
        $choice = (Read-Host "Выбор").Trim().ToLowerInvariant()
        switch ($choice) {
            "1" { return "start" }
            "2" { return "stop" }
            "3" { return "restart" }
            "0" { return "exit" }
            "start" { return "start" }
            "stop" { return "stop" }
            "restart" { return "restart" }
            default { Write-Host "Нужна цифра 1, 2, 3 или 0." }
        }
    }
}

Show-Banner
if ([string]::IsNullOrWhiteSpace($Action)) {
    $Action = Read-Action
} else {
    $Action = $Action.Trim().ToLowerInvariant()
}

switch ($Action) {
    "start" { Start-NewBad }
    "stop" {
        $clear = Stop-NewBad
        Write-Host ""
        Read-Host "Нажмите Enter"
        if (-not $clear) { exit 1 }
    }
    "restart" {
        $clear = Stop-NewBad
        if (-not $clear) {
            Write-Host "Перезапуск остановлен: порт держит другой процесс."
            Write-Host ""
            Read-Host "Нажмите Enter"
            exit 1
        }
        if (-not (Wait-PortsFree)) {
            Write-Host "Порты ещё заняты. Повторите остановку."
            Write-Host ""
            Read-Host "Нажмите Enter"
            exit 1
        }
        Write-Host "Запускаем снова."
        Start-NewBad
    }
    "exit" { exit 0 }
    default {
        Write-Host "Неизвестная команда. Используйте start, stop или restart."
        Write-Host ""
        Read-Host "Нажмите Enter"
        exit 1
    }
}
