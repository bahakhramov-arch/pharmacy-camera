# Открыть сайт отчётов из интернета через Cloudflare Tunnel, например https://dostlik.5plyus.uz
#
# Запуск на компьютере с программой учёта (PowerShell, в папке программы):
#   powershell -ExecutionPolicy Bypass -File setup_tunnel.ps1 -Hostname dostlik.5plyus.uz
#
# Что делает: ставит cloudflared, один раз просит войти в Cloudflare (откроется браузер -
# выбрать домен 5plyus.uz), создаёт туннель и запись DNS, пишет config.yml.
# Сам туннель потом запускает start.bat вместе с программой.

param(
    [string]$Hostname = "dostlik.5plyus.uz",
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$Name = "apteka-" + $Hostname.Split(".")[0]
Set-Location $PSScriptRoot

# 0. Без пароля сайт в интернет не выпускаем
$sitePass = (Get-Content secrets.env -Encoding UTF8 -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*REPORTS_PASSWORD\s*=' }) -replace '^\s*REPORTS_PASSWORD\s*=\s*', ''
if (-not $sitePass -or $sitePass.Trim().Length -lt 6) {
    Write-Host "Сначала задайте пароль сайта: в secrets.env строка REPORTS_PASSWORD=не_короче_6_символов" -ForegroundColor Red
    Write-Host "(логин на сайте будет: report). Потом перезапустите программу и этот скрипт."
    exit 1
}

# 1. cloudflared
$cf = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cf) {
    Write-Host "Ставлю cloudflared..."
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
    $cf = @("${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe", "$env:ProgramFiles\cloudflared\cloudflared.exe") |
          Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $cf) { Write-Host "cloudflared не установился. Закройте и откройте PowerShell, запустите снова."; exit 1 }
}
Write-Host "cloudflared: $cf"

# 2. Вход в Cloudflare (один раз; в браузере выбрать 5plyus.uz -> Authorize)
$dir = Join-Path $env:USERPROFILE ".cloudflared"
if (-not (Test-Path (Join-Path $dir "cert.pem"))) {
    Write-Host "Откроется браузер: войдите в Cloudflare, выберите 5plyus.uz и нажмите Authorize."
    & $cf tunnel login
}

# 3. Туннель и запись DNS
$tunnels = & $cf tunnel list --output json | ConvertFrom-Json
$t = $tunnels | Where-Object { $_.name -eq $Name } | Select-Object -First 1
if (-not $t) {
    & $cf tunnel create $Name
    $t = (& $cf tunnel list --output json | ConvertFrom-Json) | Where-Object { $_.name -eq $Name } | Select-Object -First 1
}
$id = $t.id
Write-Host "Туннель $Name ($id)"
& $cf tunnel route dns --overwrite-dns $Name $Hostname

# 4. Настройки туннеля: всё с адреса -> сайт отчётов на этом компьютере
$cfg = @"
tunnel: $id
credentials-file: $dir\$id.json
ingress:
  - hostname: $Hostname
    service: http://localhost:$Port
  - service: http_status:404
"@
Set-Content -Path (Join-Path $dir "config.yml") -Value $cfg -Encoding ASCII
Write-Host ""
Write-Host "Готово. Перезапустите start.bat - туннель запустится вместе с программой." -ForegroundColor Green
Write-Host "Адрес: https://$Hostname  (логин: report, пароль из secrets.env)"
