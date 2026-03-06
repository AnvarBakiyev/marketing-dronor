# Marketing Dronor - Windows Installer
# Run in PowerShell as Administrator:
#
#   irm https://raw.githubusercontent.com/AnvarBakiyev/marketing-dronor/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Run PowerShell as Administrator!" -ForegroundColor Red
    pause; exit 1
}

$tmpDir = "$env:TEMP\MarketingDronorSetup"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$batPath = "$tmpDir\install.bat"

Write-Host "Downloading installer..." -ForegroundColor Yellow
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/AnvarBakiyev/marketing-dronor/main/install.bat" -OutFile $batPath -UseBasicParsing

Write-Host "Starting installation..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$batPath`"" -Verb RunAs -Wait

Write-Host "Setup complete!" -ForegroundColor Green
