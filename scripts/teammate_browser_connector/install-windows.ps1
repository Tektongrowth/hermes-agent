[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$VpsHost,

    [ValidateNotNullOrEmpty()]
    [string]$VpsUser = "hermes-browser",

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$RemotePort,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$LocalPort,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SshKeyPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$KnownHostsPath,

    [string]$BrowserPath,
    [string]$ProfileDir,
    [switch]$AttachExisting,
    [switch]$StartNow,
    [switch]$DisableAutostart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$TaskName = "HermesBrowserConnector"
$InstallDir = Join-Path $env:LOCALAPPDATA "HermesBrowserConnector"
$WrapperPath = Join-Path $InstallDir "start-connector.ps1"
$LogPath = Join-Path $InstallDir "connector.log"
$SshDebugLogPath = Join-Path $InstallDir "ssh.log"

function Resolve-RequiredFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description does not exist or is not a file: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -eq 0) {
        throw "$Description is empty: $Path"
    }
    return $item.FullName
}

function ConvertTo-PowerShellLiteral {
    param([AllowEmptyString()][string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Find-ChromiumBrowser {
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe")
        $candidates += (Join-Path $env:ProgramFiles "BraveSoftware\Brave-Browser\Application\brave.exe")
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "BraveSoftware\Brave-Browser\Application\brave.exe")
    }
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
        $candidates += (Join-Path $env:LOCALAPPDATA "BraveSoftware\Brave-Browser\Application\brave.exe")
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Get-Item -LiteralPath $candidate).FullName
        }
    }
    throw "Chrome or Brave was not found. Pass -BrowserPath or use -AttachExisting."
}

if ($VpsHost -notmatch '^[A-Za-z0-9._:-]+$') {
    throw "VpsHost contains unsupported characters. Use a hostname or IP address, without an ssh:// prefix."
}
if ($VpsUser -notmatch '^[A-Za-z0-9._-]+$') {
    throw "VpsUser contains unsupported characters."
}
if ($RemotePort -eq $LocalPort -and $VpsHost -in @("127.0.0.1", "localhost", "::1")) {
    throw "A local VPS host with identical local and remote ports would create an ambiguous forwarding loop."
}

$SshKeyPath = Resolve-RequiredFile -Path $SshKeyPath -Description "SSH private key"
$KnownHostsPath = Resolve-RequiredFile -Path $KnownHostsPath -Description "Strict known-hosts file"
$sshCommand = Get-Command ssh.exe -ErrorAction SilentlyContinue
if (-not $sshCommand) {
    throw "OpenSSH client ssh.exe was not found. Install the Windows OpenSSH Client capability."
}
$SshExePath = $sshCommand.Source

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $state = (Get-ScheduledTask -TaskName $TaskName).State
        if ($state -ne "Running") {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if ((Get-ScheduledTask -TaskName $TaskName).State -eq "Running") {
        throw "The existing connector task did not stop. Its wrapper and tunnel were not replaced."
    }
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

if ($AttachExisting) {
    $BrowserPath = ""
    $ProfileDir = ""
} else {
    if ([string]::IsNullOrWhiteSpace($BrowserPath)) {
        $BrowserPath = Find-ChromiumBrowser
    } else {
        $BrowserPath = Resolve-RequiredFile -Path $BrowserPath -Description "Browser executable"
    }

    if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
        $ProfileDir = Join-Path $InstallDir "profile"
    } else {
        $ProfileDir = [System.IO.Path]::GetFullPath($ProfileDir)
    }
    $profileSentinel = Join-Path $ProfileDir ".hermes-browser-connector-profile"
    if (Test-Path -LiteralPath $ProfileDir -PathType Container) {
        $existingProfileItems = Get-ChildItem -LiteralPath $ProfileDir -Force
        if ($existingProfileItems -and -not (Test-Path -LiteralPath $profileSentinel -PathType Leaf)) {
            throw "ProfileDir already contains data and is not owned by this connector: $ProfileDir"
        }
    }
    New-Item -ItemType Directory -Path $ProfileDir -Force | Out-Null
    Set-Content -LiteralPath $profileSentinel -Value "Hermes browser connector profile" -Encoding ASCII
}

$template = @'
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$vpsHost = {{VPS_HOST}}
$vpsUser = {{VPS_USER}}
$remotePort = {{REMOTE_PORT}}
$localPort = {{LOCAL_PORT}}
$sshKeyPath = {{SSH_KEY_PATH}}
$knownHostsPath = {{KNOWN_HOSTS_PATH}}
$sshPath = {{SSH_PATH}}
$browserPath = {{BROWSER_PATH}}
$profileDir = {{PROFILE_DIR}}
$attachExisting = {{ATTACH_EXISTING}}
$logPath = {{LOG_PATH}}
$sshDebugLogPath = {{SSH_DEBUG_LOG_PATH}}

function Write-ConnectorLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    Add-Content -LiteralPath $logPath -Value "$timestamp $Message"
}

function Test-CdpHealth {
    param([int]$Port)
    $response = $null
    $reader = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port/json/version")
        $request.Method = "GET"
        $request.Proxy = $null
        $request.Timeout = 3000
        $response = $request.GetResponse()
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $payload = $reader.ReadToEnd() | ConvertFrom-Json
        $webSocketUrl = [string]$payload.webSocketDebuggerUrl
        return $webSocketUrl -match '^wss?://'
    } catch {
        return $false
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
    }
}

function Test-LoopbackOnlyListener {
    param([int]$Port)
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        return $false
    }
    if ($listeners.Count -eq 0) {
        return $false
    }
    foreach ($listener in $listeners) {
        if ([string]$listener.LocalAddress -notin @('127.0.0.1', '::1')) {
            return $false
        }
    }
    return $true
}

try {
    if (-not (Test-Path -LiteralPath $sshKeyPath -PathType Leaf)) {
        throw "SSH private key is missing."
    }
    if (-not (Test-Path -LiteralPath $knownHostsPath -PathType Leaf)) {
        throw "Strict known-hosts file is missing."
    }

    if (-not $attachExisting -and (Test-CdpHealth -Port $localPort)) {
        throw "Dedicated connector port is already serving CDP. Use AttachExisting explicitly or choose a free local port."
    }
    if (-not $attachExisting) {
        if (-not (Test-Path -LiteralPath $browserPath -PathType Leaf)) {
            throw "Configured browser executable is missing."
        }
        New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
        $quotedProfileDir = '"' + $profileDir + '"'
        $browserArguments = @(
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=$localPort",
            "--user-data-dir=$quotedProfileDir",
            "--no-first-run",
            "--no-default-browser-check"
        )
        Start-Process -FilePath $browserPath -ArgumentList $browserArguments | Out-Null
    }

    $healthy = $false
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-CdpHealth -Port $localPort) {
            $healthy = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) {
        throw "Local CDP health check failed on loopback port $localPort. SSH was not started."
    }
    if (-not (Test-LoopbackOnlyListener -Port $localPort)) {
        throw "Local CDP listener is not loopback-only. SSH was not started."
    }

    Write-ConnectorLog "Local CDP health check passed. Starting reverse tunnel."
    $sshArguments = @(
        "-N",
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1",
        "-o", "RequestTTY=no",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "UserKnownHostsFile=$knownHostsPath",
        "-o", "IdentitiesOnly=yes",
        "-E", $sshDebugLogPath,
        "-v",
        "-i", $sshKeyPath,
        "-R", "127.0.0.1:${remotePort}:127.0.0.1:${localPort}",
        "$vpsUser@$vpsHost"
    )

    & $sshPath @sshArguments 2>&1 | ForEach-Object {
        Write-ConnectorLog ([string]$_)
    }
    $sshExitCode = $LASTEXITCODE
    Write-ConnectorLog "SSH exited with code $sshExitCode. A port conflict or authentication failure must be corrected before retrying."
    exit $sshExitCode
} catch {
    Write-ConnectorLog ("Connector failed: " + $_.Exception.Message)
    exit 1
}
'@

$replacements = @{
    "{{VPS_HOST}}" = ConvertTo-PowerShellLiteral $VpsHost
    "{{VPS_USER}}" = ConvertTo-PowerShellLiteral $VpsUser
    "{{REMOTE_PORT}}" = [string]$RemotePort
    "{{LOCAL_PORT}}" = [string]$LocalPort
    "{{SSH_KEY_PATH}}" = ConvertTo-PowerShellLiteral $SshKeyPath
    "{{KNOWN_HOSTS_PATH}}" = ConvertTo-PowerShellLiteral $KnownHostsPath
    "{{SSH_PATH}}" = ConvertTo-PowerShellLiteral $SshExePath
    "{{BROWSER_PATH}}" = ConvertTo-PowerShellLiteral $BrowserPath
    "{{PROFILE_DIR}}" = ConvertTo-PowerShellLiteral $ProfileDir
    "{{ATTACH_EXISTING}}" = if ($AttachExisting) { '$true' } else { '$false' }
    "{{LOG_PATH}}" = ConvertTo-PowerShellLiteral $LogPath
    "{{SSH_DEBUG_LOG_PATH}}" = ConvertTo-PowerShellLiteral $SshDebugLogPath
}
foreach ($placeholder in $replacements.Keys) {
    $template = $template.Replace($placeholder, $replacements[$placeholder])
}
Set-Content -LiteralPath $WrapperPath -Value $template -Encoding UTF8

$actionArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $WrapperPath + '"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Hermes teammate browser loopback CDP reverse tunnel" `
    -Force | Out-Null

if ($DisableAutostart) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}

if ($StartNow) {
    if ($DisableAutostart) {
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    Set-Content -LiteralPath $SshDebugLogPath -Value "" -Encoding ASCII
    Start-ScheduledTask -TaskName $TaskName
    $ready = $false
    for ($attempt = 1; $attempt -le 45; $attempt++) {
        Start-Sleep -Seconds 1
        if (
            (Test-Path -LiteralPath $SshDebugLogPath -PathType Leaf) -and
            (Select-String -LiteralPath $SshDebugLogPath -SimpleMatch "remote forward success" -Quiet)
        ) {
            $ready = $true
            break
        }
        if ((Get-ScheduledTask -TaskName $TaskName).State -ne "Running") {
            break
        }
    }
    $task = Get-ScheduledTask -TaskName $TaskName
    if ($DisableAutostart) {
        Disable-ScheduledTask -TaskName $TaskName | Out-Null
    }
    if (-not $ready -or $task.State -ne "Running") {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        }
        if ($DisableAutostart) {
            Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
        }
        throw "Connector did not prove the remote forward. LastTaskResult=$($info.LastTaskResult). Review $LogPath and $SshDebugLogPath."
    }
}

Write-Host "Installed the current-user connector task '$TaskName'."
Write-Host "Wrapper: $WrapperPath"
Write-Host "Log: $LogPath"
Write-Host "SSH diagnostics: $SshDebugLogPath"
if ($DisableAutostart) {
    Write-Host "Autostart is disabled. Enable this task only on the preferred device for remote port $RemotePort."
} else {
    Write-Host "Autostart is enabled for remote loopback port $RemotePort."
}
