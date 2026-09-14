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
    dedicated, test-only DevBox runner. With -Since (see below), steps 1-3
    are instead scoped to resources created/started during the current run,
    so this is also safe to invoke automatically from a shared/interactive
    session.

    It NEVER touches the repo working tree, screenshots/, or the running
    GitHub Actions runner process. Safe to invoke at the start AND end of
    every run.

.PARAMETER Since
    Optional cutoff (a [datetime]). When set, this script's destructive
    actions are scoped to resources created/started at or after this time,
    so it never touches an unrelated pre-existing IDE/process/project that
    merely shares a common name (devenv, MSBuild, ServiceHub*, vshost,
    notepad; MyGlobal/test/ConsoleApp*/WindowsApp1* folders; the known temp
    files) -- required for safe use from a shared/interactive session (e.g.
    run_test.py's automatic per-spec cleanup), where a blanket kill/delete
    could otherwise take out something the current user was working on.

    The one exception is 'conhost'/'cmd': that kill is skipped ENTIRELY
    (not just time-scoped) whenever -Since is set, because conhost's
    process ancestry doesn't reliably map to Win32 ParentProcessId, so a
    freshly spawned conhost hosting THIS SCRIPT's own invocation can itself
    be newer than -Since -- time-scoping alone was observed to still kill
    the caller's own console during testing. conhost/cmd cleanup for an
    automatic per-spec caller instead relies entirely on that spec's own
    "Clean up" steps / on_failure_capture, which only close windows THAT
    spec captured into a `*hwnd` var.

    Omit -Since on a dedicated, test-only CI DevBox runner to keep the
    original aggressive, unscoped blanket behavior for everything.

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

# Returns $true when `-Since` was not passed (legacy unscoped CI mode), or
# when $timestamp is at/after it. Returns $false (skip) when $timestamp is
# $null (can't prove the resource belongs to this run -- e.g. StartTime
# unreadable due to access-denied) so we err on the side of NOT touching it.
function Test-WithinRunScope {
    param($timestamp)
    if (-not $Since) { return $true }
    if (-not $timestamp) { return $false }
    return $timestamp -ge $Since
}

# --- 1. Terminate leftover UI processes -----------------------------------
# List of processes commonly spawned by test cases. NOT included: pwsh
# (the runner shell itself), sshd, GitHub runner processes.
# When -Since is set, each kill is scoped to processes started at/after
# that time, so a pre-existing, unrelated instance (e.g. a VS window the
# current user already had open) is left alone -- only prove-able resources
# from THIS run are touched. 'conhost'/'cmd' are handled separately below
# (skipped entirely, not time-scoped) because, unlike these, their process
# ancestry can't be safely told apart from the caller's own session.
$processNames = @(
    'devenv',           # Visual Studio
    'ServiceHub.*',     # VS ServiceHub workers (loose pattern below)
    'MSBuild',          # MSBuild workers left after a build
    'vshost',           # VS test host
    'notepad'
)

foreach ($name in $processNames) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        $proc = $_
        $startTime = $null
        try { $startTime = $proc.StartTime } catch { }
        if (-not (Test-WithinRunScope $startTime)) { return }
        try {
            Write-Host "    kill $($proc.ProcessName) (PID $($proc.Id))"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

# ServiceHub / VBCSCompiler match by wildcard.
Get-Process | Where-Object { $_.ProcessName -like 'ServiceHub*' -or $_.ProcessName -eq 'VBCSCompiler' } |
    ForEach-Object {
        $proc = $_
        $startTime = $null
        try { $startTime = $proc.StartTime } catch { }
        if (-not (Test-WithinRunScope $startTime)) { return }
        Write-Host "    kill $($proc.ProcessName) (PID $($proc.Id))"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }

# conhost / cmd, spawned via launch.py. These are intentionally NOT scoped
# by -Since (not even by time): conhost's process ancestry (via ConPTY/
# legacy console attach) doesn't reliably map to Win32 ParentProcessId, so
# time/ancestor heuristics can't safely tell a test-spawned console apart
# from the caller's own interactive shell -- as observed killing this very
# script's console host during testing. When -Since is passed (i.e. an
# automatic per-run caller such as run_test.py), skip this step entirely
# and rely on the per-spec capture-based cleanup instead (each CSV's own
# "Clean up" phase, and on_failure_capture on failure, close only the
# conhost/cmd windows THAT spec itself launched, via their captured *hwnd
# vars). Without -Since (legacy CI blanket mode on a dedicated, test-only
# DevBox), keep killing every conhost/cmd on the box.
if (-not $Since) {
    Get-Process -Name 'conhost', 'cmd' -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Write-Host "    kill $($_.ProcessName) (PID $($_.Id))"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

# --- 2. Delete leftover artifact folders in $HOME -------------------------
# These are the folder names created by scripts in test_cases/*.csv.
# When -Since is set, only folders CREATED at/after that time are deleted,
# so a pre-existing, unrelated folder that happens to share one of these
# generic names (e.g. a user's own "test" folder) is left alone.
# Uses -Recurse -Force; if a handle is still held, we log and move on.
$homeFolders = @('MyGlobal', 'test')
foreach ($folder in $homeFolders) {
    $path = Join-Path $HOME $folder
    if (Test-Path $path) {
        $created = $null
        try { $created = (Get-Item -Path $path -ErrorAction Stop).CreationTime } catch { }
        if (-not (Test-WithinRunScope $created)) { continue }
        Write-Host "    rmdir $path"
        Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ConsoleApp / WindowsApp1 with numeric suffixes (VS auto-suffixes on collision).
Get-ChildItem -Path $HOME -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1)\d*$' } |
    ForEach-Object {
        if (-not (Test-WithinRunScope $_.CreationTime)) { return }
        Write-Host "    rmdir $($_.FullName)"
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }

# Same under %USERPROFILE%\source\repos\ (VS default new-project location).
$reposDir = Join-Path $HOME 'source\repos'
if (Test-Path $reposDir) {
    Get-ChildItem -Path $reposDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1|MyGlobal|test)\d*$' } |
        ForEach-Object {
            if (-not (Test-WithinRunScope $_.CreationTime)) { return }
            Write-Host "    rmdir $($_.FullName)"
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
}

# --- 3. Delete leftover temp files in $HOME -------------------------------
# Scoped by last-write time (these files get overwritten every run, so
# write time -- not creation time -- reflects whether THIS run touched it).
$homeFiles = @('dn_info.txt', 'aspnet_majors.json')
foreach ($file in $homeFiles) {
    $path = Join-Path $HOME $file
    if (Test-Path $path) {
        $written = $null
        try { $written = (Get-Item -Path $path -ErrorAction Stop).LastWriteTime } catch { }
        if (-not (Test-WithinRunScope $written)) { continue }
        Write-Host "    rm $path"
        Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "==> Cleanup complete" -ForegroundColor Green
exit 0
