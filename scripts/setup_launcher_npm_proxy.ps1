param(
    [string]$Proxy = "http://127.0.0.1:7897",
    [switch]$Clear
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
$LauncherDir = Join-Path $RepoRoot "launcher"
$NpmrcPath = Join-Path $LauncherDir ".npmrc"

if (-not (Test-Path $LauncherDir)) {
    throw "Launcher directory not found: $LauncherDir"
}

if ($Clear) {
    if (Test-Path $NpmrcPath) {
        Remove-Item -LiteralPath $NpmrcPath -Force
        Write-Host "Removed local launcher npm proxy config: $NpmrcPath"
    } else {
        Write-Host "No local launcher npm proxy config exists."
    }
    Write-Host "User-level npm config was not changed by this script."
    exit 0
}

if (-not $Proxy.Trim()) {
    throw "Proxy must be non-empty, for example: http://127.0.0.1:7897"
}

$content = @(
    "proxy=$Proxy",
    "https-proxy=$Proxy",
    "electron_mirror=https://npmmirror.com/mirrors/electron/",
    "electron_builder_binaries_mirror=https://npmmirror.com/mirrors/electron-builder-binaries/",
    "ELECTRON_GET_USE_PROXY=true"
)

Set-Content -LiteralPath $NpmrcPath -Value $content -Encoding utf8

Write-Host "Wrote local ignored launcher npm proxy config: $NpmrcPath"
Write-Host "Proxy: $Proxy"
Write-Host "Electron mirror: https://npmmirror.com/mirrors/electron/"
Write-Host ""
Write-Host "Retry from launcher directory:"
Write-Host "  npm install"
Write-Host "  npm run package"
