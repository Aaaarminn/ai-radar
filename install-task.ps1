<#
.SYNOPSIS
    AI-Radar 本地定时任务安装：每 :00 / :30 扫描（攒批发送，见 README）。
#>
[CmdletBinding()]
param([string]$TaskName = 'AI-Radar')
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Host '未找到 python，请先安装。' -ForegroundColor Red; exit 1 }

$next = Get-Date
$minute = if ($next.Minute -ge 30) { 30 } else { 0 }
$next = $next.Date.AddHours($next.Hour).AddMinutes($minute)
if ($next -le (Get-Date)) { $next = $next.AddMinutes(30) }

$action   = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Set-Location '$dir'; & '$py' radar.py`""
$t1 = New-ScheduledTaskTrigger -Once -At $next -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$t2 = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($t1, $t2) -Settings $settings `
  -Description 'AI动态雷达：每半小时扫描，攒批发送' -Force | Out-Null
Write-Host "已安装：下次运行 $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)，之后每 :00 / :30" -ForegroundColor Green
Write-Host '卸载：powershell -File uninstall-task.ps1'
