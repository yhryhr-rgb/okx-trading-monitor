param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._@-]*$')]
    [string]$SshTarget
)
$ErrorActionPreference = 'Stop'
$port = 18766
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    throw "Local port $port is already in use."
}
$ssh = (Get-Command ssh.exe -CommandType Application | Select-Object -First 1).Source
$tunnel = Start-Process -FilePath $ssh -NoNewWindow -PassThru -ArgumentList @(
    '-N', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ForwardAgent=no', '-o', 'ServerAliveInterval=30',
    '-L', "127.0.0.1:${port}:127.0.0.1:8766", $SshTarget
)
try {
    while (-not $tunnel.HasExited) {
        if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                Where-Object OwningProcess -eq $tunnel.Id) {
            Start-Process "http://127.0.0.1:$port/"
            break
        }
        Start-Sleep -Milliseconds 500
    }
    $tunnel.WaitForExit()
    exit $tunnel.ExitCode
} finally {
    if (-not $tunnel.HasExited) { $tunnel.Kill() }
}
