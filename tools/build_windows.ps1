param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$WorkRoot
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$distPath = [IO.Path]::GetFullPath($OutputRoot)
$buildPath = [IO.Path]::GetFullPath($WorkRoot)
if ((Test-Path -LiteralPath $distPath) -or (Test-Path -LiteralPath $buildPath)) {
    throw 'Use new output and work directories. Prior builds are never overwritten.'
}
$buildBase = (& $PythonExe -I -c "import sys; print(sys.base_prefix)").Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Python base prefix' }
$savedPath = $env:PATH
$savedPythonHome = $env:PYTHONHOME
$savedPythonPath = $env:PYTHONPATH
try {
    $env:PATH = @((Split-Path -Parent $PythonExe), (Join-Path $buildBase 'Library\bin'), $buildBase, (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    & $PythonExe -I -m PyInstaller --clean --distpath $distPath --workpath $buildPath (Join-Path $PSScriptRoot 'windows.spec')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
} finally {
    $env:PATH = $savedPath
    $env:PYTHONHOME = $savedPythonHome
    $env:PYTHONPATH = $savedPythonPath
}
