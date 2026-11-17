# Hermes Image Persist - Windows installer wrapper.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Apply
#
# Finds a Python 3.9+ (Hermes' own environments first) and runs install.py.
param(
    [switch]$Apply,
    [switch]$Yes,
    [string]$Root = "",
    [string]$HermesHome = "",
    [switch]$NoSource,
    [switch]$NoShim,
    [switch]$NoGuard
)

$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot

function Find-Python {
    $candidates = @()
    $hermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'hermes' }
    # Hermes' own interpreter (PM store python recorded in the tree launcher)
    $launcher = Join-Path $hermesHome 'hermes-agent\.hermes\bin\hermes.exe'
    if (Test-Path $launcher) { $candidates += $launcher }
    # Python shipped under installs/ (PM dependency generations)
    Get-ChildItem -Path (Join-Path $hermesHome 'installs') -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 3 | ForEach-Object { $candidates += $_.FullName }
    # Python shipped under tools/ (Hermes' bundled interpreter, e.g. python-3.14.x)
    Get-ChildItem -Path (Join-Path $hermesHome 'tools') -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 3 | ForEach-Object { $candidates += $_.FullName }
    $candidates += @('python', 'py -3')
    foreach ($candidate in $candidates) {
        try {
            $null = Invoke-Expression ($candidate + ' -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"') 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host 'No Python 3.9+ found. Install Python 3.9+ (or install Hermes first) and retry.' -ForegroundColor Red
    exit 1
}

$argsList = @()
if ($Apply) { $argsList += '--apply' }
if ($Yes) { $argsList += '--yes' }
if ($Root) { $argsList += @('--root', $Root) }
if ($HermesHome) { $argsList += @('--home', $HermesHome) }
if ($NoSource) { $argsList += '--no-source' }
if ($NoShim) { $argsList += '--no-shim' }
if ($NoGuard) { $argsList += '--no-guard' }

Invoke-Expression ($python + ' "' + (Join-Path $Here 'install.py') + '" ' + ($argsList -join ' '))
exit $LASTEXITCODE
