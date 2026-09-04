[CmdletBinding()]
param([string]$Prefix = 'AI-Radar')
# 兼容旧版单任务名
Unregister-ScheduledTask -TaskName $Prefix -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "$Prefix-Eval" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "$Prefix-Send" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "已卸载定时任务（$Prefix / $Prefix-Eval / $Prefix-Send）。" -ForegroundColor Green
