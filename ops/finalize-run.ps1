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

.NOTES
    This is intentionally aggressive about stale processes but conservative
    about files — it only touches known artifact names, not arbitrary paths.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'   # never fail the workflow on cleanup

Write-Host "==> UI-automation post-run cleanup" -ForegroundColor Cyan

# --- 1. Terminate leftover UI processes -----------------------------------
# List of processes commonly spawned by test cases. NOT included: pwsh
# (the runner shell itself), sshd, GitHub runner processes.
$processNames = @(
    'devenv',           # Visual Studio
    'ServiceHub.*',     # VS ServiceHub workers (loose pattern below)
    'MSBuild',          # MSBuild workers left after a build
    'vshost',           # VS test host
    'notepad',
    'conhost',          # spawned via launch.py with conhost.exe
    'cmd'               # spawned via launch.py with cmd.exe
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
