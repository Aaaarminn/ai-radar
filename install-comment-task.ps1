<#
.SYNOPSIS
    AI-Radar-Comment 定时任务安装(t6,『看懂再评论』管线):
      每 2 小时的 :15 跑 bili_comment.py run
    运行模式由 config.json 的 comment_mode 控制:
      off(空转) / dryrun(L0,只出草稿不发) / review(L1,入人工审批队列) /
      auto(L2,白名单内自动发布)
    与既有任务错峰: 评估 :45 / 发送 整点 / patrol 11:17+20:17 / 评论 :15
#>
[CmdletBinding()]
param([string]$Prefix = 'AI-Radar')
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable

# 下一个偶数小时的 :15(设计 §6.4)
function Next-Slot15 {
    $now = Get-Date
    $slotHour = [Math]::Floor($now.Hour / 2.0) * 2
    $t = $now.Date.AddHours($slotHour).AddMinutes(15)
    if ($t -le $now) { $t = $t.AddHours(2) }
    return $t
}

# 与 BiliPatrol-AM/PM 同款调用形式(py 启动器显式 3.12,不依赖 PATH python)
$a = New-ScheduledTaskAction -Execute 'cmd.exe' `
  -Argument "/c cd /d $dir && py -3.12 bili_comment.py run"
$t = New-ScheduledTaskTrigger -Once -At (Next-Slot15) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "$Prefix-Comment" -Action $a -Trigger $t -Settings $settings `
  -Description 'AI评论管线:每2小时:15 看懂再评论(L0 dryrun起步,模式见 config.json comment_mode)' -Force | Out-Null

Write-Host ("已安装 $Prefix-Comment  下次: " + (Get-ScheduledTaskInfo -TaskName "$Prefix-Comment").NextRunTime + "(每 2 小时 :15)")
Write-Host '卸载: schtasks /Delete /TN AI-Radar-Comment /F'
