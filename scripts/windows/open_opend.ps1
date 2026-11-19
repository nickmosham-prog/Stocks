# Starts moomoo OpenD if it is installed and not already running.
# Does nothing (and never fails) otherwise. Called by "Start Screener (Windows).bat".

$ErrorActionPreference = 'SilentlyContinue'

$listening = Get-NetTCPConnection -LocalPort 11111 -State Listen
$running = Get-Process | Where-Object { $_.ProcessName -like '*OpenD*' }
if ($listening -or $running) {
    Write-Host 'moomoo OpenD is already running.'
    exit 0
}

# Installed programs leave a Start Menu shortcut; that is the most reliable place to look.
$menus = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
    (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')
) | Where-Object { $_ -and (Test-Path $_) }
$target = Get-ChildItem -Path $menus -Filter '*OpenD*.lnk' -Recurse |
    Where-Object { $_.Name -notmatch 'uninstall' } | Select-Object -First 1

if (-not $target) {
    $folders = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs')) |
        Where-Object { $_ -and (Test-Path $_) }
    $target = Get-ChildItem -Path $folders -Filter '*OpenD*.exe' -Recurse -Depth 3 |
        Where-Object { $_.Name -notmatch 'uninst|setup|install' } | Select-Object -First 1
}

if ($target) {
    Start-Process -FilePath $target.FullName
    Write-Host "Opened moomoo OpenD. Log in there if it asks (don't click Unlock Trade)."
} else {
    Write-Host 'moomoo OpenD not found. If you use it, open it yourself; otherwise the screener uses Yahoo.'
}
exit 0
