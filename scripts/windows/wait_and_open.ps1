# Opens the dashboard in the browser once the screener answers.
# Gives up after about 10 minutes. Called by "Start Screener (Windows).bat".
param([string]$Url = 'http://127.0.0.1:8000')

for ($i = 0; $i -lt 300; $i++) {
    try {
        Invoke-WebRequest -Uri "$Url/api/status" -UseBasicParsing -TimeoutSec 2 | Out-Null
        Start-Process $Url
        exit 0
    } catch {
        Start-Sleep -Seconds 2
    }
}
exit 0
