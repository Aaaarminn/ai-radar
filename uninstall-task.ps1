[CmdletBinding()]
param([string]$TaskName = 'AI-Radar')
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "已卸载定时任务 '$TaskName'。" -ForegroundColor Green
