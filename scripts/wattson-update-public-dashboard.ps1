param(
    [string]$RepositoryPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $RepositoryPath
python -m src.live update-public-dashboard
