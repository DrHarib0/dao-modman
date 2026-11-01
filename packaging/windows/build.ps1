# Builds dao-modman.exe for Windows with PyApp (https://ofek.dev/pyapp/) and zips it with
# README.txt into dist\dao-modman-windows.zip. Needs Rust (cargo) on PATH.
#
# The exe holds no dao-modman of its own: on first start it fetches a standalone Python and
# installs the newest dao-modman from PyPI into its own folder; `dao-modman.exe self update`
# upgrades it. So one exe serves every release.

param(
    [string]$Out = (Join-Path $PSScriptRoot "..\..\dist")
)

$ErrorActionPreference = "Stop"
$PyAppVersion = "v0.30.0"
$PyAppSha256 = "D80190FDB2947176E85416B1D9B61F8707FAC70633ADFD1A0C02811A51989E2A"

$work = Join-Path ([IO.Path]::GetTempPath()) "dao-modman-pyapp-build"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory $work | Out-Null

$zip = Join-Path $work "source.zip"
$url = "https://github.com/ofek/pyapp/releases/download/$PyAppVersion/source.zip"
Invoke-WebRequest $url -OutFile $zip
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash
if ($hash -ne $PyAppSha256) { throw "PyApp source $PyAppVersion has an unexpected checksum: $hash" }
Expand-Archive $zip -DestinationPath $work
$source = Join-Path $work "pyapp-$PyAppVersion"

# What the exe installs and how it runs it; see https://ofek.dev/pyapp/latest/config/
# An unpinned requirement installs the newest dao-modman, and `self update` upgrades it to
# the newest again. PyApp still wants a version: with a dependency file it is only a label,
# the newest release at build time.
$requirements = Join-Path $work "requirements.txt"
Set-Content -Path $requirements -Value "dao-modman" -Encoding ascii
$env:PYAPP_PROJECT_NAME = "dao-modman"
$env:PYAPP_PROJECT_DEPENDENCY_FILE = $requirements
$env:PYAPP_PROJECT_VERSION = (Invoke-RestMethod "https://pypi.org/pypi/dao-modman/json").info.version
$env:PYAPP_EXEC_MODULE = "dao_modman"
$env:PYAPP_PYTHON_VERSION = "3.14"
$env:PYAPP_UV_ENABLED = "1"              # uv installs much faster than pip
$env:PYAPP_PASS_LOCATION = "1"           # PYAPP=<exe path>: dao-modman offers updates with it

Push-Location $source
try {
    cargo build --release
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed" }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force $Out | Out-Null
$exe = Join-Path $Out "dao-modman.exe"
Copy-Item (Join-Path $source "target\release\pyapp.exe") $exe
$readme = Join-Path $Out "README.txt"
Copy-Item (Join-Path $PSScriptRoot "README.txt") $readme
$package = Join-Path $Out "dao-modman-windows.zip"
Compress-Archive -Path $exe, $readme -DestinationPath $package -Force
Write-Output "built $package"
