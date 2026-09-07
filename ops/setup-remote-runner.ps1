# One-line DevBox setup for the UI-automation self-hosted runner (fork-based model).
# Run this ONCE per DevBox in an Administrator PowerShell:
#   irm https://raw.githubusercontent.com/<your-handle>/UI-automation/main/ops/setup-remote-runner.ps1 | iex
#
# Slot model:
#   The workflow YAML exposes 4 static DevBox slots: devbox-1 .. devbox-4.
#   This script asks GitHub which slots on your fork are currently free and
#   lets you claim one. No workflow edits, no git push -- the YAML never
#   changes. To free a slot, run ops/remove-runner.ps1 on the DevBox that
#   currently holds it.

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

$MaxSlots = 4

# --- Admin check ---------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "This script must be run in an Administrator PowerShell."
}

Set-Location $HOME
$RepoPath = Join-Path $HOME 'UI-automation'

# --- Refuse if a runner is already installed on this DevBox --------------
if (Test-Path 'C:\actions-runner\config.cmd') {
    throw "A self-hosted runner is already installed at C:\actions-runner. Deregister it first with 'ops\remove-runner.ps1 -Label <current-label>' before registering a new slot."
}

# --- Detect GitHub handle ------------------------------------------------
Write-Step "Resolving your GitHub handle..."
$GhHandle = $null

if (Test-Path (Join-Path $RepoPath '.git')) {
    Push-Location $RepoPath
    try {
        $originUrl = (git remote get-url origin 2>$null)
        if ($originUrl -and $originUrl -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)') {
            $detectedOwner = $Matches.owner
            if ($detectedOwner -eq 'william051200') {
                Write-Warn "Existing clone at $RepoPath points at the UPSTREAM repo (william051200)."
                Write-Host "    Choose:" -ForegroundColor Yellow
                Write-Host "      [F] Fork it now (opens the fork URL, then continue)"
                Write-Host "      [H] I already forked -- enter my handle"
                Write-Host "      [C] Cancel"
                $choice = Read-Host "Choice (F/H/C)"
                switch ($choice.ToUpper()) {
                    'F' {
                        Start-Process 'https://github.com/william051200/UI-automation/fork'
                        Read-Host "Press <Enter> after forking (make sure Actions is enabled on your fork)"
                        $GhHandle = Read-Host "Your GitHub handle (fork owner)"
                    }
                    'H' {
                        $GhHandle = Read-Host "Your GitHub handle (fork owner)"
                    }
                    default { throw "Cancelled by user." }
                }
            } else {
                $GhHandle = $detectedOwner
                Write-Ok "Detected handle from existing clone: $GhHandle"
            }
        }
    } finally {
        Pop-Location
    }
}

if (-not $GhHandle) {
    $GhHandle = Read-Host "Your GitHub handle (fork owner, e.g. octocat)"
    if (-not $GhHandle) { throw "GitHub handle is required." }
}

$Repo = "$GhHandle/UI-automation"
$RepoUrl = "https://github.com/$Repo.git"
Write-Ok "Target fork: $Repo"

# --- Ensure git is available (prerequisite) -----------------------------
Write-Step "Checking git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "git is not installed and winget is unavailable. Install Git for Windows manually, then re-run."
    }
    Write-Warn "git missing; installing via winget..."
    winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements | Out-Null
    $env:Path = ([Environment]::GetEnvironmentVariable('Path','Machine')) + ';' + ([Environment]::GetEnvironmentVariable('Path','User'))
}
Write-Ok "git: $(git --version)"

# --- Clone or refresh the fork ------------------------------------------
Write-Step "Cloning/refreshing $Repo into $RepoPath..."
if (Test-Path (Join-Path $RepoPath '.git')) {
    Push-Location $RepoPath
    try {
        $currentUrl = (git remote get-url origin 2>$null).Trim()
        if ($currentUrl -ne $RepoUrl) {
            Write-Warn "Repointing origin from '$currentUrl' to '$RepoUrl'"
            git remote set-url origin $RepoUrl
        }
        git fetch origin main | Out-Host
        git checkout main | Out-Host
        git reset --hard origin/main | Out-Host
        Write-Ok "Clone refreshed."
    } finally {
        Pop-Location
    }
} else {
    git clone $RepoUrl $RepoPath | Out-Host
    Write-Ok "Cloned."
}

Set-Location $RepoPath

# --- Install uv + uv sync ------------------------------------------------
Write-Step "Ensuring uv is installed..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    irm https://astral.sh/uv/install.ps1 | iex
}
$env:Path = "$HOME\.local\bin;$env:Path"
Write-Ok "uv: $(uv --version)"

# Match UV_PROJECT_ENVIRONMENT in .github/workflows/run-ui-tests.yml so setup pre-warms the CI venv.
$projectSetup = Join-Path $RepoPath 'ops\setup.ps1'
$projectEnvironment = 'C:\uv-venvs\ui-automation'
Write-Step "Installing Python dependencies (venv: $projectEnvironment)..."
& $projectSetup -EnvironmentPath $projectEnvironment
Write-Ok "Python environment ready."

# --- Prompt for PAT (used for slot check + registration token) ----------
Write-Step "GitHub Personal Access Token"
Write-Host "    A PAT with 'repo' scope is required so we can:" -ForegroundColor Yellow
Write-Host "      * list the runners currently registered on your fork" -ForegroundColor Yellow
Write-Host "      * fetch a runner registration token (no browser copy-paste)" -ForegroundColor Yellow
Write-Host "    Create one at: https://github.com/settings/tokens (classic, 'repo' scope)" -ForegroundColor Cyan
$PatSecure = Read-Host "PAT" -AsSecureString
$Pat = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PatSecure))
if (-not $Pat) { throw "No PAT provided." }

$Headers = @{
    Authorization = "Bearer $Pat"
    Accept        = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent'  = 'ui-automation-setup'
}

# --- Discover free DevBox slots on this fork ----------------------------
Write-Step "Checking which devbox-N slots are free on $Repo..."
try {
    $runners = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/actions/runners?per_page=100" -Headers $Headers -Method Get
} catch {
    throw "Could not list runners on $Repo. Check that the PAT has 'repo' scope and that your fork exists. Error: $_"
}

$existing = @()
if ($runners.runners) { $existing = @($runners.runners | ForEach-Object { $_.name }) }
Write-Ok "Registered runners on fork: $(if ($existing) { $existing -join ', ' } else { '(none)' })"

$free = 1..$MaxSlots | Where-Object { "devbox-$_" -notin $existing }
if (-not $free) {
    throw "All $MaxSlots DevBox slots (devbox-1..devbox-$MaxSlots) are already registered on $Repo. Free one with 'ops\remove-runner.ps1 -Label <label>' on the DevBox that owns it, then re-run this script."
}

Write-Host "    Available slots: $($free -join ', ')" -ForegroundColor Yellow
do {
    $picked = Read-Host "Pick a slot number ($($free -join '/'))"
    $pickedInt = 0
    $ok = [int]::TryParse($picked, [ref]$pickedInt) -and ($pickedInt -in $free)
    if (-not $ok) { Write-Warn "Invalid choice '$picked'. Must be one of: $($free -join ', ')" }
} while (-not $ok)

$Label = "devbox-$pickedInt"
Write-Ok "Label: $Label"

# --- Fetch a runner registration token via API --------------------------
Write-Step "Requesting a runner registration token from GitHub..."
try {
    $tokenResp = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/actions/runners/registration-token" -Headers $Headers -Method Post
} catch {
    throw "Could not fetch a registration token. Check that the PAT has 'repo' scope. Error: $_"
}
$Token = $tokenResp.token
if (-not $Token) { throw "GitHub did not return a registration token." }
Write-Ok "Registration token acquired (expires $($tokenResp.expires_at))."

# --- Delegate to setup-runner.ps1 ---------------------------------------
Write-Step "Invoking ops\setup-runner.ps1..."
$setupRunner = Join-Path $RepoPath 'ops\setup-runner.ps1'
& $setupRunner -Label $Label -Repo $Repo -Token $Token -RepoPath $RepoPath
