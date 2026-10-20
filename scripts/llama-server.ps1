# Запуск Gemma на дискретной NVIDIA
# Считает только видеокарта. Слои на процессор не переносятся.
# Если весов нет или свободной видеопамяти мало, скрипт останавливается и ничего не запускает.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$gguf = Join-Path $repo "models\gemma-4-26B_q4_0-it.gguf"
$minFreeMiB = 14800

if (-not (Test-Path $gguf)) {
    Write-Host "Файла весов нет: $gguf"
    Write-Host "Нужен один официальный файл Gemma 4 26B A4B QAT Q4_0, около 13.5 ГиБ."
    Write-Host "Проектор картинок не скачиваем: в этой версии новости только текст."
    Write-Host "Адрес:"
    Write-Host "https://huggingface.co/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/resolve/main/gemma-4-26B_q4_0-it.gguf"
    Write-Host "Положите его по пути выше и запустите скрипт снова."
    exit 1
}

$length = (Get-Item $gguf).Length
if ($length -lt 14000000000) {
    Write-Host "Файл весов короче ожидаемых 14.4 ГБ. Это не официальный Q4_0, сервер не запускаю."
    exit 1
}

$smi = & nvidia-smi --query-gpu=name,memory.free --format=csv,noheader,nounits 2>$null
if ($LASTEXITCODE -ne 0 -or -not $smi) {
    Write-Host "Дискретной NVIDIA нет. Локальную модель не запускаем, демо идёт из заглушки."
    exit 1
}

$parts = $smi.Split(",")
$gpuName = $parts[0].Trim()
$freeMiB = [int]$parts[1].Trim()
Write-Host "$gpuName, свободно $freeMiB МиБ"
if ($freeMiB -lt $minFreeMiB) {
    Write-Host "Нужно около $minFreeMiB МиБ свободной видеопамяти, чтобы весь расчёт шёл на видеокарте."
    Write-Host "Сейчас свободно $freeMiB МиБ. Закройте программы, которые занимают GPU."
    Write-Host "Оставшиеся слои на процессор не переносим, сервер не запускаю."
    exit 1
}

$server = $env:LLAMA_SERVER
if (-not $server) {
    $found = Get-Command llama-server -ErrorAction SilentlyContinue
    if ($found) {
        $server = $found.Source
    }
}
if (-not $server) {
    Write-Host "Не найден llama-server. Нужна сборка llama.cpp с CUDA, не CPU-сборка."
    Write-Host "https://github.com/ggml-org/llama.cpp/releases"
    Write-Host "Распакуйте llama-server.exe и укажите путь в переменной LLAMA_SERVER."
    exit 1
}

Write-Host "Поднимаю llama-server на 127.0.0.1:8080, контекст 4096, все слои на GPU."
& $server -m $gguf --host 127.0.0.1 --port 8080 -c 4096 -ngl 99
exit $LASTEXITCODE
