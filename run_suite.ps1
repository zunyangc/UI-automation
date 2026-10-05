# run_suite.ps1 — Shortcut for: uv run python run_suite.py <args>
# Runs a set of CSV specs with auto-retry of failed cases and a local
# HTML/JSON report, without going through an LLM.
#
# Examples:
#   .\run_suite.ps1 test_cases\e2e-001-verify_dotnet_info.csv
#   .\run_suite.ps1 --all
#   .\run_suite.ps1 --all --max-retries 2 --suite-timeout-min 180
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    $Rest
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

uv run python run_suite.py @Rest
exit $LASTEXITCODE
