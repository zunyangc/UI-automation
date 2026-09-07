<#
.SYNOPSIS
    Uninstall a self-hosted DevBox runner and free its slot on the fork.

.DESCRIPTION
    Run this on the DevBox in an Administrator PowerShell to fully
    decommission a runner:

        .\ops\remove-runner.ps1 -Label <devbox-N> -Token <RemoveToken>

    What it does:
      1. Stop and unregister the Scheduled Task 'GHRunner-<Label>'.
      2. Kill any live 'run.cmd'/'Runner.Listener' process for that runner.
      3. Run 'C:\actions-runner\config.cmd remove --token <Token>' to
         deregister the runner on GitHub, which frees the slot so
         setup-remote-runner.ps1 can re-claim it on another (or the same)
         DevBox.

    The workflow YAML is static (devbox-1..devbox-4 slots) and is NOT
    edited by this script.

.PARAMETER Label
    The DevBox slot label (devbox-1, devbox-2, devbox-3, or devbox-4).

.PARAMETER Token
    Runner *removal* token. Get one from:
        gh api -X POST repos/<your-handle>/UI-automation/actions/runners/remove-token
    Or (browser): fork -> Settings -> Actions -> Runners -> click your
    runner -> Remove -> copy the token from the shown './config.cmd
    remove --token ...' line.

.PARAMETER Repo
    GitHub 'owner/name' of your fork. Auto-detected from RepoPath origin
    if omitted.

.PARAMETER RepoPath
    Local clone path (default: $HOME\UI-automation). Used only for repo
    auto-detection when -Repo is omitted.

.PARAMETER RunnerRoot
    Runner install dir (default: C:\actions-runner).

.PARAMETER LocalOnly
    If set, only clean local state (Scheduled Task + config.cmd --local).
    Skips the GitHub-side deregistration; use when the runner is already
    gone from Settings -> Runners or the token endpoint 404s.
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^devbox-[1-4]$')]
    [string]$Label,

    [string]$Token,

    [string]$Repo,

    [string]$RepoPath = "$HOME\UI-automation",

    [string]$RunnerRoot = 'C:\actions-runner',

    [switch]$LocalOnly
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# --- Admin check --------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "This script must be run in an Administrator PowerShell." }

# --- Resolve Repo -------------------------------------------------------
if (-not $Repo) {
    if (-not (Test-Path (Join-Path $RepoPath '.git'))) {
        throw "RepoPath '$RepoPath' is not a git checkout; pass -Repo <owner/name>."
    }
    Push-Location $RepoPath
    try {
        $originUrl = (git remote get-url origin 2>$null).Trim()
    } finally { Pop-Location }
    if ($originUrl -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)') {
        $Repo = "$($Matches.owner)/$($Matches.repo)"
    } else {
        throw "Could not parse GitHub owner/repo from '$originUrl'. Pass -Repo <owner/name>."
    }
}
Write-Ok "Target fork: $Repo"

# --- Stop and remove Scheduled Task ------------------------------------
$taskName = "GHRunner-$Label"
Write-Step "Stopping Scheduled Task '$taskName'..."
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch {}
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Ok "Scheduled Task removed."
} else {
    Write-Warn "Scheduled Task '$taskName' not found; skipping."
}

# --- Kill live listener processes --------------------------------------
Write-Step "Stopping any live listener processes..."
$killed = 0
Get-Process -Name 'Runner.Listener','run' -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; $killed++ } catch {}
}
if ($killed -gt 0) { Write-Ok "$killed listener process(es) terminated." } else { Write-Warn "No live listener processes found." }

# --- Deregister on GitHub ----------------------------------------------
Write-Step "Removing runner registration..."
if (-not (Test-Path (Join-Path $RunnerRoot 'config.cmd'))) {
    Write-Warn "No runner install found at $RunnerRoot; skipping config.cmd remove."
} else {
    Push-Location $RunnerRoot
    try {
        if ($LocalOnly) {
            .\config.cmd remove --token dummy --local | Out-Host
        } elseif ($Token) {
            .\config.cmd remove --token $Token | Out-Host
        } else {
            Write-Warn "No -Token supplied; falling back to --local removal."
            Write-Warn "The runner may still appear (offline) on GitHub -- delete it manually from Settings -> Actions -> Runners."
            .\config.cmd remove --token dummy --local | Out-Host
        }
        if ($LASTEXITCODE -ne 0) { throw "config.cmd remove failed with exit code $LASTEXITCODE." }
        Write-Ok "Runner unregistered."
    } finally { Pop-Location }
}

# --- Done: workflow YAML intentionally not touched ---------------------
# NOTE: the workflow YAML is static (devbox-1..devbox-4) and is not
# edited by this script. Freeing the slot on GitHub (via config.cmd
# remove above) is sufficient -- the next setup-remote-runner.ps1 run
# will see the slot as available.

Write-Host ""
Write-Host "DONE." -ForegroundColor Green
Write-Host "Verify: https://github.com/$Repo/settings/actions/runners (runner '$Label' should be gone)."
Write-Host "The '$Label' slot is now free for another DevBox to claim via ops\setup-remote-runner.ps1."
