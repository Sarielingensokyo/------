$ErrorActionPreference = 'Stop'
$musescore = 'C:\Program Files\MuseScore 4\bin\MuseScore4.exe'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $musescore)) {
    throw "MuseScore 4 not found: $musescore"
}

Get-ChildItem -LiteralPath $here -Filter '*.musicxml' | ForEach-Object {
    $base = Join-Path $here $_.BaseName
    & $musescore -o ($base + '.mscz') $_.FullName
    & $musescore -o ($base + '.pdf')  $_.FullName
    & $musescore -o ($base + '.mid')  $_.FullName
    & $musescore -o ($base + '.wav')  $_.FullName
}
