param(
    [string]$PackageRoot = $PSScriptRoot,
    [string]$LocalAppDataRoot = $env:LOCALAPPDATA,
    [string]$HermesHome = "",
    [string]$HermesCommand = "hermes",
    [string]$HermesVersion = "",
    [string]$OutputPath = "",
    [switch]$ConfirmHermesClosed,
    [switch]$EnableHermesPlugin
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
function Write-HolInstallResult([bool]$Ok, [string]$Code, [string]$Message) {
    $json = @{ok=$Ok; code=$Code; message=$Message} | ConvertTo-Json -Compress
    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        Write-Output $json
        return
    }
    $target = [IO.Path]::GetFullPath($OutputPath)
    $parent = [IO.Path]::GetDirectoryName($target)
    if ([string]::IsNullOrWhiteSpace($parent) -or
        -not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "Install output path is unavailable"
    }
    [IO.File]::WriteAllText($target, $json, [Text.UTF8Encoding]::new($false))
}
try {
    $bootstrap = (Get-Content -LiteralPath (Join-Path $PackageRoot "release-manifest.json") -Raw | ConvertFrom-Json)
    foreach ($name in @("install.ps1", "InstallSupport.psm1")) {
        $entry = @($bootstrap.files | Where-Object { $_.path -ceq $name })
        $path = Join-Path $PackageRoot $name
        if ($entry.Count -ne 1 -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() `
            -cne $entry[0].sha256) { throw "bootstrap integrity" }
    }
    Import-Module (Join-Path $PackageRoot "InstallSupport.psm1") -Force
} catch {
    Write-HolInstallResult $false "INSTALL_VALIDATION_FAILED" "Package validation failed."
    exit 2
}
function Stop-HolInstall([int]$ExitCode, [string]$Code, [string]$Message) {
    Write-HolInstallResult ($ExitCode -eq 0) $Code $Message
    exit $ExitCode
}
if (-not $ConfirmHermesClosed) {
    Stop-HolInstall 2 "HERMES_CLOSED_CONFIRMATION_REQUIRED" "Confirm Hermes is closed."
}
if ([string]::IsNullOrWhiteSpace($LocalAppDataRoot)) {
    Stop-HolInstall 2 "INSTALL_ROOT_INVALID" "Installation root is unavailable."
}
if ([string]::IsNullOrWhiteSpace($HermesHome)) {
    $HermesHome = Join-Path $LocalAppDataRoot "hermes"
}
$appParent = Join-Path $LocalAppDataRoot "Holon"
$appRoot = Join-Path $appParent "app"
$dataRoot = Join-Path $appParent "data"
$pluginParent = Join-Path $HermesHome "plugins"
$pluginRoot = Join-Path $pluginParent "holon"
$skillsParent = Join-Path (Join-Path $HermesHome "skills") "crypto"
$token = [Guid]::NewGuid().ToString("N")
$stageApp = Join-Path $appParent (".app-stage-" + $token)
$stagePlugin = Join-Path $pluginParent (".plugin-stage-" + $token)
$stageSkills = Join-Path $skillsParent (".skills-stage-" + $token)
$stageData = Join-Path $appParent (".data-stage-" + $token)
$backupApp = Join-Path $appParent (".app-backup-" + $token)
$backupPlugin = Join-Path $pluginParent (".plugin-backup-" + $token)
$swappedApp = $false; $swappedPlugin = $false
$skillBackups = @{}; $swappedSkills = @{}; $managedSkillIds = @()
$committed = $false
$installStep = "manifest"
$installCode = "INSTALL_FILESYSTEM_FAILED"
$installMessage = ""
function Test-HolGuardRunning([string]$AppRootPath) {
    try {
        $expected = [IO.Path]::GetFullPath((Join-Path $AppRootPath "HolonGuard.exe"))
        foreach ($process in @(Get-Process -Name "HolonGuard" -ErrorAction SilentlyContinue)) {
            try {
                if ([IO.Path]::GetFullPath($process.Path).Equals(
                    $expected, [StringComparison]::OrdinalIgnoreCase
                )) { return $true }
            } catch { continue }
        }
    } catch { return $true }
    return $false
}
if (Test-HolGuardRunning $appRoot) {
    Stop-HolInstall 2 "HOLON_RUNTIME_RUNNING" "Close the installed Holon runtime and run Setup again."
}
function Restore-HolPrevious([string]$Current, [string]$Backup, [bool]$Swapped) {
    try {
        if ($Swapped -and (Test-Path -LiteralPath $Current)) {
            Remove-Item -LiteralPath $Current -Recurse -Force
        }
        if ((Test-Path -LiteralPath $Backup) -and -not (Test-Path -LiteralPath $Current)) {
            Move-Item -LiteralPath $Backup -Destination $Current
        }
    } catch { return }
}
function Test-HolHermesVersion([string]$Version) {
    return $Version -cmatch "^0\.18\.(\d+)$" -and [int]$Matches[1] -ge 2
}
function Test-HolHermesMetadataCompatibility([string]$HermesHomePath) {
    try {
        $sitePackages = Join-Path $HermesHomePath "hermes-agent\venv\Lib\site-packages"
        $metadataDirectories = @(Get-ChildItem -LiteralPath $sitePackages -Directory `
            -Filter "hermes_agent-*.dist-info" -Force | Where-Object {
                -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint)
            })
        foreach ($metadataDirectory in $metadataDirectories) {
            $metadataPath = Join-Path $metadataDirectory.FullName "METADATA"
            $versionLine = Get-Content -LiteralPath $metadataPath -ErrorAction Stop | `
                Where-Object { $_ -cmatch "^Version: " } | Select-Object -First 1
            if ($null -ne $versionLine -and $versionLine.StartsWith("Version: ") -and
                (Test-HolHermesVersion $versionLine.Substring("Version: ".Length))) {
                return $true
            }
        }
    } catch { return $false }
    return $false
}
function Test-HolHermesCompatibility(
    [string]$VerifiedVersion, [string]$HermesHomePath, [string]$HermesCommandPath
) {
    if (-not [string]::IsNullOrWhiteSpace($VerifiedVersion)) {
        return Test-HolHermesVersion $VerifiedVersion
    }
    if (Test-HolHermesMetadataCompatibility $HermesHomePath) {
        return $true
    }
    try {
        $versionOutput = & $HermesCommandPath --version 2>&1
        $exitCode = $LASTEXITCODE
        $versionText = $versionOutput -join " "
        if ($exitCode -ne 0 -or $versionText -notmatch "(?:^|[^0-9])0\.18\.(\d+)(?:[^0-9]|$)") {
            return $false
        }
        return Test-HolHermesVersion ("0.18." + $Matches[1])
    } catch { return $false }
}
function Get-HolConfigStamp([string]$HermesHomePath) {
    try {
        $configPath = Join-Path $HermesHomePath "config.yaml"
        if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return "missing" }
        $item = Get-Item -LiteralPath $configPath -Force -ErrorAction Stop
        return "$($item.Length):$($item.LastWriteTimeUtc.Ticks)"
    } catch { return "unavailable" }
}
function Get-HolEnableFailure([object[]]$Output, [int]$ExitCode, [bool]$ConfigChanged) {
    $text = ($Output | ForEach-Object { [string]$_ }) -join " `n"
    if ($text -match '(?i)plugin\s+[''"]?holon[''"]?\s+is not installed or bundled') {
        return @("HERMES_ENABLE_PLUGIN_NOT_FOUND", "Hermes could not find the staged Holon plugin. Previous files were restored.")
    }
    if ($text -match "(?i)(access is denied|permissionerror|unauthorizedaccess|winerror\s*5)") {
        return @("HERMES_ENABLE_ACCESS_DENIED", "Hermes cannot update its local configuration. Previous files were restored.")
    }
    if ($text -match "(?i)(being used by another process|resource busy|locked|sharing violation)") {
        return @("HERMES_ENABLE_CONFIG_LOCKED", "Hermes configuration is still in use. Close Hermes and run Setup again.")
    }
    if ($text -match "(?i)(config\.yaml|yaml|toml|scannererror|parsererror|decodeerror)") {
        return @("HERMES_ENABLE_CONFIG_INVALID", "Hermes could not read its local configuration. Repair Hermes configuration, then run Setup again.")
    }
    if ($text -match "(?im)^\s*(?:[A-Za-z_][A-Za-z0-9_]*\.)?(?<type>[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception))\s*:") {
        return @("HERMES_ENABLE_INTERNAL_ERROR", "Hermes stopped with an internal $($Matches['type']) before enabling Holon. Previous files were restored.")
    }
    if ($ExitCode -lt 0) {
        return @("HERMES_ENABLE_COMMAND_FAILED", "The Hermes command could not complete. Previous files were restored.")
    }
    if (-not $ConfigChanged) {
        return @("HERMES_ENABLE_BEFORE_CONFIG_UPDATE", "Hermes stopped before updating its local configuration. Previous files were restored.")
    }
    return @("HERMES_ENABLE_FAILED", "Hermes rejected enabling Holon (exit code $ExitCode). Previous files were restored.")
}
function Invoke-HolHermesEnable([string]$CommandPath, [string]$HermesHomePath) {
    if ([IO.Path]::GetExtension($CommandPath) -ieq ".exe") {
        $process = $null
        try {
            $start = [Diagnostics.ProcessStartInfo]::new()
            $start.FileName = $CommandPath
            $start.Arguments = "plugins enable holon --no-allow-tool-override"
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $start.RedirectStandardOutput = $true
            $start.RedirectStandardError = $true
            $start.WorkingDirectory = $HermesHomePath
            $start.EnvironmentVariables["HERMES_HOME"] = $HermesHomePath
            $process = [Diagnostics.Process]::new()
            $process.StartInfo = $start
            if (-not $process.Start()) { throw "Hermes process did not start" }
            $stdout = $process.StandardOutput.ReadToEnd()
            $stderr = $process.StandardError.ReadToEnd()
            $process.WaitForExit()
            return [PSCustomObject]@{ ExitCode = $process.ExitCode; Output = @($stdout, $stderr) }
        } catch {
            return [PSCustomObject]@{ ExitCode = -1; Output = @($_.Exception.Message) }
        } finally {
            if ($null -ne $process) { $process.Dispose() }
        }
    }
    $oldHome = $env:HERMES_HOME
    try {
        $env:HERMES_HOME = $HermesHomePath
        $output = @(& $CommandPath plugins enable holon --no-allow-tool-override 2>&1)
        return [PSCustomObject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    } catch {
        return [PSCustomObject]@{ ExitCode = -1; Output = @($_.Exception.Message) }
    } finally { $env:HERMES_HOME = $oldHome }
}
try {
    $installStep = "manifest"
    $manifest = Read-HolManifest $PackageRoot
    $installStep = "package_integrity"
    Test-HolPackage $PackageRoot $manifest
    $installStep = "previous_ownership"
    $previousSkillIds = @(Read-HolInstalledSkillIds $appRoot)
    $managedSkillIds = @(@($manifest.skill_ids) + $previousSkillIds | Sort-Object -Unique)
    foreach ($skillId in $managedSkillIds) {
        $skillBackups[$skillId] = Join-Path $skillsParent (".$skillId-backup-" + $token)
        $swappedSkills[$skillId] = $false
    }
    if ($EnableHermesPlugin) {
        $installStep = "hermes_compatibility"
        if (-not (Test-HolHermesCompatibility $HermesVersion $HermesHome $HermesCommand)) {
            throw [System.ArgumentException]::new("Hermes compatibility failed")
        }
    }
    $installStep = "create_parents"
    $null = New-Item -ItemType Directory -Path $appParent -Force
    $null = New-Item -ItemType Directory -Path $pluginParent -Force
    $null = New-Item -ItemType Directory -Path $skillsParent -Force
    $installStep = "stage_app"
    Copy-HolComponent $manifest $PackageRoot "payload/app/" $stageApp
    Copy-Item -LiteralPath (Join-Path $PackageRoot "release-manifest.json") `
        -Destination (Join-Path $stageApp "release-manifest.json") -Force
    $installStep = "stage_plugin"
    Copy-HolComponent $manifest $PackageRoot "payload/plugin/" $stagePlugin
    $installStep = "stage_skills"
    Copy-HolComponent $manifest $PackageRoot "payload/skills/crypto/" $stageSkills
    $installStep = "stage_initial_data"
    Copy-HolComponent $manifest $PackageRoot "payload/initial-data/" $stageData
    $installStep = "verify_staging"
    Test-HolComponent $manifest "payload/app/" $stageApp
    Test-HolComponent $manifest "payload/plugin/" $stagePlugin
    Test-HolComponent $manifest "payload/skills/crypto/" $stageSkills
    Test-HolComponent $manifest "payload/initial-data/" $stageData
    if (-not (Test-Path -LiteralPath $dataRoot)) {
        $installStep = "initialize_data"
        Move-Item -LiteralPath $stageData -Destination $dataRoot
    }
    $installStep = "backup_previous"
    if (Test-Path -LiteralPath $appRoot) { Move-Item -LiteralPath $appRoot -Destination $backupApp }
    if (Test-Path -LiteralPath $pluginRoot) { Move-Item -LiteralPath $pluginRoot -Destination $backupPlugin }
    foreach ($skillId in $managedSkillIds) {
        $skillRoot = Join-Path $skillsParent $skillId
        if (Test-Path -LiteralPath $skillRoot) {
            Move-Item -LiteralPath $skillRoot -Destination $skillBackups[$skillId] }
    }
    $installStep = "activate_app"
    Move-Item -LiteralPath $stageApp -Destination $appRoot
    $swappedApp = $true
    $installStep = "activate_plugin"
    Move-Item -LiteralPath $stagePlugin -Destination $pluginRoot
    $swappedPlugin = $true
    foreach ($skillId in @($manifest.skill_ids)) {
        $installStep = "activate_skill"
        $skillRoot = Join-Path $skillsParent $skillId
        Move-Item -LiteralPath (Join-Path $stageSkills $skillId) -Destination $skillRoot
        $swappedSkills[$skillId] = $true
    }
    if ($EnableHermesPlugin) {
        $installStep = "enable_plugin"
        $configStampBeforeEnable = Get-HolConfigStamp $HermesHome
        $enableResult = Invoke-HolHermesEnable $HermesCommand $HermesHome
        $enableOutput = @($enableResult.Output)
        $enableExit = [int]$enableResult.ExitCode
        if ($enableExit -ne 0) {
            $configChanged = (Get-HolConfigStamp $HermesHome) -cne $configStampBeforeEnable
            $enableFailure = Get-HolEnableFailure $enableOutput $enableExit $configChanged
            $installCode, $installMessage = $enableFailure[0], $enableFailure[1]
            throw [System.InvalidOperationException]::new("Hermes enable failed")
        }
    }
    $installStep = "commit"
    $committed = $true
    foreach ($backup in @($backupApp, $backupPlugin) + @($skillBackups.Values)) {
        try {
            if (Test-Path -LiteralPath $backup) {
                Remove-Item -LiteralPath $backup -Recurse -Force }
        } catch { continue }
    }
    Stop-HolInstall 0 "INSTALL_OK" "Holon $($manifest.composition_id) package installed."
} catch [System.ArgumentException] {
    if (-not $committed) {
        Restore-HolPrevious $appRoot $backupApp $swappedApp
        Restore-HolPrevious $pluginRoot $backupPlugin $swappedPlugin
        foreach ($skillId in $managedSkillIds) {
            $skillRoot = Join-Path $skillsParent $skillId
            $skillBackup = [string]$skillBackups[$skillId]
            $skillSwapped = [bool]$swappedSkills[$skillId]
            Restore-HolPrevious $skillRoot $skillBackup $skillSwapped
        }
    }
    Stop-HolInstall 2 "INSTALL_VALIDATION_FAILED" "Package validation or approval failed."
} catch {
    if (-not $committed) {
        Restore-HolPrevious $appRoot $backupApp $swappedApp
        Restore-HolPrevious $pluginRoot $backupPlugin $swappedPlugin
        foreach ($skillId in $managedSkillIds) {
            $skillRoot = Join-Path $skillsParent $skillId
            $skillBackup = [string]$skillBackups[$skillId]
            $skillSwapped = [bool]$swappedSkills[$skillId]
            Restore-HolPrevious $skillRoot $skillBackup $skillSwapped
        }
    }
    if ([string]::IsNullOrWhiteSpace($installMessage)) {
        $installMessage = "Installation could not be completed at $installStep."
    }
    Stop-HolInstall 3 $installCode $installMessage
} finally {
    foreach ($path in @($stageApp, $stagePlugin, $stageSkills, $stageData)) {
        try {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        } catch { continue }
    }
}
