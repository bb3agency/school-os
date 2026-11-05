<#
.SYNOPSIS
  Builds the SchoolOS Tally agent MSI (D69, ADR-0032). Windows PowerShell 5.1 compatible.

.DESCRIPTION
  1. Reads pins.toml and refuses placeholder or malformed SHA-256 values.
  2. Downloads the Python embeddable runtime and WinSW by exact version and checks their SHA-256
     (and the published MD5 where pins.toml has one). Downloads are cached in <OutDir>\downloads.
  3. Stages Program Files content in <OutDir>\stage: python\ (embeddable runtime, with the agent
     wheel and its locked dependencies installed into python\Lib\site-packages by uv, for
     CPython 3.12 on x64 Windows, binary wheels only, hashes required), sos-tally-agent.exe
     (WinSW), sos-tally-agent.xml and tally-agent.cmd.
  4. Generates <OutDir>\payload.wxs (one component per file of python\) and runs `wix build`.

  Needs: uv on PATH; for the MSI also the WiX v4 dotnet tool `wix` with WixToolset.Util.wixext
  (the release workflow installs both at the version in pins.toml). -StageOnly stops before WiX.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File apps\edge-agent\installer\build.ps1 -Version 2026.11.1
#>
[CmdletBinding()]
param(
    # The CalVer release tag (YYYY.M.N); the MSI ProductVersion is (YYYY-2000).M.N.
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OutDir = "dist\tally-agent",
    [switch]$StageOnly
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is very slow with progress on 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$InstallerDir = $PSScriptRoot
$AgentDir = Split-Path -Parent $InstallerDir
$RepoRoot = Split-Path -Parent (Split-Path -Parent $AgentDir)

function Fail([string]$Message) {
    throw "build.ps1: $Message"
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    # Windows PowerShell 5.1 can turn a native tool's stderr (uv's progress lines) into errors
    # under 'Stop'; the exit code is the only verdict.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { Fail "$Exe failed with exit code $code" }
}

# --- 1. pins.toml (the subset of TOML it uses: [section] and key = "value") -------------------
function Read-Pins([string]$Path) {
    $pins = @{}
    $section = $null
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { continue }
        if ($line -match '^\[([A-Za-z0-9_-]+)\]$') {
            $section = $Matches[1]
            $pins[$section] = @{}
            continue
        }
        if ($line -match '^([A-Za-z0-9_]+)\s*=\s*"([^"]*)"\s*(#.*)?$') {
            if ($null -eq $section) { Fail "pins.toml: '$($Matches[1])' is outside a section" }
            $pins[$section][$Matches[1]] = $Matches[2]
            continue
        }
        Fail "pins.toml: cannot read the line '$line'"
    }
    return $pins
}

function Get-Pin($Pins, [string]$Name) {
    if (-not $Pins.ContainsKey($Name)) { Fail "pins.toml has no [$Name] section" }
    $pin = $Pins[$Name]
    foreach ($key in 'version', 'file', 'url', 'sha256') {
        if (-not $pin.ContainsKey($key) -or $pin[$key] -eq '') { Fail "pins.toml: [$Name] has no $key" }
    }
    if ($pin['sha256'] -notmatch '^[0-9a-f]{64}$') {
        Fail ("pins.toml: [$Name] sha256 is a placeholder or malformed ('$($pin['sha256'])'). " +
              "Put the reviewed SHA-256 of $($pin['file']) $($pin['version']) there (see the comment at the top of pins.toml).")
    }
    if (-not $pin['url'].StartsWith('https://')) { Fail "pins.toml: [$Name] url must be https" }
    if (-not $pin['url'].Contains($pin['version'])) { Fail "pins.toml: [$Name] url does not name version $($pin['version'])" }
    return $pin
}

function Get-Download($Pin, [string]$CacheDir) {
    $target = Join-Path $CacheDir $Pin['file']
    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host "Downloading $($Pin['file']) $($Pin['version'])"
        $partial = "$target.partial"
        Invoke-WebRequest -Uri $Pin['url'] -OutFile $partial -UseBasicParsing
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Pin['sha256']) {
        Remove-Item -LiteralPath $target -Force
        Fail "SHA-256 mismatch for $($Pin['file']): expected $($Pin['sha256']), got $actual. The file was deleted; do not change the pin without a review."
    }
    if ($Pin.ContainsKey('md5')) {
        $md5 = (Get-FileHash -LiteralPath $target -Algorithm MD5).Hash.ToLowerInvariant()
        if ($md5 -ne $Pin['md5']) { Fail "MD5 mismatch for $($Pin['file']) (publisher's value $($Pin['md5']), got $md5)" }
    }
    return $target
}

# --- version --------------------------------------------------------------------------------
if ($Version -notmatch '^(20[0-9]{2})\.(1[0-2]|[1-9])\.([0-9]{1,3})$') {
    Fail "-Version must be a CalVer release tag like 2026.11.1, got '$Version'"
}
$ProductVersion = '{0}.{1}.{2}' -f ([int]$Matches[1] - 2000), $Matches[2], $Matches[3]

$pins = Read-Pins (Join-Path $InstallerDir 'pins.toml')
$python = Get-Pin $pins 'python'
$winsw = Get-Pin $pins 'winsw'
if ($python['version'] -notmatch '^3\.12\.[0-9]+$') { Fail "the agent needs CPython 3.12, pins.toml has $($python['version'])" }

if (-not [IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $RepoRoot $OutDir }
$downloads = Join-Path $OutDir 'downloads'
$stage = Join-Path $OutDir 'stage'
$work = Join-Path $OutDir 'work'
foreach ($dir in $stage, $work) {
    if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path $downloads, $stage, $work | Out-Null

# --- 2. pinned downloads --------------------------------------------------------------------
$pythonZip = Get-Download $python $downloads
$winswExe = Get-Download $winsw $downloads

# --- 3. stage -------------------------------------------------------------------------------
$pythonDir = Join-Path $stage 'python'
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonDir
$parts = $python['version'].Split('.')
$pth = Join-Path $pythonDir ("python{0}{1}._pth" -f $parts[0], $parts[1])
if (-not (Test-Path -LiteralPath $pth)) { Fail "the embeddable runtime has no $(Split-Path -Leaf $pth)" }
$stdlibZip = "python{0}{1}.zip" -f $parts[0], $parts[1]
# The ._pth file is the runtime's whole sys.path (no site, no PYTHONPATH, no current directory).
[IO.File]::WriteAllText($pth, "$stdlibZip`r`n.`r`nLib\site-packages`r`n", (New-Object Text.UTF8Encoding($false)))
$sitePackages = Join-Path $pythonDir 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

Push-Location $RepoRoot
try {
    $wheelDir = Join-Path $work 'wheel'
    Invoke-Checked 'uv' @('build', '--package', 'sos-edge-agent', '--wheel', '--out-dir', $wheelDir)
    $wheel = @(Get-ChildItem -LiteralPath $wheelDir -Filter 'sos_edge_agent-*.whl')
    if ($wheel.Count -ne 1) { Fail "expected one agent wheel in $wheelDir, found $($wheel.Count)" }
    $requirements = Join-Path $work 'requirements.txt'
    Invoke-Checked 'uv' @('export', '--package', 'sos-edge-agent', '--frozen', '--no-default-groups',
        '--no-emit-project', '--format', 'requirements-txt', '--output-file', $requirements)
    $target = @('--target', $sitePackages, '--python-platform', 'x86_64-pc-windows-msvc',
        '--python-version', ($parts[0] + '.' + $parts[1]), '--only-binary', ':all:', '--no-cache')
    Invoke-Checked 'uv' (@('pip', 'install') + $target + @('--require-hashes', '--requirement', $requirements))
    Invoke-Checked 'uv' (@('pip', 'install') + $target + @('--no-deps', $wheel[0].FullName))
} finally {
    Pop-Location
}
# The service runs `python -m`; console-script launchers are not needed.
foreach ($launchers in 'bin', 'Scripts') {
    $bin = Join-Path $sitePackages $launchers
    if (Test-Path -LiteralPath $bin) { Remove-Item -LiteralPath $bin -Recurse -Force }
}

Copy-Item -LiteralPath $winswExe -Destination (Join-Path $stage 'sos-tally-agent.exe')
Copy-Item -LiteralPath (Join-Path $AgentDir 'windows\sos-tally-agent.xml') -Destination (Join-Path $stage 'sos-tally-agent.xml')
Copy-Item -LiteralPath (Join-Path $InstallerDir 'tally-agent.cmd') -Destination (Join-Path $stage 'tally-agent.cmd')

# Smoke test of the staged runtime: the agent imports with only the bundled files.
Invoke-Checked (Join-Path $pythonDir 'python.exe') @('-I', '-c', 'import sos_edge_agent.cli, defusedxml; print("agent", sos_edge_agent.__version__)')

# --- 4. payload.wxs and the MSI ---------------------------------------------------------------
$sha1 = [Security.Cryptography.SHA1]::Create()
function Get-WixId([string]$Prefix, [string]$Text) {
    $bytes = $sha1.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text.ToLowerInvariant()))
    return $Prefix + (($bytes | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 32)
}
$xml = New-Object Text.StringBuilder
[void]$xml.AppendLine('<?xml version="1.0" encoding="utf-8"?>')
[void]$xml.AppendLine('<!-- Generated by build.ps1 (D69): the staged python\ folder. Do not edit. -->')
[void]$xml.AppendLine('<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"><Fragment>')
[void]$xml.AppendLine('<ComponentGroup Id="PythonRuntime" Directory="PYTHONDIR">')
$prefixLength = $pythonDir.TrimEnd('\').Length + 1
foreach ($file in Get-ChildItem -LiteralPath $pythonDir -Recurse -File | Sort-Object FullName) {
    $relative = $file.FullName.Substring($prefixLength)
    $subdir = Split-Path -Parent $relative
    $sub = ''
    if ($subdir) { $sub = ' Subdirectory="' + [Security.SecurityElement]::Escape($subdir) + '"' }
    $source = [Security.SecurityElement]::Escape('!(bindpath.stage)\python\' + $relative)
    [void]$xml.AppendLine(('<Component Id="{0}" Bitness="always64"{1}><File Id="{2}" Source="{3}" KeyPath="yes" /></Component>' -f `
        (Get-WixId 'c' $relative), $sub, (Get-WixId 'f' $relative), $source))
}
[void]$xml.AppendLine('</ComponentGroup></Fragment></Wix>')
$payload = Join-Path $OutDir 'payload.wxs'
[IO.File]::WriteAllText($payload, $xml.ToString(), (New-Object Text.UTF8Encoding($false)))

if ($StageOnly) {
    Write-Host "Staged in $stage (ProductVersion $ProductVersion); -StageOnly, so no MSI."
    return
}

$msi = Join-Path $OutDir ("schoolos-tally-agent-{0}-x64.msi" -f $Version)
Invoke-Checked 'wix' @('build', '-arch', 'x64', '-ext', 'WixToolset.Util.wixext',
    '-d', "ProductVersion=$ProductVersion", '-bindpath', "stage=$stage",
    '-o', $msi, (Join-Path $InstallerDir 'SchoolOSTallyAgent.wxs'), $payload)
$hash = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Built $msi (ProductVersion $ProductVersion, SHA-256 $hash)"
