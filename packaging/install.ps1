param(
    [string]$PackageRoot = $PSScriptRoot,
    [string]$LocalAppDataRoot = $env:LOCALAPPDATA,
    [string]$HermesHome = "",
    [string]$HermesCommand = "hermes",
    [switch]$ConfirmHermesClosed,
    [switch]$EnableHermesPlugin
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"
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
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Write-Output '{"ok":false,"code":"INSTALL_VALIDATION_FAILED","message":"Package validation failed."}'
    exit 2
}
function Stop-HolInstall([int]$ExitCode, [string]$Code, [string]$Message) {
    Write-HolResult ($ExitCode -eq 0) $Code $Message
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
$holonSkillRoot = Join-Path $skillsParent "holon"
$lendingSkillRoot = Join-Path $skillsParent "holon-lending"
$token = [Guid]::NewGuid().ToString("N")
$stageApp = Join-Path $appParent (".app-stage-" + $token)
$stagePlugin = Join-Path $pluginParent (".plugin-stage-" + $token)
$stageSkills = Join-Path $skillsParent (".skills-stage-" + $token)
$stageData = Join-Path $appParent (".data-stage-" + $token)
$backupApp = Join-Path $appParent (".app-backup-" + $token)
$backupPlugin = Join-Path $pluginParent (".plugin-backup-" + $token)
$backupHolonSkill = Join-Path $skillsParent (".holon-skill-backup-" + $token)
$backupLendingSkill = Join-Path $skillsParent (".lending-skill-backup-" + $token)
$swappedApp = $false; $swappedPlugin = $false
$swappedHolonSkill = $false; $swappedLendingSkill = $false
$committed = $false
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
try {
    $manifest = Read-HolManifest $PackageRoot
    Test-HolPackage $PackageRoot $manifest
    if ($EnableHermesPlugin) {
        $versionOutput = & $HermesCommand --version 2>&1
        if ($LASTEXITCODE -ne 0 -or (($versionOutput -join " ") -notmatch "(?:^|[^0-9])0\.18\.(\d+)(?:[^0-9]|$)") -or
            [int]$Matches[1] -lt 2) {
            throw [System.ArgumentException]::new("Hermes compatibility failed")
        }
    }
    $null = New-Item -ItemType Directory -Path $appParent -Force
    $null = New-Item -ItemType Directory -Path $pluginParent -Force
    $null = New-Item -ItemType Directory -Path $skillsParent -Force
    Copy-HolComponent $manifest $PackageRoot "payload/app/" $stageApp
    Copy-Item -LiteralPath (Join-Path $PackageRoot "release-manifest.json") `
        -Destination (Join-Path $stageApp "release-manifest.json") -Force
    Copy-HolComponent $manifest $PackageRoot "payload/plugin/" $stagePlugin
    Copy-HolComponent $manifest $PackageRoot "payload/skills/crypto/" $stageSkills
    Copy-HolComponent $manifest $PackageRoot "payload/initial-data/" $stageData
    Test-HolComponent $manifest "payload/app/" $stageApp
    Test-HolComponent $manifest "payload/plugin/" $stagePlugin
    Test-HolComponent $manifest "payload/skills/crypto/" $stageSkills
    Test-HolComponent $manifest "payload/initial-data/" $stageData
    if (-not (Test-Path -LiteralPath $dataRoot)) {
        Move-Item -LiteralPath $stageData -Destination $dataRoot
    }
    if (Test-Path -LiteralPath $appRoot) { Move-Item -LiteralPath $appRoot -Destination $backupApp }
    if (Test-Path -LiteralPath $pluginRoot) { Move-Item -LiteralPath $pluginRoot -Destination $backupPlugin }
    if (Test-Path -LiteralPath $holonSkillRoot) {
        Move-Item -LiteralPath $holonSkillRoot -Destination $backupHolonSkill }
    if (Test-Path -LiteralPath $lendingSkillRoot) {
        Move-Item -LiteralPath $lendingSkillRoot -Destination $backupLendingSkill }
    Move-Item -LiteralPath $stageApp -Destination $appRoot
    $swappedApp = $true
    Move-Item -LiteralPath $stagePlugin -Destination $pluginRoot
    $swappedPlugin = $true
    Move-Item -LiteralPath (Join-Path $stageSkills "holon") -Destination $holonSkillRoot
    $swappedHolonSkill = $true
    Move-Item -LiteralPath (Join-Path $stageSkills "holon-lending") -Destination $lendingSkillRoot
    $swappedLendingSkill = $true
    if ($EnableHermesPlugin) {
        $oldHome = $env:HERMES_HOME
        try {
            $env:HERMES_HOME = $HermesHome
            $output = & $HermesCommand plugins enable holon --no-allow-tool-override 2>&1
            if ($LASTEXITCODE -ne 0) { throw [System.ArgumentException]::new("Hermes enable failed") }
        } finally { $env:HERMES_HOME = $oldHome }
    }
    $committed = $true
    foreach ($backup in @(
        $backupApp, $backupPlugin, $backupHolonSkill, $backupLendingSkill
    )) {
        try {
            if (Test-Path -LiteralPath $backup) {
                Remove-Item -LiteralPath $backup -Recurse -Force }
        } catch { continue }
    }
    Stop-HolInstall 0 "INSTALL_OK" "Holon base package installed."
} catch [System.ArgumentException] {
    if (-not $committed) {
        Restore-HolPrevious $appRoot $backupApp $swappedApp
        Restore-HolPrevious $pluginRoot $backupPlugin $swappedPlugin
        Restore-HolPrevious $holonSkillRoot $backupHolonSkill $swappedHolonSkill
        Restore-HolPrevious $lendingSkillRoot $backupLendingSkill $swappedLendingSkill
    }
    Stop-HolInstall 2 "INSTALL_VALIDATION_FAILED" "Package validation or approval failed."
} catch {
    if (-not $committed) {
        Restore-HolPrevious $appRoot $backupApp $swappedApp
        Restore-HolPrevious $pluginRoot $backupPlugin $swappedPlugin
        Restore-HolPrevious $holonSkillRoot $backupHolonSkill $swappedHolonSkill
        Restore-HolPrevious $lendingSkillRoot $backupLendingSkill $swappedLendingSkill
    }
    Stop-HolInstall 3 "INSTALL_FILESYSTEM_FAILED" "Installation could not be completed."
} finally {
    foreach ($path in @($stageApp, $stagePlugin, $stageSkills, $stageData)) {
        try {
            if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
        } catch { continue }
    }
}
