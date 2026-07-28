#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Bootstrap the local NaviGraph dev environment on Windows.

.DESCRIPTION
    Copies infra/.env.example to infra/.env if it doesn't already exist, then
    brings up the full docker-compose stack. Real PowerShell (not a bash
    script renamed) -- run it with `.\tools\scripts\bootstrap.ps1` from
    Windows PowerShell or PowerShell 7+.

.EXAMPLE
    .\tools\scripts\bootstrap.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

$EnvExample = Join-Path $RepoRoot "infra\.env.example"
$EnvFile = Join-Path $RepoRoot "infra\.env"
$ComposeFile = Join-Path $RepoRoot "infra\docker-compose.yml"

Write-Host "==> NaviGraph bootstrap"
Write-Host "    repo root: $RepoRoot"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "==> infra\.env not found -- copying from infra\.env.example"
        Copy-Item -Path $EnvExample -Destination $EnvFile
    }
    else {
        Write-Error "infra\.env.example not found at $EnvExample. This is owned by the infra workstream; create it (or infra\.env directly) before continuing."
        exit 1
    }
}
else {
    Write-Host "==> infra\.env already exists -- leaving it untouched"
}

if (-not (Test-Path $ComposeFile)) {
    Write-Error "infra\docker-compose.yml not found at $ComposeFile. This is owned by the infra workstream and must exist before bootstrap can bring up the stack."
    exit 1
}

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
if (-not $dockerCmd) {
    Write-Error "docker is not installed or not on PATH. Install Docker Desktop and retry."
    exit 1
}

Write-Host "==> Building and starting the full stack (docker compose up -d --build)"
docker compose -f $ComposeFile up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "==> Stack is starting in the background. Containers take a bit to become healthy"
Write-Host "    (Neo4j and Trino in particular can take 30-60s on first boot)."
Write-Host ""
Write-Host "    Once everything reports healthy, run the smoke test (Git Bash / WSL):"
Write-Host ""
Write-Host "        tools/scripts/smoke-test.sh"
Write-Host ""
Write-Host "    To watch container status in the meantime:"
Write-Host ""
Write-Host "        docker compose -f infra/docker-compose.yml ps"
Write-Host ""
