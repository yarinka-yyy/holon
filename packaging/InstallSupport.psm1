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
function Test-HolLayout($File, $Manifest) {
    $path = $File.path; if ($path -eq "payload/app/HolonGuard.exe") { $component = "guard" }
    elseif ($path -eq "payload/app/HolonWallet.exe") { $component = "wallet" }
    elseif ($path -in @(
        "payload/app/module-catalog.json",
        "payload/plugin/module-catalog.json"
    )) { $component = "modules" }
    elseif ($path -eq "payload/app/holon_policy/baseline-policy.json") { $component = "policy" }
    elseif ($path.StartsWith("payload/plugin/holon_contracts/") -or
        $path.StartsWith("payload/plugin/holon_guard_ipc/")) { $component = "contracts" }
    elseif ($path.StartsWith("payload/plugin/holon_modules/")) { $component = "modules" }
    elseif ($path.StartsWith("payload/app/modules/") -or $path.StartsWith("payload/plugin/modules/")) {
        $parts = @($path.Split("/"))
        if ($parts.Count -lt 5 -or $parts[3] -cnotin @($Manifest.module_ids)) {
            throw [System.ArgumentException]::new("Unexpected module path") }
        $component = "modules"
    }
    elseif ($path.StartsWith("payload/plugin/")) { $component = "plugin" }
    elseif ($path.StartsWith("payload/skills/crypto/")) {
        $parts = @($path.Split("/"))
        if ($parts.Count -lt 5 -or $parts[3] -cnotin @($Manifest.skill_ids)) {
            throw [System.ArgumentException]::new("Unexpected skill path") }
        $component = "skills"
    }
    elseif ($path.StartsWith("payload/initial-data/")) { $component = "initial-data" }
    elseif ($path -in @(
        "payload/app/licenses/LICENSE",
        "payload/app/licenses/NOTICE",
        "payload/app/licenses/THIRD_PARTY_LICENSES.txt"
    )) {
        $component = "installer"
    }
    elseif ($path -in @(
        "install.ps1", "uninstall.ps1", "detect-hermes.ps1", "InstallSupport.psm1", "INSTALL.md"
    )) {
        $component = "installer"
    } else { throw [System.ArgumentException]::new("Unexpected package path") }
    $critical = $path.StartsWith("payload/app/") -or $path.StartsWith("payload/plugin/")
    if ($path.StartsWith("payload/skills/crypto/")) {
        $parts = @($path.Split("/"))
        $critical = $parts.Count -ge 5 -and $parts[3] -cnotin @(
            "holon", "holon-earn", "holon-lending"
        )
    }
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
        "manifest_version", "package_version", "component_versions", "hermes_compatibility",
        "composition_id", "core_api_version", "module_catalog_sha256", "module_ids",
        "skill_ids", "files"
    ))) { throw [System.ArgumentException]::new("Invalid manifest fields") }
    if ($manifest.manifest_version -cne "3" -or $manifest.package_version -cne "0.2.0a0" -or
        $manifest.hermes_compatibility -cne ">=0.18.2,<0.19.0 || >=0.20.0,<0.21.0") {
        throw [System.ArgumentException]::new("Incompatible package") }
    if ($null -eq $manifest.component_versions -or -not (Test-HolFields `
        $manifest.component_versions @(
            "plugin", "guard", "wallet", "contracts", "policy", "skills", "modules"
        ))) {
        throw [System.ArgumentException]::new("Invalid component versions")
    }
    $versions = @($manifest.component_versions.plugin, $manifest.component_versions.guard,
        $manifest.component_versions.wallet, $manifest.component_versions.contracts,
        $manifest.component_versions.policy, $manifest.component_versions.skills,
        $manifest.component_versions.modules)
    if (($versions -join "|") -cne "0.2.0a0|0.2.0a0|0.2.0a0|1|1|0.2.0a0|1") {
        throw [System.ArgumentException]::new("Incompatible component versions") }
    if ($manifest.composition_id -isnot [string] -or
        $manifest.composition_id -cnotmatch "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$" -or
        $manifest.core_api_version -cne "1" -or
        $manifest.module_catalog_sha256 -cnotmatch "^[0-9a-f]{64}$") {
        throw [System.ArgumentException]::new("Invalid composition metadata") }
    $moduleIds = @($manifest.module_ids)
    if ($moduleIds.Count -gt 32 -or @($moduleIds | Where-Object {
        $_ -isnot [string] -or $_ -cnotmatch "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$"
    }).Count -or ($moduleIds -join "|") -cne (@($moduleIds | Sort-Object -Unique) -join "|")) {
        throw [System.ArgumentException]::new("Invalid module ids") }
    $skillIds = @($manifest.skill_ids)
    if ($skillIds.Count -lt 3 -or "holon" -cnotin $skillIds -or
        "holon-earn" -cnotin $skillIds -or "holon-lending" -cnotin $skillIds -or
        @($skillIds | Where-Object {
            $_ -isnot [string] -or $_ -cnotmatch "^holon(?:-[a-z0-9]+)*$"
        }).Count -or ($skillIds -join "|") -cne (@($skillIds | Sort-Object -Unique) -join "|")) {
        throw [System.ArgumentException]::new("Invalid skill ids") }
    $files = @($manifest.files); if ($files.Count -eq 0 -or $files.Count -gt 4096) {
        throw [System.ArgumentException]::new("Invalid manifest files") }
    $previous = ""; foreach ($file in $files) {
        if ($null -eq $file -or $file -isnot [PSCustomObject]) {
            throw [System.ArgumentException]::new("Invalid manifest entry") }
        if (-not (Test-HolFields $file @("component", "path", "sha256", "critical")) -or
            $file.path -isnot [string] -or $file.component -isnot [string] -or
            $file.sha256 -cnotmatch "^[0-9a-f]{64}$" -or $file.critical -isnot [bool] -or
            $file.component -notin @(
                "installer", "guard", "wallet", "plugin", "contracts", "policy",
                "skills", "initial-data", "modules"
            )) {
            throw [System.ArgumentException]::new("Invalid manifest entry")
        }
        $null = Resolve-HolFile $Root $file.path; Test-HolLayout $file $manifest
        $key = $file.path.ToLowerInvariant()
        if ($previous -and [string]::CompareOrdinal($previous, $key) -ge 0) {
            throw [System.ArgumentException]::new("Non-canonical path") }
        $previous = $key
    }
    foreach ($catalogRelative in @(
        "payload/app/module-catalog.json",
        "payload/plugin/module-catalog.json",
        "payload/plugin/holon_modules/module-catalog.json"
    )) {
        $catalogEntries = @($files | Where-Object { $_.path -ceq $catalogRelative })
        if ($catalogEntries.Count -ne 1 -or
            $catalogEntries[0].sha256 -cne $manifest.module_catalog_sha256) {
            throw [System.ArgumentException]::new("Module catalog declaration mismatch") }
    }
    $catalogPath = Resolve-HolFile $Root "payload/app/module-catalog.json"
    try { $catalog = (Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json) }
    catch { throw [System.ArgumentException]::new("Invalid module catalog") }
    if ($null -eq $catalog -or -not (Test-HolFields $catalog @(
        "catalog_version", "composition_id", "core_api_version", "modules"
    )) -or $catalog.catalog_version -cne "1" -or
        $catalog.composition_id -cne $manifest.composition_id -or
        $catalog.core_api_version -cne "1") {
        throw [System.ArgumentException]::new("Incompatible module catalog") }
    $catalogModules = @($catalog.modules)
    if ($catalogModules.Count -ne $moduleIds.Count) {
        throw [System.ArgumentException]::new("Module catalog id mismatch") }
    $catalogIds = @()
    foreach ($module in $catalogModules) {
        if ($null -eq $module -or -not (Test-HolFields $module @(
            "module_id", "enabled", "manifest_path", "manifest_sha256"
        )) -or $module.enabled -isnot [bool] -or
            $module.module_id -cnotmatch "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$" -or
            $module.manifest_path -cne "modules/$($module.module_id)/module-manifest.json" -or
            $module.manifest_sha256 -cnotmatch "^[0-9a-f]{64}$") {
            throw [System.ArgumentException]::new("Invalid module catalog entry") }
        $catalogIds += $module.module_id
        foreach ($component in @("app", "plugin")) {
            $packageManifestPath = "payload/$component/modules/$($module.module_id)/module-manifest.json"
            $manifestEntries = @($files | Where-Object { $_.path -ceq $packageManifestPath })
            if ($manifestEntries.Count -ne 1 -or
                $manifestEntries[0].sha256 -cne $module.manifest_sha256) {
                throw [System.ArgumentException]::new("Module manifest declaration mismatch") }
        }
    }
    if (($catalogIds -join "|") -cne ($moduleIds -join "|")) {
        throw [System.ArgumentException]::new("Module catalog id mismatch") }
    return $manifest
}
function Read-HolInstalledSkillIds([string]$AppRoot) {
    if (-not (Test-Path -LiteralPath $AppRoot -PathType Container)) { return @() }
    $manifestPath = Join-Path $AppRoot "release-manifest.json"
    try {
        $item = Get-Item -LiteralPath $manifestPath -Force -ErrorAction Stop
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $item.Length -eq 0 -or $item.Length -gt 262144) {
            throw "unsafe"
        }
        $installed = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        throw [System.ArgumentException]::new("Installed ownership manifest is invalid")
    }
    if ($installed.manifest_version -ceq "2") {
        if (-not (Test-HolFields $installed @(
            "manifest_version", "package_version", "component_versions",
            "hermes_compatibility", "files"
        ))) {
            throw [System.ArgumentException]::new("Legacy ownership manifest is invalid")
        }
        return @("holon", "holon-lending")
    }
    if ($installed.manifest_version -cne "3" -or -not (Test-HolFields $installed @(
        "manifest_version", "package_version", "component_versions", "hermes_compatibility",
        "composition_id", "core_api_version", "module_catalog_sha256", "module_ids",
        "skill_ids", "files"
    )) -or $installed.composition_id -cnotmatch "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$" -or
        $installed.core_api_version -cne "1" -or
        $installed.module_catalog_sha256 -cnotmatch "^[0-9a-f]{64}$") {
        throw [System.ArgumentException]::new("Installed ownership manifest is incompatible")
    }
    $ids = @($installed.skill_ids)
    $moduleIds = @($installed.module_ids)
    if ($ids.Count -lt 3 -or "holon" -cnotin $ids -or "holon-earn" -cnotin $ids -or
        "holon-lending" -cnotin $ids -or
        @($ids | Where-Object {
            $_ -isnot [string] -or $_ -cnotmatch "^holon(?:-[a-z0-9]+)*$"
        }).Count -or ($ids -join "|") -cne (@($ids | Sort-Object -Unique) -join "|") -or
        $moduleIds.Count -gt 32 -or @($moduleIds | Where-Object {
            $_ -isnot [string] -or $_ -cnotmatch "^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$"
        }).Count -or ($moduleIds -join "|") -cne (@($moduleIds | Sort-Object -Unique) -join "|")) {
        throw [System.ArgumentException]::new("Installed ownership ids are invalid")
    }
    $catalogPath = Join-Path $AppRoot "module-catalog.json"
    try {
        if ((Get-FileHash -LiteralPath $catalogPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
            $installed.module_catalog_sha256) { throw "digest" }
        $catalog = Get-Content -LiteralPath $catalogPath -Raw | ConvertFrom-Json
    } catch {
        throw [System.ArgumentException]::new("Installed module ownership is invalid")
    }
    if (-not (Test-HolFields $catalog @(
        "catalog_version", "composition_id", "core_api_version", "modules"
    )) -or $catalog.catalog_version -cne "1" -or
        $catalog.composition_id -cne $installed.composition_id -or
        $catalog.core_api_version -cne "1" -or
        (@($catalog.modules | ForEach-Object { $_.module_id }) -join "|") -cne
        ($moduleIds -join "|")) {
        throw [System.ArgumentException]::new("Installed module ownership is incompatible")
    }
    return $ids
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
Export-ModuleMember -Function Read-HolManifest, Read-HolInstalledSkillIds, Test-HolPackage, Copy-HolComponent, Test-HolComponent, Write-HolResult
