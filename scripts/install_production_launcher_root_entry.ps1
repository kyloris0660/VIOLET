param(
    [string]$RepoRoot,
    [string]$LauncherName = "V.I.O.L.E.T. Production Launcher.exe"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Split-Path -Parent $scriptDir
}

$resolvedRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$distExe = Join-Path $resolvedRoot "launcher\dist\$LauncherName"
$rootExe = Join-Path $resolvedRoot $LauncherName

if (-not (Test-Path -LiteralPath $distExe -PathType Leaf)) {
    throw "Packaged launcher executable not found: $distExe"
}

Copy-Item -LiteralPath $distExe -Destination $rootExe -Force

[PSCustomObject]@{
    repo_root = $resolvedRoot
    source = $distExe
    installed = $rootExe
} | ConvertTo-Json -Compress
