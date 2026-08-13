param(
    [string]$LocalAppDataRoot = $env:LOCALAPPDATA,
    [string]$HermesHomeOverride = "",
    [string]$HermesCommandOverride = "",
    [string]$OutputPath = "",
    [switch]$RequireClosed
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Write-HolDetection(
    [string]$Code, [string]$HermesRoot = "", [string]$Command = "",
    [string]$Desktop = "", [string]$Version = ""
) {
    $lines = [string[]]@(
        ("code=" + $Code),
        ("hermes_home=" + $HermesRoot),
        ("hermes_command=" + $Command),
        ("hermes_desktop=" + $Desktop),
        ("version=" + $Version)
    )
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        $lines | Write-Output
        return
    }
    $target = [IO.Path]::GetFullPath($OutputPath)
    $parent = [IO.Path]::GetDirectoryName($target)
    if ([string]::IsNullOrWhiteSpace($parent) -or -not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Detection output path is unavailable"
    }
    [IO.File]::WriteAllLines($target, $lines, [Text.UTF8Encoding]::new($false))
}

function Resolve-HolCommand([string]$HermesRoot, [string]$ExplicitCommand) {
    if (-not [string]::IsNullOrWhiteSpace($ExplicitCommand)) {
        return [IO.Path]::GetFullPath($ExplicitCommand)
    }
    return Join-Path $HermesRoot "hermes-agent\venv\Scripts\hermes.exe"
}

function Add-HolCandidate($List, [string]$HermesRoot, [string]$Command) {
    if ([string]::IsNullOrWhiteSpace($HermesRoot)) { return }
    try { $resolvedRoot = [IO.Path]::GetFullPath($HermesRoot).TrimEnd("\") }
    catch { return }
    if (@($List | Where-Object {
        $_.HermesRoot.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)
    }).Count) { return }
    $List.Add([PSCustomObject]@{
        HermesRoot = $resolvedRoot
        Command = Resolve-HolCommand $resolvedRoot $Command
    })
}

function Test-HolHermesRunning([string]$HermesRoot) {
    $prefix = $HermesRoot.TrimEnd("\") + "\"
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        if ($process.Id -eq $PID) { continue }
        try { $path = $process.Path } catch { continue }
        if (-not [string]::IsNullOrWhiteSpace($path) -and
            $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Test-HolHermesVersion([string]$Version) {
    if ($Version -notmatch "^0\.(18|20)\.(\d+)$") { return $false }
    try {
        $minor = [int]$Matches[1]
        $patch = [int]$Matches[2]
    } catch { return $false }
    return (($minor -eq 18 -and $patch -ge 2) -or $minor -eq 20)
}

$candidates = [Collections.Generic.List[object]]::new()
Add-HolCandidate $candidates $HermesHomeOverride $HermesCommandOverride
Add-HolCandidate $candidates $env:HERMES_HOME ""
if (-not [string]::IsNullOrWhiteSpace($LocalAppDataRoot)) {
    Add-HolCandidate $candidates (Join-Path $LocalAppDataRoot "hermes") ""
}
try {
    $pathCommand = (Get-Command hermes -CommandType Application -ErrorAction Stop).Source
    $commandItem = Get-Item -LiteralPath $pathCommand -Force
    $inferredHome = $commandItem.Directory.Parent.Parent.Parent.FullName
    Add-HolCandidate $candidates $inferredHome $pathCommand
} catch { }

$foundRuntime = $false
foreach ($candidate in $candidates) {
    $hermesRoot = [string]$candidate.HermesRoot
    $command = [string]$candidate.Command
    if (-not (Test-Path -LiteralPath $hermesRoot -PathType Container) -or
        -not (Test-Path -LiteralPath $command -PathType Leaf)) { continue }
    $foundRuntime = $true
    $previousHermesRoot = $env:HERMES_HOME
    try {
        $env:HERMES_HOME = $hermesRoot
        $versionOutput = & $command --version 2>&1
        if ($LASTEXITCODE -ne 0) { continue }
    } catch { continue }
    finally { $env:HERMES_HOME = $previousHermesRoot }
    $versionText = $versionOutput -join " "
    if ($versionText -notmatch "(?:^|[^0-9])(0\.(?:18|20)\.\d+)(?![0-9.])") { continue }
    $version = $Matches[1]
    if (-not (Test-HolHermesVersion $version)) { continue }
    $desktop = Join-Path $hermesRoot "hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe"
    if (-not (Test-Path -LiteralPath $desktop -PathType Leaf)) { $desktop = "" }
    if ($RequireClosed -and (Test-HolHermesRunning $hermesRoot)) {
        Write-HolDetection "HERMES_RUNNING" $hermesRoot $command $desktop $version
        exit 2
    }
    Write-HolDetection "HERMES_READY" $hermesRoot $command $desktop $version
    exit 0
}

Write-HolDetection $(if ($foundRuntime) { "HERMES_INCOMPATIBLE" } else { "HERMES_NOT_FOUND" })
exit 2
