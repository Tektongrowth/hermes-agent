[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$RemoveProfile,
    [string]$ProfileDir,
    [switch]$RemoveLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TaskName = "HermesBrowserConnector"
$InstallDir = Join-Path $env:LOCALAPPDATA "HermesBrowserConnector"
$WrapperPath = Join-Path $InstallDir "start-connector.ps1"
$LogPath = Join-Path $InstallDir "connector.log"
$SshDebugLogPath = Join-Path $InstallDir "ssh.log"
$DefaultProfileDir = Join-Path $InstallDir "profile"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    if ($PSCmdlet.ShouldProcess($TaskName, "Stop and unregister scheduled task")) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        for ($attempt = 1; $attempt -le 20; $attempt++) {
            if ((Get-ScheduledTask -TaskName $TaskName).State -ne "Running") {
                break
            }
            Start-Sleep -Milliseconds 250
        }
        if ((Get-ScheduledTask -TaskName $TaskName).State -eq "Running") {
            throw "The connector task did not stop. Files were preserved so the tunnel remains manageable."
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
}

if (Test-Path -LiteralPath $WrapperPath -PathType Leaf) {
    if ($PSCmdlet.ShouldProcess($WrapperPath, "Remove generated connector wrapper")) {
        Remove-Item -LiteralPath $WrapperPath -Force
    }
}

if ($RemoveLogs -and (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
    if ($PSCmdlet.ShouldProcess($LogPath, "Remove connector log")) {
        Remove-Item -LiteralPath $LogPath -Force
    }
}
if ($RemoveLogs -and (Test-Path -LiteralPath $SshDebugLogPath -PathType Leaf)) {
    if ($PSCmdlet.ShouldProcess($SshDebugLogPath, "Remove SSH diagnostic log")) {
        Remove-Item -LiteralPath $SshDebugLogPath -Force
    }
}

if ($RemoveProfile) {
    $profileToRemove = if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
        $DefaultProfileDir
    } else {
        [System.IO.Path]::GetFullPath($ProfileDir)
    }
    $profileToRemove = [System.IO.Path]::GetFullPath($profileToRemove)
    $profileRoot = [System.IO.Path]::GetPathRoot($profileToRemove)
    $userHome = [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\')
    $profilePrefix = $userHome + '\'
    if (
        $profileToRemove -eq $profileRoot -or
        $profileToRemove.TrimEnd('\') -eq $userHome -or
        -not $profileToRemove.StartsWith($profilePrefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to remove a profile outside the current user's home directory: $profileToRemove"
    }
    $profileSentinel = Join-Path $profileToRemove ".hermes-browser-connector-profile"
    if (-not (Test-Path -LiteralPath $profileSentinel -PathType Leaf)) {
        throw "Refusing to remove a profile without the connector ownership sentinel: $profileToRemove"
    }
    if (Test-Path -LiteralPath $profileToRemove -PathType Container) {
        if ($PSCmdlet.ShouldProcess($profileToRemove, "Remove browser profile recursively")) {
            Remove-Item -LiteralPath $profileToRemove -Recurse -Force
        }
    }
}

if (Test-Path -LiteralPath $InstallDir -PathType Container) {
    $remaining = Get-ChildItem -LiteralPath $InstallDir -Force
    if (-not $remaining) {
        Remove-Item -LiteralPath $InstallDir -Force
    }
}

Write-Host "Removed the Hermes browser connector task and generated wrapper."
if (-not $RemoveProfile) {
    Write-Host "Browser profile data was preserved. Use -RemoveProfile to delete it explicitly."
}
Write-Host "SSH keys and known-hosts files were not changed."
