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

    It NEVER touches the repo working tree, screenshots/, or the running
    GitHub Actions runner process. Safe to invoke at the start AND end of
    every run.

.PARAMETER Since
    Optional cutoff (a [datetime]). When set, the generic 'conhost'/'cmd'
    kill below is skipped entirely rather than scoped by time, because
    conhost's process ancestry doesn't reliably map to Win32
    ParentProcessId, so it can't be safely told apart from the caller's own
    interactive console. Pass this whenever invoking the script from an
    interactive/shared session (e.g. run_test.py's automatic per-spec
    cleanup) -- conhost/cmd cleanup for that spec then relies on its own
    "Clean up" steps / on_failure_capture, which only close windows THAT
    spec captured. Omit it on a dedicated, test-only CI DevBox runner to
    keep the original aggressive blanket conhost/cmd kill.

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

# --- 1. Terminate leftover UI processes -----------------------------------
# List of processes commonly spawned by test cases. NOT included: pwsh
# (the runner shell itself), sshd, GitHub runner processes.
# 'conhost'/'cmd' are handled separately below (scoped by -Since) because,
# unlike devenv/MSBuild/ServiceHub/vshost/notepad, they are also used to
# host unrelated interactive shells (e.g. the caller's own session), so a
# blanket kill is unsafe outside a dedicated CI DevBox.
$processNames = @(
    'devenv',           # Visual Studio
    'ServiceHub.*',     # VS ServiceHub workers (loose pattern below)
    'MSBuild',          # MSBuild workers left after a build
    'vshost',           # VS test host
    'notepad'
)

foreach ($name in $processNames) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            Write-Host "    kill $($_.ProcessName) (PID $($_.Id))"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        } catch { }
    }
}

# ServiceHub / VBCSCompiler match by wildcard.
Get-Process | Where-Object { $_.ProcessName -like 'ServiceHub*' -or $_.ProcessName -eq 'VBCSCompiler' } |
    ForEach-Object {
        Write-Host "    kill $($_.ProcessName) (PID $($_.Id))"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }

# conhost / cmd, spawned via launch.py. These are intentionally NOT scoped
# by -Since: conhost's process ancestry (via ConPTY/legacy console attach)
# doesn't reliably map to Win32 ParentProcessId, so time/ancestor heuristics
# can't safely tell a test-spawned console apart from the caller's own
# interactive shell -- as observed killing this very script's console host
# during testing. When -Since is passed (i.e. an automatic per-run caller
# such as run_test.py), skip this step entirely and rely on the per-spec
# capture-based cleanup instead (each CSV's own "Clean up" phase, and
# on_failure_capture on failure, close only the conhost/cmd windows THAT
# spec itself launched, via their captured *hwnd vars). Without -Since
# (legacy CI blanket mode on a dedicated, test-only DevBox), keep killing
# every conhost/cmd on the box.
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
# Uses -Recurse -Force; if a handle is still held, we log and move on.
$homeFolders = @('MyGlobal', 'test')
foreach ($folder in $homeFolders) {
    $path = Join-Path $HOME $folder
    if (Test-Path $path) {
        Write-Host "    rmdir $path"
        Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ConsoleApp / WindowsApp1 with numeric suffixes (VS auto-suffixes on collision).
Get-ChildItem -Path $HOME -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1)\d*$' } |
    ForEach-Object {
        Write-Host "    rmdir $($_.FullName)"
        Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }

# Same under %USERPROFILE%\source\repos\ (VS default new-project location).
$reposDir = Join-Path $HOME 'source\repos'
if (Test-Path $reposDir) {
    Get-ChildItem -Path $reposDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(ConsoleApp|WindowsApp1|MyGlobal|test)\d*$' } |
        ForEach-Object {
            Write-Host "    rmdir $($_.FullName)"
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
}

# --- 3. Delete leftover temp files in $HOME -------------------------------
$homeFiles = @('dn_info.txt', 'aspnet_majors.json')
foreach ($file in $homeFiles) {
    $path = Join-Path $HOME $file
    if (Test-Path $path) {
        Write-Host "    rm $path"
        Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "==> Cleanup complete" -ForegroundColor Green
exit 0
