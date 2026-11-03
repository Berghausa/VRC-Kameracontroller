param([string]$Python = 'python', [string]$Iscc = '')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name VRC-Kameracontroller --collect-all zeroconf --hidden-import ifaddr --paths camera_controller camera_controller/controller.py
    if ($LASTEXITCODE -ne 0) { throw 'EXE-Erstellung fehlgeschlagen.' }
    if (-not $Iscc) {
        $Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$PSScriptRoot\tools\inno\ISCC.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if (-not $Iscc) { throw 'Inno Setup 6 fehlt. Offiziell installieren oder -Iscc angeben.' }
    $env:CAMERA_VERSION = (& $Python -c "import sys;sys.path.insert(0,'camera_controller');from version import VERSION;print(VERSION)").Trim()
    & $Iscc installer/setup.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer-Erstellung fehlgeschlagen.' }
    Copy-Item dist/VRC-Kameracontroller.exe release/VRC-Kameracontroller.exe
    $lines = Get-ChildItem release/*.exe | ForEach-Object { '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot 'release/SHA256SUMS.txt'), [string[]]$lines, [Text.UTF8Encoding]::new($false))
} finally { Pop-Location }
