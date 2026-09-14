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

    With -Since (see below), this becomes a *stale-cleanup policy* backed by
    a small persisted manifest, instead of a naive "created after my own
    -Since" filter: a per-machine, per-user state file
    ($env:LOCALAPPDATA\ui-automation\cleanup-state.json) remembers the end
    time of the last cleanup attempt (`lastAttemptAt`) and any specific
    process/path this script has already tried -- and failed -- to remove
    (`pendingProcesses` / `pendingPaths`). Each invocation:
      - Uses the PERSISTED `lastAttemptAt` (not this run's own -Since) as the
        scope cutoff, so a resource left behind by an EARLIER run -- e.g. one
        whose own cleanup never got to run at all, because run_test.py's
        process was killed externally before reaching its `finally` block --
        is still caught: its timestamp predates *this* run's start but not
        necessarily the last time cleanup actually executed.
      - ALSO unconditionally retries anything already recorded in
        `pendingProcesses`/`pendingPaths` regardless of its timestamp, since
        those are known-owned resources this script previously targeted and
        failed to remove (e.g. transient access-denied) -- a resource whose
        creation time will always predate any cutoff that already failed to
        catch it once, so time-scoping alone can never self-heal that case.
      - Advances `lastAttemptAt` to now and persists whatever is still left
        (kill/delete failures) as the new pending list, so cleanup keeps
        retrying known offenders on every subsequent invocation until they
        actually go away, while a genuinely pre-existing, unrelated
        IDE/process/project that merely shares one of these generic names is
        never recorded and never touched.
      - On the very first invocation ever on a machine (no state file yet),
        bootstraps `lastAttemptAt` to this run's own -Since value, so it
        does not retroactively sweep long-standing, unrelated state the
        first time it runs.

    It NEVER touches the repo working tree, screenshots/, or the running
    GitHub Actions runner process. Safe to invoke at the start AND end of
    every run.

.PARAMETER Since
    Optional (a [datetime]). Passing ANY value switches the script into
    scoped, manifest-backed mode (see above) instead of the legacy unscoped
    blanket sweep; its actual value is only used to bootstrap the persisted
    `lastAttemptAt` cutoff the very first time this runs on a machine (when
    no state file exists yet). Required for safe use from a shared/
    interactive session (e.g. run_test.py's automatic per-spec cleanup),
    where an unscoped blanket kill/delete could otherwise take out
    something the current user was working on.

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
$StatePath = Join-Path $env:LOCALAPPDATA 'ui-automation\cleanup-state.json'

# --- Persisted manifest (scoped mode only) --------------------------------
# See .DESCRIPTION above. `pendingProcesses`/`pendingPaths` are the
# ownership/retry manifest: specific resources this script has already
# targeted (whether via time-scope detection or a prior retry) and failed
# to remove, kept keyed precisely enough (pid+name+start time; exact path)
# to avoid ever conflating them with an unrelated resource, e.g. a reused
# PID.
function Get-CleanupState {
    $default = [pscustomobject]@{
        lastAttemptAt    = $Since
        pendingProcesses = @()
        pendingPaths     = @()
    }
    if (-not (Test-Path $StatePath)) { return $default }
    try {
        $raw = Get-Content -Path $StatePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Host "    ! cleanup-state.json unreadable/corrupt, bootstrapping fresh: $_"
        return $default
    }
    $lastAttemptAt = $Since
    if ($raw.lastAttemptAt) {
        try {
            $lastAttemptAt = [datetime]::Parse(
                $raw.lastAttemptAt, [System.Globalization.CultureInfo]::InvariantCulture,
                [System.Globalization.DateTimeStyles]::RoundtripKind)
        } catch { }
    }
    $pendingProcesses = @()
    if ($raw.pendingProcesses) { $pendingProcesses = @($raw.pendingProcesses) }
    $pendingPaths = @()
    if ($raw.pendingPaths) { $pendingPaths = @($raw.pendingPaths) }
    return [pscustomobject]@{
        lastAttemptAt    = $lastAttemptAt
        pendingProcesses = $pendingProcesses
        pendingPaths     = $pendingPaths
    }
}

function Save-CleanupState {
    param($LastAttemptAt, $PendingProcesses, $PendingPaths)
    $dir = Split-Path -Path $StatePath -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $state = [pscustomobject]@{
        lastAttemptAt    = $LastAttemptAt.ToString('o')
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
$Cutoff = if ($Scoped) { $CleanupState.lastAttemptAt } else { $null }
$PendingProcessKeys = if ($Scoped) {
    @($CleanupState.pendingProcesses | ForEach-Object { "$($_.pid)|$($_.name)|$($_.startTime)" })
} else { @() }
$PendingPathSet = if ($Scoped) { @($CleanupState.pendingPaths) } else { @() }

# Survivors of THIS pass (still present after an attempted kill/delete, or
# still out of reach) become next invocation's retry manifest.
$SurvivingProcesses = @()
$SurvivingPaths = @()

# A resource is in scope when: legacy unscoped mode (-Since omitted), OR its
# own timestamp is at/after the persisted cutoff (created/started since the
# last cleanup attempt -- catches leftovers from a run whose own cleanup
# never ran), OR it is a already-known pending offender (retried
# unconditionally regardless of age until it actually goes away).
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
    Save-CleanupState -LastAttemptAt (Get-Date) -PendingProcesses $SurvivingProcesses -PendingPaths $SurvivingPaths
}

Write-Host "==> Cleanup complete" -ForegroundColor Green
exit 0
