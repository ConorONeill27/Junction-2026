param(
    [switch]$Rebuild,
    [switch]$LaunchBrowser,
    [switch]$Tunnel
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDir = Join-Path $Root "backend"
$GlassesDir = Join-Path $Root "glasses-app"
$FrontendDir = Join-Path $Root "frontend"

function Test-PortListening([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -ne $conns -and @($conns).Count -gt 0)
}

Write-Host "=== WorldGit Meta Ray-Ban Display Launcher ===" -ForegroundColor Cyan

if ($Rebuild -or -not (Test-Path (Join-Path $GlassesDir "dist\index.html"))) {
    Write-Host "[1/4] Building glasses-app..." -ForegroundColor Yellow
    Push-Location $GlassesDir
    npm run build
    Pop-Location
}

if (-not (Test-PortListening 8000)) {
    Write-Host "[2/4] Starting FastAPI + VGGT + IMU backend (:8000)..." -ForegroundColor Yellow
    $py = Join-Path $BackendDir ".venv311\Scripts\uvicorn.exe"
    Start-Process -FilePath $py -ArgumentList "main:app", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $BackendDir -WindowStyle Minimized
    Start-Sleep -Seconds 2
} else {
    Write-Host "[2/4] Backend (:8000) already running." -ForegroundColor Green
}

if (-not (Test-PortListening 4173)) {
    Write-Host "[3/4] Starting Meta Display HUD preview (:4173)..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run preview -- --host 0.0.0.0 --port 4173" -WorkingDirectory $GlassesDir -WindowStyle Minimized
    Start-Sleep -Seconds 2
} else {
    Write-Host "[3/4] Glasses HUD (:4173) already running." -ForegroundColor Green
}

if (-not (Test-PortListening 5173)) {
    Write-Host "[4/4] Starting 3D Studio viewer (:5173)..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev -- --host 0.0.0.0 --port 5173" -WorkingDirectory $FrontendDir -WindowStyle Minimized
    Start-Sleep -Seconds 2
} else {
    Write-Host "[4/4] 3D Studio (:5173) already running." -ForegroundColor Green
}

$adbCmd = Get-Command adb -ErrorAction SilentlyContinue
if ($adbCmd) {
    $rawDevices = & $adbCmd.Source devices | Where-Object { $_ -match "\tdevice$" }
    if ($rawDevices) {
        Write-Host ""
        Write-Host "[ADB] Connected device(s) detected — configuring USB port forwarding:" -ForegroundColor Green
        & $adbCmd.Source reverse tcp:4173 tcp:4173
        & $adbCmd.Source reverse tcp:8000 tcp:8000
        & $adbCmd.Source reverse tcp:5173 tcp:5173
        if ($LaunchBrowser) {
            & $adbCmd.Source shell am start -a android.intent.action.VIEW -d "http://127.0.0.1:4173/" | Out-Null
        }
    }
}

$tunnelUrl = $null
if ($Tunnel) {
    $cf = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cf) {
        $logFile = Join-Path $Root "cloudflared-glasses.log"
        if (Test-Path $logFile) { Remove-Item $logFile -Force }
        Start-Process -FilePath $cf.Source -ArgumentList "tunnel", "--url", "http://127.0.0.1:4173", "--no-autoupdate", "--logfile", $logFile -WindowStyle Minimized
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Path $logFile) {
                $m = Select-String -Path $logFile -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches | Select-Object -First 1
                if ($m) {
                    $tunnelUrl = $m.Matches[0].Value
                    break
                }
            }
        }
        if ($tunnelUrl) {
            $pyExe = Join-Path $BackendDir ".venv311\Scripts\python.exe"
            $qrScript = Join-Path $Root "meta-wearables-webapp-ref\plugins\meta-wearables-webapp\skills\ai-glasses-webapp-publish\scripts\qr_generator.py"
            $qrOut = Join-Path $Root "qr-glasses.png"
            if (Test-Path $qrScript) {
                $enc = [uri]::EscapeDataString($tunnelUrl)
                & $pyExe $qrScript --png $qrOut "fb-viewapp://web_app_deep_link?appName=WorldGit&appUrl=$enc" | Out-Null
            }
        }
    } else {
        Write-Host "[Tunnel] cloudflared not found on PATH." -ForegroundColor Red
    }
}

$lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254\.)" -and $_.InterfaceAlias -notmatch "vEthernet|Loopback" } |
    Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host ""
Write-Host "Ready URLs:" -ForegroundColor Cyan
if ($tunnelUrl) {
    Write-Host "  Public HTTPS Glasses HUD (Meta AI Web App):            $tunnelUrl/" -ForegroundColor Green
    Write-Host "  Saved Meta AI QR Code:                                   $Root\qr-glasses.png" -ForegroundColor Green
}
Write-Host "  Glasses HUD (USB / adb reverse - 9-axis secure context): http://127.0.0.1:4173/"
if ($lanIp) {
    Write-Host "  Glasses HUD (LAN Wi-Fi):                                 http://${lanIp}:4173/"
}
Write-Host "  Desktop 3D Studio (Upload .MP4 + Live IMU):              http://127.0.0.1:5173/"
Write-Host "  Backend API / Docs:                                      http://127.0.0.1:8000/docs"
