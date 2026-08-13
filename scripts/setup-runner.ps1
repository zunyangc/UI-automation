<#
.SYNOPSIS
    Register the current DevBox as a self-hosted GitHub Actions runner
    for the UI-automation repository (fork-based model).

.DESCRIPTION
    One-time bootstrap. Run this ON YOUR DEVBOX (RDP'd, unlocked) once,
    then never again for that DevBox. After it completes, your DevBox
    is a runner reachable from your fork's Actions tab.

    Normally invoked by scripts/setup-remote-runner.ps1 (see docs/REMOTE_RUNNING.md).

.PARAMETER Label
    The label to register this runner under. Accepted formats:
      <DDMMYYYY>-<N>                    e.g. 12082026-1
      <DDMMYYYY>-<Name>-<N>             e.g. 12082026-desk-1
      <INITIALS>-<DDMMYYYY>-<N>         (legacy) e.g. ZY-24072026-1

.PARAMETER Repo
    The GitHub repo to register the runner against (must be YOUR fork).
    If omitted, auto-detected from `git remote get-url origin` on -RepoPath.

.PARAMETER Token
    Registration token from GitHub. If omitted, you'll be prompted with
    the URL to fetch it from.

.PARAMETER InstallRoot
    Directory to install the runner into. Default: C:\actions-runner

.PARAMETER RepoPath
    Local clone of the repo where the workflow file lives. Default:
    $HOME\UI-automation

.EXAMPLE
    .\scripts\setup-runner.ps1 -Label 12082026-1

.EXAMPLE
    .\scripts\setup-runner.ps1 -Label 12082026-desk-1 -Token ABCDEF...

.NOTES
    Must be run in an Administrator PowerShell (installing a Scheduled
    Task at logon requires elevation).
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^([A-Z]{2}-)?\d{8}(-[A-Za-z0-9]+)*-\d+$')]
    [string]$Label,

    [string]$Repo,

    [string]$Token,

    [string]$InstallRoot = 'C:\actions-runner',

    [string]$RepoPath = (Join-Path $HOME 'UI-automation')
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

# --- Admin check ----------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "This script must be run in an Administrator PowerShell."
}

# --- Resolve target repo (auto-detect from local clone if not given) -----
if (-not $Repo) {
    if (-not (Test-Path $RepoPath)) {
        throw "-Repo was not passed and -RepoPath '$RepoPath' does not exist. Clone your fork first, or pass -Repo <owner/name>."
    }
    Push-Location $RepoPath
    try {
        $originUrl = (git remote get-url origin 2>$null).Trim()
    } finally {
        Pop-Location
    }
    if (-not $originUrl) {
        throw "Could not read 'origin' remote in $RepoPath. Pass -Repo <owner/name> explicitly."
    }
    # Match https://github.com/<owner>/<repo>(.git)? or git@github.com:<owner>/<repo>(.git)?
    if ($originUrl -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)') {
        $Repo = "$($Matches.owner)/$($Matches.repo)"
    } else {
        throw "Could not parse GitHub owner/repo from origin URL '$originUrl'. Pass -Repo <owner/name> explicitly."
    }
    Write-Ok "Detected repo from origin: $Repo"
    if ($Repo -match '^william051200/') {
        throw "Origin still points at the upstream repo. Fork william051200/UI-automation to your account, re-clone from your fork, and re-run setup-remote-runner.ps1."
    }
}

# Extract GitHub handle from the resolved repo (owner part) -- used as the
# commenting/attribution name next to the label in the workflow file.
$GhHandle = ($Repo -split '/')[0]

# --- Prereqs: uv, git, python --------------------------------------------
Write-Step "Checking prerequisites (uv, git, python)..."

function Ensure-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget is not available. Install App Installer from the Microsoft Store, then re-run."
    }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Ensure-Winget
    Write-Warn "git missing; installing via winget..."
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
}
Write-Ok "git: $(git --version)"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Warn "uv missing; installing from astral.sh..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$HOME\.local\bin;$env:Path"
}
Write-Ok "uv: $(uv --version)"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    # uv will fetch python on first `uv sync`; nothing to install here.
    Write-Warn "python not on PATH -- uv will provision one on first sync."
} else {
    Write-Ok "python: $(python --version)"
}

# --- Token ---------------------------------------------------------------
if (-not $Token) {
    Write-Host ""
    Write-Host "A runner registration token is required." -ForegroundColor Yellow
    Write-Host "Get one from:" -ForegroundColor Yellow
    Write-Host "  https://github.com/$Repo/settings/actions/runners/new?arch=x64&os=win" -ForegroundColor Cyan
    Write-Host "Copy the token shown next to './config.cmd --token ...' and paste it here:"
    $Token = Read-Host -Prompt "Token" -AsSecureString |
        ForEach-Object { [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($_)) }
}
if (-not $Token) { throw "No token provided." }

# --- Download runner ------------------------------------------------------
Write-Step "Downloading latest actions/runner..."
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
Set-Location $InstallRoot

$latest = Invoke-RestMethod https://api.github.com/repos/actions/runner/releases/latest
$asset  = $latest.assets | Where-Object { $_.name -like 'actions-runner-win-x64-*.zip' } | Select-Object -First 1
if (-not $asset) { throw "Could not find a Windows x64 runner asset in the latest release." }

$zip = Join-Path $InstallRoot $asset.name
# Validate any existing zip by size. A truncated download from an earlier
# aborted run will fail extraction with "End of Central Directory record
# could not be found."
if (Test-Path $zip) {
    $localSize = (Get-Item $zip).Length
    if ($localSize -ne $asset.size) {
        Write-Warn "Existing '$($asset.name)' is $localSize bytes (expected $($asset.size)); re-downloading..."
        Remove-Item $zip -Force
    }
}
if (-not (Test-Path $zip)) {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
}

if (-not (Test-Path (Join-Path $InstallRoot 'config.cmd'))) {
    Write-Step "Extracting runner..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    try {
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $InstallRoot)
    } catch [System.IO.InvalidDataException] {
        Write-Warn "Zip appears corrupt; re-downloading and retrying..."
        Remove-Item $zip -Force
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $InstallRoot)
    }
}

# --- Configure runner (NOT as service: UI automation needs the interactive
#     desktop, and Windows services run in Session 0 with no UI access) ----
Write-Step "Configuring runner as '$Label' against $Repo..."
$runnerUrl = "https://github.com/$Repo"
& .\config.cmd `
    --url $runnerUrl `
    --token $Token `
    --name $Label `
    --labels $Label `
    --work "_work" `
    --unattended `
    --replace
if ($LASTEXITCODE -ne 0) { throw "config.cmd failed with exit code $LASTEXITCODE" }

Write-Ok "Runner '$Label' registered."

# --- Launch runner in the interactive session ----------------------------
# UI automation requires the desktop, so we start run.cmd in a visible
# PowerShell window that the tester leaves open (screens stay unlocked
# at all times per team policy).
Write-Step "Starting runner in a new PowerShell window..."
$runCmd = Join-Path $InstallRoot 'run.cmd'
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoExit', '-Command', "Set-Location '$InstallRoot'; & '$runCmd'") `
    -WorkingDirectory $InstallRoot | Out-Null
Write-Ok "Runner launched. Leave that PowerShell window open -- closing it stops the runner."

# --- Create a Scheduled Task so the runner auto-starts on user logon -----
Write-Step "Registering Scheduled Task 'GHRunner-$Label' to auto-start on logon..."
try {
    $taskName = "GHRunner-$Label"
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoExit -Command `"Set-Location '$InstallRoot'; & '$runCmd'`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    Write-Ok "Scheduled Task '$taskName' created. Runner auto-starts on logon."
} catch {
    Write-Warn "Could not register Scheduled Task: $_"
    Write-Warn "Runner will still work now, but you'll need to manually run:"
    Write-Warn "  cd $InstallRoot; .\run.cmd"
    Write-Warn "after each reboot/logon."
}

# --- Update workflow YAML to expose this label in the dropdown ------------
Write-Step "Adding '$Label' to .github/workflows/run-ui-tests.yml..."

$workflow = Join-Path $RepoPath '.github/workflows/run-ui-tests.yml'
if (-not (Test-Path $workflow)) {
    Write-Warn "Workflow file not found at $workflow; skipping YAML edit."
    Write-Warn "Add '- $Label   # $GhHandle' manually under target_devbox.options."
} else {
    $content = Get-Content -Path $workflow -Raw
    $newLine = "          - $Label # $GhHandle"

    if ($content -match [regex]::Escape("- $Label")) {
        Write-Ok "Label '$Label' is already present in the workflow -- nothing to do."
        $skipPush = $true
    } else {
        $skipPush = $false
        # Primary: find target_devbox.options block and append after the last existing bullet line.
        # Accept both legacy 'XX-DDMMYYYY-N' and new 'DDMMYYYY[-name]-N' entries.
        $pattern = '(?ms)(target_devbox:.*?options:[ \t]*\r?\n(?:[^\r\n]*\r?\n)*?)((?:[ ]{10}- (?:[A-Z]{2}-)?\d{8}(?:-[A-Za-z0-9]+)*-\d+[^\r\n]*\r?\n)+)'
        $match = [regex]::Match($content, $pattern)
        $updated = $null

        if ($match.Success) {
            $existingBlock = $match.Groups[2].Value
            $newBlock = $existingBlock.TrimEnd("`n") + "`n$newLine`n"
            $updated = $content.Substring(0, $match.Groups[2].Index) + $newBlock + $content.Substring($match.Groups[2].Index + $match.Groups[2].Length)
        } else {
            # Fallback: anchor on 'options:' under target_devbox and insert immediately after it,
            # skipping only leading comment lines. Works even when the options list is empty.
            $fallback = '(?ms)(target_devbox:.*?options:[ \t]*\r?\n(?:[ ]{10}#[^\r\n]*\r?\n)*)'
            $m2 = [regex]::Match($content, $fallback)
            if ($m2.Success) {
                $insertAt = $m2.Index + $m2.Length
                $updated = $content.Substring(0, $insertAt) + "$newLine`n" + $content.Substring($insertAt)
            }
        }

        if ($updated) {
            Set-Content -Path $workflow -Value $updated -NoNewline
            Write-Ok "Added '$Label # $GhHandle' to workflow."
        } else {
            Write-Warn "Could not locate target_devbox.options block in $workflow."
            Write-Warn "Add '$newLine' manually under target_devbox.options, then push."
            $skipPush = $true
        }
    }

    if (-not $skipPush) {
        Write-Step "Committing and pushing to origin/main..."
        Push-Location $RepoPath
        try {
            git add .github/workflows/run-ui-tests.yml
            git commit -m "Register DevBox runner: $Label" | Out-Host
            git push origin main | Out-Host
            Write-Ok "Workflow updated on origin/main. Label '$Label' is now selectable."
        } catch {
            Write-Warn "Push failed: $_"
            Write-Warn "Push manually from $RepoPath : git add -A; git commit -m 'Register $Label'; git push origin main"
        } finally {
            Pop-Location
        }
    }
}

Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Yellow
Write-Host "  1. Verify at: https://github.com/$Repo/settings/actions/runners"
Write-Host "     Your runner '$Label' should show status = Idle."
Write-Host "  2. Trigger a run from the Actions tab: pick a CSV + your label."
Write-Host ""
