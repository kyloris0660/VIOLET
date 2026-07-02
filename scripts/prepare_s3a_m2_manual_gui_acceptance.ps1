param(
    [string]$ExpectedHead = "",
    [switch]$AllowDirtyTracked
)

$ErrorActionPreference = "Stop"
$TargetBranch = "codex/s3a-m2-production-delta-e2e-gpu-telemetry"
$ExpectedUrl = "http://127.0.0.1:8012/admin?tab=content#dynamic-library-sync-section"
$ArtifactDir = Join-Path (Split-Path -Parent $PSScriptRoot) ".local_manifests\s3a_m2_delta_e2e\manual_acceptance_preflight"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot "venv\Scripts\python.exe"
$Result = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString("o")
    repo_root = $RepoRoot
    branch = $null
    head = $null
    git_status = $null
    python = $Python
    profile_status = $null
    diagnostic_summary = $null
    active_server_audit = $null
    source_root_check = $null
    opened_url = $null
    status = "running"
    blockers = @()
}
$StartedServer = $false

function Add-Blocker([string]$Message) {
    $script:Result.blockers += $Message
}

function Run-Command([string]$Exe, [string[]]$Args, [switch]$Json) {
    $output = & $Exe @Args
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        throw "Command failed ($exit): $Exe $($Args -join ' ')`n$output"
    }
    if ($Json) {
        return ($output | Out-String | ConvertFrom-Json)
    }
    return ($output | Out-String).Trim()
}

function Run-Git([string[]]$Args) {
    return Run-Command "git" $Args
}

function Has-TrackedChanges([string]$Porcelain) {
    foreach ($line in ($Porcelain -split "`r?`n")) {
        if (-not $line.Trim()) { continue }
        if ($line.StartsWith("??")) { continue }
        return $true
    }
    return $false
}

function Assert-Ready([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        Add-Blocker $Message
        throw $Message
    }
}

function Write-PreflightArtifact {
    New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $path = Join-Path $ArtifactDir "manual-gui-acceptance-preflight-$stamp.json"
    $Result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

try {
    Set-Location $RepoRoot
    Assert-Ready (Test-Path (Join-Path $RepoRoot "run.py")) "Repo root check failed: run.py not found."
    Assert-Ready (Test-Path (Join-Path $RepoRoot "backend\app")) "Repo root check failed: backend\app not found."
    Assert-Ready (Test-Path $Python) "Repo venv Python not found: $Python"

    Write-Host "Repo root: $RepoRoot"
    Run-Git @("fetch", "origin") | Out-Null
    $statusBefore = Run-Git @("status", "--porcelain=v1")
    if ((Has-TrackedChanges $statusBefore) -and -not $AllowDirtyTracked) {
        $Result.git_status = $statusBefore
        throw "Tracked working-tree changes are present. Commit/stash them or rerun with -AllowDirtyTracked only if you understand the risk."
    }

    $currentBranch = Run-Git @("branch", "--show-current")
    if ($currentBranch -ne $TargetBranch) {
        Run-Git @("checkout", $TargetBranch) | Out-Null
    }
    if (-not (Has-TrackedChanges (Run-Git @("status", "--porcelain=v1")))) {
        Run-Git @("pull", "--ff-only") | Out-Null
    }

    $Result.git_status = Run-Git @("status", "-sb")
    $Result.branch = Run-Git @("branch", "--show-current")
    $Result.head = Run-Git @("rev-parse", "HEAD")
    $lastCommits = Run-Git @("log", "--oneline", "-3")
    Write-Host $Result.git_status
    Write-Host "Branch: $($Result.branch)"
    Write-Host "HEAD: $($Result.head)"
    Write-Host "Last commits:"
    Write-Host $lastCommits
    Assert-Ready ($Result.branch -eq $TargetBranch) "Wrong branch: expected $TargetBranch, got $($Result.branch)."
    if ($ExpectedHead) {
        Assert-Ready ($Result.head -eq $ExpectedHead) "Wrong HEAD: expected $ExpectedHead, got $($Result.head)."
    }

    Run-Command $Python @("scripts\check_python_env.py", "--expected-python", ".\venv\Scripts\python.exe") | Write-Host

    Write-Host "Stopping managed production server, if any..."
    Run-Command $Python @("scripts\violet_production_control.py", "stop", "--json") -Json | Out-Null
    $audit = Run-Command $Python @("scripts\audit_active_violet_servers.py", "--ports", "8000,8012-8024", "--include-process-tree", "--json") -Json
    $Result.active_server_audit = $audit
    Assert-Ready ([int]$audit.unknown_listener_count -eq 0) "Ambiguous listener remains after stop. Inspect active-server audit before proceeding."
    Assert-Ready ([int]$audit.suspected_violet_count -eq 0) "Suspected V.I.O.L.E.T. listener remains after stop."

    $profile = Run-Command $Python @("scripts\violet_production_control.py", "profile-status", "--json") -Json
    $Result.profile_status = $profile
    Assert-Ready ([bool]$profile.ok) "Production profile-status failed."
    $profileData = $profile.data.profile
    Assert-Ready ($profileData.env -eq "production") "Profile env is not production."
    Assert-Ready ($profileData.db.name -eq "blombooru") "Profile DB is not blombooru."
    Assert-Ready ($profileData.db.name -ne "blombooru_test") "Profile points at test DB."
    Assert-Ready ([bool]$profileData.storage_root_configured) "Production storage root is not configured."
    Assert-Ready ([bool]$profileData.manual_sync_enabled) "Manual sync is disabled."
    Assert-Ready ([bool]$profileData.manual_sync_execute_enabled) "Manual execute is disabled."
    Assert-Ready (($profileData.automation_flags_enabled | Measure-Object).Count -eq 0) "Automatic/scheduled/startup/service sync flags are enabled."
    Assert-Ready ([bool]$profileData.manual_e2e_components.ai_tagging_enabled) "Manual E2E AI tagging is not enabled."
    Assert-Ready ([bool]$profileData.manual_e2e_components.content_classification_enabled) "Manual E2E classification is not enabled."
    Assert-Ready ($profileData.manual_e2e_components.content_classification_method -eq "clip") "Manual E2E classification method is not clip."
    Assert-Ready ([bool]$profileData.manual_e2e_components.tag_translation_llm_enabled) "Manual E2E LLM localization readiness is not enabled."

    $rootCheckScript = @'
import json
import os
from scripts import violet_production_control as vpc
profile, _path, errors = vpc.load_production_profile()
if errors or profile is None:
    raise SystemExit(json.dumps({"ok": False, "errors": errors or ["profile_missing"]}))
os.environ.update(vpc._profile_to_env(profile))
os.environ["VIOLET_ENV"] = "production"
from backend.app import database
from backend.app.models import DynamicSourceRoot
database.init_engine()
db = database.SessionLocal()
try:
    root = db.get(DynamicSourceRoot, 2)
    print(json.dumps({
        "ok": bool(root and root.is_active and root.label == "icloud-photos-production"),
        "root_id": 2,
        "label": root.label if root else None,
        "is_active": bool(root.is_active) if root else False,
    }))
finally:
    db.close()
'@
    $rootCheck = ($rootCheckScript | & $Python - | Out-String | ConvertFrom-Json)
    $Result.source_root_check = $rootCheck
    Assert-Ready ([bool]$rootCheck.ok) "Source root 2 is not active icloud-photos-production."

    Write-Host "Starting production server..."
    Run-Command $Python @("scripts\violet_production_control.py", "start", "--json") -Json | Out-Null
    $StartedServer = $true
    $diag = Run-Command $Python @("scripts\violet_production_control.py", "diagnostic-summary", "--json") -Json
    $Result.diagnostic_summary = $diag
    Assert-Ready ([bool]$diag.running) "Diagnostic summary says server is not running."
    Assert-Ready ($diag.env -eq "production") "Server env is not production."
    Assert-Ready ([int]$diag.port -eq 8012) "Server port is not 8012."
    Assert-Ready ($diag.profile.db.name -eq "blombooru") "Server diagnostic DB is not blombooru."
    Assert-Ready (($diag.profile.automation_flags_enabled | Measure-Object).Count -eq 0) "Server diagnostic shows automation flags enabled."
    Assert-Ready ($diag.manual_e2e_components.content_classification_method -eq "clip") "Server diagnostic classification method is not clip."
    Assert-Ready ([bool]$diag.manual_e2e_components.ai_tagging_enabled) "Server diagnostic AI tagging is not enabled."
    Assert-Ready ([bool]$diag.manual_e2e_components.tag_translation_llm_enabled) "Server diagnostic LLM localization readiness is not enabled."

    Run-Command $Python @("scripts\violet_production_control.py", "open-manual-sync", "--json") -Json | Out-Null
    $Result.opened_url = $ExpectedUrl
    $Result.status = "ready"
    $artifact = Write-PreflightArtifact

    Write-Host ""
    Write-Host "READY FOR MANUAL GUI ACCEPTANCE"
    Write-Host "Branch: $($Result.branch)"
    Write-Host "HEAD: $($Result.head)"
    Write-Host "DB: blombooru"
    Write-Host "Source root: 2 / icloud-photos-production"
    Write-Host "Port: 8012"
    Write-Host "URL: $ExpectedUrl"
    Write-Host "Preflight artifact: $artifact"
    Write-Host ""
    Write-Host "Next manual steps:"
    Write-Host "1. Check the page shows icloud-photos-production."
    Write-Host "2. Click Start manual sync."
    Write-Host "3. Review plan summary and downstream follow-up count."
    Write-Host "4. Confirm run #16 leftovers appear as downstream follow-up if applicable."
    Write-Host "5. Confirm manually only when the page asks; do not double-click."
    Write-Host "6. Click Execute exactly once."
    Write-Host "7. Wait for a terminal status."
    Write-Host "8. Run the validator command below."
    Write-Host ""
    Write-Host "Validator:"
    Write-Host ".\venv\Scripts\python.exe scripts\validate_s3a_m2_gui_execute_acceptance.py --min-run-id 8 --write-public-summary --update-main-report"
    Write-Host ""
    Write-Host "Contracts:"
    Write-Host ".\venv\Scripts\python.exe scripts\check_phase_contract.py --contract s3a_m2_production_delta_e2e_contract_v1 --summary docs\reports\s3a-m2-production-delta-e2e-summary.json"
    Write-Host ".\venv\Scripts\python.exe scripts\check_phase_contract.py --contract public_redaction_contract_v1 --summary docs\reports\s3a-m2-production-delta-e2e-summary.json"
}
catch {
    $Result.status = "failed"
    Add-Blocker ($_.Exception.Message)
    if ($StartedServer) {
        try {
            Run-Command $Python @("scripts\violet_production_control.py", "stop", "--json") -Json | Out-Null
        }
        catch {
            Add-Blocker ("Failed to stop server after failed preflight: " + $_.Exception.Message)
        }
    }
    $artifact = Write-PreflightArtifact
    Write-Host ""
    Write-Host "NOT SAFE TO RUN MANUAL ACCEPTANCE"
    Write-Host "Blockers:"
    foreach ($blocker in $Result.blockers) {
        Write-Host "- $blocker"
    }
    Write-Host "Preflight artifact: $artifact"
    exit 1
}
