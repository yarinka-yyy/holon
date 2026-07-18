Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
function Test-HolFields($Object, [string[]]$Expected) {
    $actual = @($Object.PSObject.Properties.Name | Sort-Object)
    return (($actual -join "|") -ceq (@($Expected | Sort-Object) -join "|"))
}
function Resolve-HolFile([string]$Root, [string]$Relative) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Length -gt 240 -or
        $Relative.Contains("\") -or $Relative.Contains(":") -or $Relative.StartsWith("/") -or
        $Relative -match "[\x00-\x1F]") {
        throw [System.ArgumentException]::new("Unsafe package path") }
    $parts = @($Relative.Split("/"))
    if ($parts.Count -eq 0 -or @($parts | Where-Object { $_ -eq "" -or $_ -eq "." -or $_ -eq ".." }).Count) {
        throw [System.ArgumentException]::new("Unsafe package path") }
    if (@($parts | Where-Object { $_.EndsWith(" ") -or $_.EndsWith(".") }).Count) {
        throw [System.ArgumentException]::new("Unsafe package path") }
    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd("\")
    $candidate = [IO.Path]::GetFullPath([IO.Path]::Combine($rootPath, $Relative.Replace("/", "\")))
    if (-not $candidate.StartsWith($rootPath + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw [System.ArgumentException]::new("Unsafe package path") }
    return $candidate
}
function Test-HolLayout($File) {
    $path = $File.path; if ($path -eq "payload/app/HolonGuard.exe") { $component = "guard" }
    elseif ($path -eq "payload/app/HolonWallet.exe") { $component = "wallet" }
    elseif ($path -eq "payload/app/holon_policy/baseline-policy.json") { $component = "policy" }
    elseif ($path.StartsWith("payload/plugin/holon_contracts/") -or
        $path.StartsWith("payload/plugin/holon_guard_ipc/")) { $component = "contracts" }
    elseif ($path.StartsWith("payload/plugin/")) { $component = "plugin" }
    elseif ($path.StartsWith("payload/initial-data/")) { $component = "initial-data" }
    elseif ($path -in @("install.ps1", "uninstall.ps1", "InstallSupport.psm1", "INSTALL.md")) {
        $component = "installer"
    } else { throw [System.ArgumentException]::new("Unexpected package path") }
    $critical = $path.StartsWith("payload/app/") -or $path.StartsWith("payload/plugin/")
    if ($File.component -cne $component -or $File.critical -ne $critical) {
        throw [System.ArgumentException]::new("Invalid package classification") }
}
function Read-HolManifest([string]$Root) {
    $path = Join-Path $Root "release-manifest.json"; $manifestItem = Get-Item -LiteralPath $path -Force
    if ($manifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw [System.ArgumentException]::new("Unsafe manifest file") }
    $bytes = [IO.File]::ReadAllBytes($path); if ($bytes.Length -eq 0 -or $bytes.Length -gt 262144) {
        throw [System.ArgumentException]::new("Invalid manifest size") }
    try { $manifest = ([Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json) }
    catch { throw [System.ArgumentException]::new("Invalid manifest JSON") }
    if ($null -eq $manifest -or $manifest -isnot [PSCustomObject]) {
        throw [System.ArgumentException]::new("Invalid manifest object") }
    if (-not (Test-HolFields $manifest @(
        "manifest_version", "package_version", "component_versions", "hermes_compatibility", "files"
    ))) { throw [System.ArgumentException]::new("Invalid manifest fields") }
    if ($manifest.manifest_version -cne "1" -or $manifest.package_version -cne "0.1.0a0" -or
        $manifest.hermes_compatibility -cne ">=0.18.2,<0.19.0") {
        throw [System.ArgumentException]::new("Incompatible package") }
    if ($null -eq $manifest.component_versions -or -not (Test-HolFields `
        $manifest.component_versions @("plugin", "guard", "wallet", "contracts", "policy"))) {
        throw [System.ArgumentException]::new("Invalid component versions")
    }
    $versions = @($manifest.component_versions.plugin, $manifest.component_versions.guard,
        $manifest.component_versions.wallet, $manifest.component_versions.contracts,
        $manifest.component_versions.policy)
    if (($versions -join "|") -cne "0.1.0a0|0.1.0a0|0.1.0a0|1|1") {
        throw [System.ArgumentException]::new("Incompatible component versions") }
    $files = @($manifest.files); if ($files.Count -eq 0 -or $files.Count -gt 4096) {
        throw [System.ArgumentException]::new("Invalid manifest files") }
    $previous = ""; foreach ($file in $files) {
        if ($null -eq $file -or $file -isnot [PSCustomObject]) {
            throw [System.ArgumentException]::new("Invalid manifest entry") }
        if (-not (Test-HolFields $file @("component", "path", "sha256", "critical")) -or
            $file.path -isnot [string] -or $file.component -isnot [string] -or
            $file.sha256 -cnotmatch "^[0-9a-f]{64}$" -or $file.critical -isnot [bool] -or
            $file.component -notin @("installer", "guard", "wallet", "plugin", "contracts", "policy", "initial-data")) {
            throw [System.ArgumentException]::new("Invalid manifest entry")
        }
        $null = Resolve-HolFile $Root $file.path; Test-HolLayout $file
        $key = $file.path.ToLowerInvariant()
        if ($previous -and [string]::CompareOrdinal($previous, $key) -ge 0) {
            throw [System.ArgumentException]::new("Non-canonical path") }
        $previous = $key
    }
    return $manifest
}
function Test-HolPackage([string]$Root, $Manifest) {
    foreach ($file in @($Manifest.files)) {
        $path = Resolve-HolFile $Root $file.path; $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw [System.ArgumentException]::new("Unsafe package file")
        }
        $parent = $item.Directory; $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd("\")
        while ($null -ne $parent -and $parent.FullName -cne $rootPath) {
            if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw [System.ArgumentException]::new("Unsafe package link")
            }
            $parent = $parent.Parent
        }
        $digest = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -cne $file.sha256) { throw [System.ArgumentException]::new("Package integrity failed") }
    }
}
function Copy-HolComponent($Manifest, [string]$PackageRoot, [string]$Prefix, [string]$Target) {
    $null = New-Item -ItemType Directory -Path $Target -Force
    foreach ($file in @($Manifest.files | Where-Object { $_.path.StartsWith($Prefix) })) {
        $relative = $file.path.Substring($Prefix.Length); $source = Resolve-HolFile $PackageRoot $file.path
        $destination = Resolve-HolFile $Target $relative
        $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}
function Test-HolComponent($Manifest, [string]$Prefix, [string]$Target) {
    foreach ($file in @($Manifest.files | Where-Object { $_.path.StartsWith($Prefix) })) {
        $path = Resolve-HolFile $Target $file.path.Substring($Prefix.Length)
        $digest = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -cne $file.sha256) { throw [System.IO.IOException]::new("Staging integrity failed") }
    }
}
function Write-HolResult([bool]$Ok, [string]$Code, [string]$Message) {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Write-Output (@{ok=$Ok; code=$Code; message=$Message} | ConvertTo-Json -Compress)
}
Export-ModuleMember -Function Read-HolManifest, Test-HolPackage, Copy-HolComponent, Test-HolComponent, Write-HolResult
