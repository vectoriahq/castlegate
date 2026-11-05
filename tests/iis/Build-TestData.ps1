<#
.SYNOPSIS
For the IIS tests: builds the test GeoPackage and projects with OSGeo4W's Python, from
tests/server/build_project.py and the rules in tests/server/fixtures.mjs.
#>
param(
    [string]$OSGeo4WRoot = 'C:\OSGeo4W',
    [string]$QgisApp = 'qgis-ltr',
    [string]$DataFolder = 'C:\castlegate\data'
)
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
New-Item -ItemType Directory -Force $DataFolder | Out-Null
$rules = node --input-type=module -e "import('file:///$($repo -replace '\\','/')/tests/server/fixtures.mjs').then(m => process.stdout.write(JSON.stringify(m.RULES)))"
$env:CASTLEGATE_TEST_RULES = $rules
$env:CASTLEGATE_OUT = $DataFolder
$app = Join-Path $OSGeo4WRoot "apps\$QgisApp"
$bat = Join-Path ([IO.Path]::GetTempPath()) 'castlegate-build.bat'
@(
    '@echo off',
    "call `"$OSGeo4WRoot\bin\o4w_env.bat`"",
    "set PATH=$app\bin;%PATH%",
    "set PYTHONPATH=$app\python;%PYTHONPATH%",
    "set QGIS_PREFIX_PATH=$app",
    'set QT_QPA_PLATFORM=offscreen',
    "`"%PYTHONHOME%\python.exe`" `"$repo\tests\server\build_project.py`""
) | Set-Content -Encoding ASCII $bat
cmd /c $bat
if ($LASTEXITCODE -ne 0) { throw "building the test data failed ($LASTEXITCODE)" }
Get-ChildItem $DataFolder | Select-Object Name, Length | Format-Table | Out-String
