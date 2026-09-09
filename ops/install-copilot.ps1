# ops/install-copilot.ps1 — Easy installer for the GitHub Copilot CLI.
#
# Installs BOTH (whichever is missing):
#   1. The standalone agentic Copilot CLI  (the `copilot` command)
#   2. The `gh copilot` extension          (on top of the GitHub `gh` CLI)
# ...then triggers interactive login for each.
#
# Usage on a Windows 10/11 machine (from the repo root):
#   .\ops\install-copilot.ps1
#
# Or one-line, straight from GitHub:
#   irm https://raw.githubusercontent.com/william051200/UI-automation/main/ops/install-copilot.ps1 | iex
#
# If your execution policy blocks scripts, use:
#   powershell -ExecutionPolicy Bypass -File .\ops\install-copilot.ps1
#
# Skip the interactive login prompts with:  .\ops\install-copilot.ps1 -NoLogin

[CmdletBinding()]
param(
    [switch]$NoLogin
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Test-HasCommand($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Update-MachineAndUserPath {
    # winget installs land in machine/user PATH; refresh this session from the registry.
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}

function Invoke-Winget($id) {
    if (-not (Test-HasCommand winget)) { return $false }
    Write-Warn "Installing '$id' via winget..."
    winget install --id $id --silent --accept-package-agreements --accept-source-agreements
    Update-MachineAndUserPath
    return $true
}

# 0. Preflight ------------------------------------------------------------
Write-Step "Checking PowerShell version..."
$psv = $PSVersionTable.PSVersion
if ($psv.Major -lt 6) {
    Write-Warn "PowerShell $psv detected. The standalone Copilot CLI recommends PowerShell 6+."
    Write-Warn "Consider installing it: winget install Microsoft.PowerShell"
} else {
    Write-Ok "PowerShell $psv."
}

# 1. Standalone Copilot CLI ----------------------------------------------
Write-Step "Checking for the standalone Copilot CLI (copilot)..."
if (Test-HasCommand copilot) {
    Write-Ok "copilot already installed: $((copilot --version) 2>&1)"
} else {
    $installed = Invoke-Winget "GitHub.Copilot"
    if (-not (Test-HasCommand copilot)) {
        if ($installed) {
            Write-Warn "winget ran but 'copilot' is not on PATH yet."
        }
        # Fallback: npm (requires Node.js 18+).
        Write-Warn "Falling back to npm install -g @github/copilot..."
        if (-not (Test-HasCommand node)) {
            Write-Warn "Node.js not found. Installing Node.js LTS..."
            Invoke-Winget "OpenJS.NodeJS.LTS" | Out-Null
        }
        if (-not (Test-HasCommand npm)) {
            throw "npm is unavailable. Install Node.js 18+ from https://nodejs.org/ and re-run, or install via 'winget install GitHub.Copilot'."
        }
        npm install -g "@github/copilot"
        Update-MachineAndUserPath
    }
    if (Test-HasCommand copilot) {
        Write-Ok "copilot installed: $((copilot --version) 2>&1)"
    } else {
        Write-Warn "copilot installed but not on PATH. Open a new terminal to pick it up."
    }
}

# 2. gh CLI ---------------------------------------------------------------
Write-Step "Checking for the GitHub CLI (gh)..."
if (Test-HasCommand gh) {
    Write-Ok "gh already installed: $((gh --version | Select-Object -First 1) 2>&1)"
} else {
    if (-not (Invoke-Winget "GitHub.cli")) {
        throw "gh not found and winget is unavailable. Install Git/GitHub CLI from https://cli.github.com/ and re-run."
    }
    if (-not (Test-HasCommand gh)) {
        throw "gh installed but not on PATH. Open a new PowerShell and re-run."
    }
    Write-Ok "gh installed: $((gh --version | Select-Object -First 1) 2>&1)"
}

# 3. gh copilot extension -------------------------------------------------
Write-Step "Checking for the 'gh copilot' extension..."
$extList = (gh extension list 2>&1) -join "`n"
if ($extList -match "gh-copilot") {
    Write-Warn "Extension present. Upgrading..."
    gh extension upgrade gh-copilot
    Write-Ok "gh copilot extension up to date."
} else {
    gh extension install github/gh-copilot
    Write-Ok "gh copilot extension installed."
}

# 4. Authentication (interactive) ----------------------------------------
if ($NoLogin) {
    Write-Step "Skipping login (-NoLogin)."
} else {
    Write-Step "Checking GitHub authentication (gh)..."
    gh auth status 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Not logged in to GitHub. Launching 'gh auth login'..."
        gh auth login
    } else {
        Write-Ok "Already authenticated with GitHub (gh)."
    }

    if (Test-HasCommand copilot) {
        Write-Step "Launching the Copilot CLI so you can authenticate..."
        Write-Warn "Inside Copilot, type '/login' and follow the prompts, then '/exit' when done."
        Write-Warn "(If you're already signed in, just type '/exit'.)"
        copilot
    }
}

# 5. Summary --------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " GitHub Copilot CLI setup complete." -ForegroundColor Green
Write-Host ""
Write-Host " Verify:" -ForegroundColor Green
Write-Host "   copilot --version" -ForegroundColor White
Write-Host "   gh copilot --help" -ForegroundColor White
Write-Host ""
Write-Host " Next: author test cases conversationally." -ForegroundColor Green
Write-Host " See docs\authoring-scenarios.md" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
