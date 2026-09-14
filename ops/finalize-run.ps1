# ops/finalize-run.ps1
<#
.SYNOPSIS
    Post-run cleanup for UI-automation tests on a self-hosted DevBox runner.

.DESCRIPTION
    UI tests spawn real windows (VS, cmd, powershell, notepad, browsers) and
    leave behind project folders in the tester's home directory. If we don't
    clean these up between runs, subsequent tests find stale state and fail
    in confusing ways (VS auto-suffixes ConsoleApp -> ConsoleApp2, cmd windows
    stack up, etc).

    This script:
      1. Terminates any leftover UI processes commonly launched by test cases
         (devenv, vs_installer, cmd, powershell hosts other than ours,
          notepad, msedge, chrome, etc).
      2. Deletes leftover project/artifact folders in $HOME that are created
         by test_cases/*.csv (MyGlobal, test, ConsoleApp*, WindowsApp1*, etc).
      3. Removes any temp files the CSVs write to $HOME
         (dn_info.txt, aspnet_majors.json, etc).

    Without -Since (the default, legacy CI mode), all of the above is an
    unscoped blanket sweep of the whole machine -- appropriate on a
    dedicated, test-only DevBox runner.

    With -Since (see below), every kill/delete is scoped to resources
    created/started at or after -Since, PLUS anything already recorded in a
    small persisted retry manifest (see below). -Since is expected to be
    the calling run's own start time, or -- when the caller (run_test.py)
    has detected that a PREVIOUS run crashed before reaching its own
    cleanup -- that earlier run's own start time instead, so its leftovers
    are still caught. This script itself does NOT infer "since the last
    cleanup attempt" on its own: doing that once was tried and reverted,
    because it also swept up anything a user created independently during
    idle time between specs (e.g. their own notepad), since that too is
    "newer than the last attempt". Only a caller with real evidence of a
    specific unfinished run (run_test.py's active-run marker) may
    legitimately extend -Since further back than "now" -- and only back to
    that run's own start, never further.

    Separately, a per-machine, per-user state file
    ($env:LOCALAPPDATA\ui-automation\cleanup-state.json) persists a retry
    manifest (`pendingProcesses` / `pendingPaths`): specific processes/paths
    this script has already tried -- and failed -- to remove (e.g.
    transient access-denied). Those are retried UNCONDITIONALLY on every
    subsequent scoped invocation, regardless of -Since, because a resource
    already known to be test-owned should keep being retried until it's
    actually gone; a resource's own timestamp will always predate whatever
    -Since a later invocation is given, so time-scoping alone could never
    self-heal a stuck removal. Only resources this script itself already
    targeted are ever recorded here -- nothing is added to the manifest
    just because it happens to be old.

    It NEVER touches the repo working tree, screenshots/, or the running
    GitHub Actions runner process. Safe to invoke at the start AND end of
    every run.

.PARAMETER Since
    Optional (a [datetime]). Passing ANY value switches the script into
    scoped mode (see above) instead of the legacy unscoped blanket sweep.
    Required for safe use from a shared/interactive session (e.g.
    run_test.py's automatic per-spec cleanup), where an unscoped blanket
    kill/delete could otherwise take out something the current user was
    working on. Pass this run's own start time, unless the caller has
    concrete evidence of a specific earlier unfinished run, in which case
    pass that run's own start time instead (never an arbitrary "last
    cleanup" timestamp -- see .DESCRIPTION).

    The one exception is 'conhost'/'cmd': that kill is skipped ENTIRELY in
    scoped mode, because conhost's process ancestry doesn't reliably map to
    Win32 ParentProcessId, so a freshly spawned conhost hosting THIS
    SCRIPT's own invocation can itself be newer than any time-based cutoff
    -- time-scoping alone was observed to still kill the caller's own
    console during testing. conhost/cmd cleanup for an automatic per-spec
    caller instead relies entirely on that spec's own "Clean up" steps /
    on_failure_capture, which only close windows THAT spec captured into a
    `*hwnd` var.

    Omit -Since on a dedicated, test-only CI DevBox runner to keep the
    original aggressive, unscoped, state-free blanket behavior for
    everything.

.NOTES
    This is intentionally aggressive about stale processes but conservative
    about files — it only touches known artifact names, not arbitrary paths.
#>

[CmdletBinding()]
param(
    [Nullable[datetime]]$Since = $null
)

$ErrorActionPreference = 'Continue'   # never fail the workflow on cleanup

Write-Host "==> UI-automation post-run cleanup" -ForegroundColor Cyan

$Scoped = [bool]$Since
$Cutoff = $Since
$StatePath = Join-Path $env:LOCALAPPDATA 'ui-automation\cleanup-state.json'


# --- Persisted retry manifest (scoped mode only) --------------------------
# `pendingProcesses`/`pendingPaths` are an explicit ownership/retry list:
# specific resources THIS script has already targeted (because they were in
# -Since scope on a previous invocation) and failed to remove, kept keyed
# precisely enough (pid+name+start time; exact path) to avoid ever
# conflating them with an unrelated resource, e.g. a reused PID. Nothing is
# ever added here just because it's old -- only things this script itself
# already tried to clean up.
function Get-CleanupState {
    $default = [pscustomobject]@{ pendingProcesses = @(); pendingPaths = @() }
    if (-not (Test-Path $StatePath)) { return $default }
    try {
        $raw = Get-Content -Path $StatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Host "    ! cleanup-state.json unreadable/corrupt, bootstrapping fresh: $_"
        return $default
    }
    $pendingProcesses = @()
    if ($raw.pendingProcesses) { $pendingProcesses = @($raw.pendingProcesses) }
    $pendingPaths = @()
    if ($raw.pendingPaths) { $pendingPaths = @($raw.pendingPaths) }
    return [pscustomobject]@{ pendingProcesses = $pendingProcesses; pendingPaths = $pendingPaths }
}

function Save-CleanupState {
    param($PendingProcesses, $PendingPaths)
    $dir = Split-Path -Path $StatePath -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $state = [pscustomobject]@{
        pendingProcesses = @($PendingProcesses)
        pendingPaths     = @($PendingPaths)
    }
    try {
        $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StatePath -Encoding utf8 -ErrorAction Stop
    } catch {
        Write-Host "    ! failed to persist cleanup-state.json: $_"
    }
}

$CleanupState = if ($Scoped) { Get-CleanupState } else { $null }
$PendingProcessKeys = if ($Scoped) {
    @($CleanupState.pendingProcesses | ForEach-Object { "$($_.pid)|$($_.name)|$($_.startTime)" })
} else { @() }
$PendingPathSet = if ($Scoped) { @($CleanupState.pendingPaths) } else { @() }

# Survivors of THIS pass (still present after an attempted kill/delete, or
# still out of reach) become next invocation's retry manifest.
$SurvivingProcesses = @()
$SurvivingPaths = @()

# A resource is in scope when: legacy unscoped mode (-Since omitted), OR its
# own timestamp is at/after -Since (created/started during -- or, when the
# caller detected a crashed previous run, since that earlier run's own
# start -- see .DESCRIPTION), OR it is an already-known pending offender
# from a PREVIOUS scoped invocation (retried unconditionally regardless of
# age until it actually goes away).
function Test-InCleanupScope {
    param($Timestamp, [bool]$IsPending)
    if (-not $Scoped) { return $true }
    if ($IsPending) { return $true }
    if (-not $Timestamp) { return $false }
    return $Timestamp -ge $Cutoff
}

# --- 1. Terminate leftover UI processes -----------------------------------
# List of processes commonly spawned by test cases. NOT included: pwsh
# (the runner shell itself), sshd, GitHub runner processes.
# 'conhost'/'cmd' are handled separately below because, unlike these, their
# process ancestry can't be safely told apart from the caller's own
# session, even with the manifest above.
$processNames = @(
    'devenv',           # Visual Studio
    'ServiceHub.*',     # VS ServiceHub workers (loose pattern below)
    'MSBuild',          # MSBuild workers left after a build
    'vshost',           # VS test host
    'notepad'
)

function Invoke-ScopedProcessKill {
    param($Proc)
    $startTime = $null
    try { $startTime = $Proc.StartTime } catch { }
    $key = "$($Proc.Id)|$($Proc.ProcessName)|$($startTime.ToString('o'))"
    $isPending = $PendingProcessKeys -contains $key
    if (-not (Test-InCleanupScope -Timestamp $startTime -IsPending $isPending)) { return }
    Write-Host "    kill $($Proc.ProcessName) (PID $($Proc.Id))"
    try { Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue } catch { }
    if ($Scoped) {
        Start-Sleep -Milliseconds 150
        if (Get-Process -Id $Proc.Id -ErrorAction SilentlyContinue) {
            Write-Host "    ! still running, will retry next cleanup: $($Proc.ProcessName) (PID $($Proc.Id))"
            $script:SurvivingProcesses += [pscustomobject]@{
                pid = $Proc.Id; name = $Proc.ProcessName; startTime = $startTime.ToString('o')
            }
        }
    }
}

foreach ($name in $processNames) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object { Invoke-ScopedProcessKill $_ }
}

# ServiceHub / VBCSCompiler match by wildcard.
Get-Process | Where-Object { $_.ProcessName -like 'ServiceHub*' -or $_.ProcessName -eq 'VBCSCompiler' } |
    ForEach-Object { Invoke-ScopedProcessKill $_ }

# conhost / cmd, spawned via launch.py. These are intentionally NEVER
# manifest/time-scoped: conhost's process ancestry (via ConPTY/legacy
# console attach) doesn't reliably map to Win32 ParentProcessId, so no
# heuristic here can safely tell a test-spawned console apart from the
# caller's own interactive shell -- as observed killing this very script's
# console host during testing. In scoped mode (-Since passed, i.e. an
# automatic per-run caller such as run_test.py), skip this step entirely
# and rely on the per-spec capture-based cleanup instead (each CSV's own
# "Clean up" phase, and on_failure_capture on failure, close only the
# conhost/cmd windows THAT spec itself launched, via their captured *hwnd
# vars). In legacy unscoped CI mode, keep killing every conhost/cmd on the
# box.
if (-not $Scoped) {
    Get-Process -Name 'conhost', 'cmd' -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Write-Host "    kill $($_.ProcessName) (PID $($_.Id))"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

# --- 2. Delete leftover artifact folders in $HOME -------------------------
# These are the folder names created by scripts in test_cases/*.csv. Scoped
# by LastWriteTime (not CreationTime): a fixed-name folder such as `test`
# that a spec reuses across runs has a CreationTime from whenever it was
# first created, which never advances even though a later run adds new
# children to it -- LastWriteTime does update on every such change, so it
# correctly reflects whether THIS window of runs touched the folder.
# Uses -Recurse -Force; if a handle is still held, we log and move on, and
# it is recorded as pending so the next invocation retries it.
function Invoke-ScopedFolderDelete {
    param($Path)
    if (-not (Test-Path $Path)) { return }
    $written = $null
    try { $written = (Get-Item -Path $Path -ErrorAction Stop).LastWriteTime } catch { }
    $isPending = $PendingPathSet -contains $Path
    if (-not (Test-InCleanupScope -Timestamp $written -IsPending $isPending)) { return }
    Write-Host "    rmdir $Path"
    Remove-Item -Path $Path -Recurse -Force -ErrorAction SilentlyContinue
    if ($Scoped -and (Test-Path $Path)) {
        Write-Host "    ! still present, will retry next cleanup: $Path"
        $script:SurvivingPaths += $Path
    }
}

$homeFolders = @('MyGlobal', 'test')
foreach ($folder in $homeFolders) {
    Invoke-ScopedFolderDelete (Join-Path $HOME $folder)
}

# ConsoleApp / WindowsApp1 with numeric suffixes (VS auto-suffixes on collision).
Get-ChildItem -Path $HOME -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1)\d*$' } |
    ForEach-Object { Invoke-ScopedFolderDelete $_.FullName }

# Same under %USERPROFILE%\source\repos\ (VS default new-project location).
$reposDir = Join-Path $HOME 'source\repos'
if (Test-Path $reposDir) {
    Get-ChildItem -Path $reposDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1|MyGlobal|test)\d*$' } |
        ForEach-Object { Invoke-ScopedFolderDelete $_.FullName }
}

# --- 3. Delete leftover temp files in $HOME -------------------------------
# Scoped by last-write time (these files get overwritten every run, so
# write time -- not creation time -- reflects whether THIS window of runs
# touched it).
function Invoke-ScopedFileDelete {
    param($Path)
    if (-not (Test-Path $Path)) { return }
    $written = $null
    try { $written = (Get-Item -Path $Path -ErrorAction Stop).LastWriteTime } catch { }
    $isPending = $PendingPathSet -contains $Path
    if (-not (Test-InCleanupScope -Timestamp $written -IsPending $isPending)) { return }
    Write-Host "    rm $Path"
    Remove-Item -Path $Path -Force -ErrorAction SilentlyContinue
    if ($Scoped -and (Test-Path $Path)) {
        Write-Host "    ! still present, will retry next cleanup: $Path"
        $script:SurvivingPaths += $Path
    }
}

$homeFiles = @('dn_info.txt', 'aspnet_majors.json')
foreach ($file in $homeFiles) {
    Invoke-ScopedFileDelete (Join-Path $HOME $file)
}

# --- Persist updated manifest (scoped mode only) --------------------------
if ($Scoped) {
    Save-CleanupState -PendingProcesses $SurvivingProcesses -PendingPaths $SurvivingPaths
}

Write-Host "==> Cleanup complete" -ForegroundColor Green
exit 0
