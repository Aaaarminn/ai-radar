<#
.SYNOPSIS
    AI-Radar 两阶段定时任务安装：
      AI-Radar-Eval  每 2 小时的 :45（0:45, 2:45, ...）扫描 + GLM 影响力评估入池
      AI-Radar-Send  每 2 小时的整点（1:00, 3:00, ...）发送窗口：池内有料即发
    （评估与推送解耦：重活在 :45 慢慢跑，整点轻量发送）
#>
[CmdletBinding()]
param([string]$Prefix = 'AI-Radar')
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Host '未找到 python，请先安装。' -ForegroundColor Red; exit 1 }

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable

function Next-Slot([int]$minuteOffset) {
    $now = Get-Date
    $slotHour = [Math]::Floor($now.Hour / 2.0) * 2 + $minuteOffset
    $t = $now.Date.AddHours($slotHour)
    if ($t -le $now) { $t = $t.AddHours(2) }
    return $t
}

# ---- 评估任务（:45） ----
$a1 = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Set-Location '$dir'; & '$py' radar.py --eval-only`""
$t1 = New-ScheduledTaskTrigger -Once -At (Next-Slot 0).AddMinutes(45) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
$t1b = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
Register-ScheduledTask -TaskName "$Prefix-Eval" -Action $a1 -Trigger @($t1, $t1b) -Settings $settings `
  -Description 'AI雷达评估：每2小时:45扫描+影响力评估入池' -Force | Out-Null

# ---- 发送任务（整点） ----
$a2 = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"Set-Location '$dir'; & '$py' radar.py --send-only`""
$t2 = New-ScheduledTaskTrigger -Once -At (Next-Slot 1) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "$Prefix-Send" -Action $a2 -Trigger $t2 -Settings $settings `
  -Description 'AI雷达发送：每2小时整点窗口，池内有料即发' -Force | Out-Null

Write-Host "已安装两阶段任务：" -ForegroundColor Green
Write-Host ("  $Prefix-Eval  下次: " + (Get-ScheduledTaskInfo -TaskName "$Prefix-Eval").NextRunTime + "（每 2 小时 :45）")
Write-Host ("  $Prefix-Send  下次: " + (Get-ScheduledTaskInfo -TaskName "$Prefix-Send").NextRunTime + "（每 2 小时整点）")
Write-Host '卸载：powershell -File uninstall-task.ps1'
