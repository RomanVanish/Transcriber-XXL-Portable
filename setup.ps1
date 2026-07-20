# ── Настройка среды Транскрибера XXL с нуля ──────────────────────────
# Скачивает и раскладывает всё тяжёлое, чего нет в git-репозитории:
#   runtime\  — портативный Python 3.12 + PyQt6
#   xxl\      — Faster-Whisper XXL r245.4 (распознавание + диаризация)
#   ffmpeg\   — ffmpeg + ffprobe (сборка BtbN, ffmpeg 7.1)
# Плюс собирает Transcriber.exe из launcher.cs.
#
# Запуск: setup.bat (или powershell -ExecutionPolicy Bypass -File setup.ps1)
# Повторный запуск безопасен: готовые компоненты пропускаются.
# Скачивается ~1.6 ГБ; нужен интернет и ~5 ГБ свободного места.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dl = Join-Path $root "_setup_downloads"

$PY_URL   = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
$PIP_URL  = "https://bootstrap.pypa.io/get-pip.py"
$PYQT_VER = "6.11.0"
$SEVENZR  = "https://www.7-zip.org/a/7zr.exe"
$XXL_URL  = "https://github.com/Purfview/whisper-standalone-win/releases/download/Faster-Whisper-XXL/Faster-Whisper-XXL_r245.4_windows.7z"
$FF_URL   = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-gpl-7.1.zip"

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }

function Fetch($url, $out) {
    if (Test-Path $out) { Write-Host "  уже скачано: $(Split-Path -Leaf $out)"; return }
    Write-Host "  скачивание: $url"
    & "$env:windir\System32\curl.exe" -L --fail --retry 3 -o "$out.part" $url
    if ($LASTEXITCODE -ne 0) { throw "не удалось скачать $url" }
    Move-Item "$out.part" $out
}

New-Item -ItemType Directory -Force $dl | Out-Null

# ── 1. Python runtime ────────────────────────────────────────────────
Step "Python runtime"
$runtime = Join-Path $root "runtime"
if (Test-Path "$runtime\python.exe") {
    Write-Host "  уже есть — пропускаю"
} else {
    $pyzip = Join-Path $dl "python-embed.zip"
    Fetch $PY_URL $pyzip
    New-Item -ItemType Directory -Force $runtime | Out-Null
    Expand-Archive $pyzip -DestinationPath $runtime -Force
    # включить site-packages (в embeddable отключено по умолчанию)
    $pth = Join-Path $runtime "python312._pth"
    (Get-Content $pth) -replace "^#\s*import site", "import site" | Set-Content $pth -Encoding ascii
    Fetch $PIP_URL (Join-Path $dl "get-pip.py")
    & "$runtime\python.exe" (Join-Path $dl "get-pip.py") --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "установка pip не удалась" }
}
cmd /c "`"$runtime\python.exe`" -c `"import PyQt6`" >nul 2>nul"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  установка PyQt6 $PYQT_VER..."
    & "$runtime\python.exe" -m pip install --no-warn-script-location "PyQt6==$PYQT_VER"
    if ($LASTEXITCODE -ne 0) { throw "установка PyQt6 не удалась" }
} else { Write-Host "  PyQt6 уже установлен" }

# ── 2. Faster-Whisper XXL ────────────────────────────────────────────
Step "Faster-Whisper XXL"
$xxl = Join-Path $root "xxl"
if (Test-Path "$xxl\faster-whisper-xxl.exe") {
    Write-Host "  уже есть — пропускаю"
} else {
    $zr = Join-Path $dl "7zr.exe"
    Fetch $SEVENZR $zr
    $arc = Join-Path $dl "Faster-Whisper-XXL_r245.4_windows.7z"
    Fetch $XXL_URL $arc   # ~1.4 ГБ, это надолго
    Write-Host "  распаковка (несколько минут)..."
    $ext = Join-Path $dl "xxl_extract"
    & $zr x $arc "-o$ext" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "распаковка XXL не удалась" }
    $exe = Get-ChildItem $ext -Recurse -Filter "faster-whisper-xxl.exe" | Select-Object -First 1
    if (-not $exe) { throw "в архиве не найден faster-whisper-xxl.exe" }
    Move-Item $exe.DirectoryName $xxl
    Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 3. ffmpeg ────────────────────────────────────────────────────────
Step "ffmpeg"
$ff = Join-Path $root "ffmpeg"
if ((Test-Path "$ff\ffmpeg.exe") -and (Test-Path "$ff\ffprobe.exe")) {
    Write-Host "  уже есть — пропускаю"
} else {
    $ffzip = Join-Path $dl "ffmpeg.zip"
    Fetch $FF_URL $ffzip
    $ext = Join-Path $dl "ff_extract"
    Expand-Archive $ffzip -DestinationPath $ext -Force
    New-Item -ItemType Directory -Force $ff | Out-Null
    foreach ($n in "ffmpeg.exe", "ffprobe.exe") {
        $f = Get-ChildItem $ext -Recurse -Filter $n | Select-Object -First 1
        if (-not $f) { throw "в архиве ffmpeg не найден $n" }
        Copy-Item $f.FullName (Join-Path $ff $n) -Force
    }
    Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
}

# ── 4. Transcriber.exe ───────────────────────────────────────────────
Step "Transcriber.exe"
if (Test-Path "$root\Transcriber.exe") {
    Write-Host "  уже есть — пропускаю"
} elseif ((Test-Path "$root\launcher.cs") -and (Test-Path "$root\T.ico")) {
    $csc = "$env:windir\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
    & $csc /nologo /target:winexe /optimize+ /win32icon:"$root\T.ico" `
        /r:System.Windows.Forms.dll /out:"$root\Transcriber.exe" "$root\launcher.cs"
    if ($LASTEXITCODE -ne 0) { throw "компиляция лаунчера не удалась" }
    Write-Host "  собран из launcher.cs"
} else {
    Write-Host "  нет launcher.cs/T.ico — пропускаю (запуск через run_gui.bat)"
}

# ── Проверка ─────────────────────────────────────────────────────────
Step "Проверка"
& "$runtime\python.exe" -c "import PyQt6.QtWidgets; print('  Python + PyQt6: OK')"
& "$xxl\faster-whisper-xxl.exe" --version | ForEach-Object { Write-Host "  XXL: $_" }
& "$ff\ffprobe.exe" -version | Select-Object -First 1 | ForEach-Object { Write-Host "  $_" }

Remove-Item $dl -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`nГотово." -ForegroundColor Green
Write-Host "Модели Whisper докачаются сами при первом использовании."
