$ErrorActionPreference='Stop'
# One-time migration: validate every service before closing its old standalone console.
$all=@(Get-CimInstance Win32_Process)
$services=@{
    8001='C:\Users\pjunm\Strata\'
    1234='C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\hermes_bridge.py'
    8188='C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI\main.py'
    8189='C:\Users\pjunm\ComfyUI-H3\main.py'
    8003='C:\Users\pjunm\OneDrive\Desktop\hermes\comfy_proxy.py'
    5000='START-FAMILY-GUI.bat'
}
$targets=@{}
foreach($port in $services.Keys){
    foreach($owner in @((Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique)){
        $p=$all | Where-Object ProcessId -eq $owner
        $parent=$all | Where-Object ProcessId -eq $p.ParentProcessId
        $identity=if($port -eq 5000){$parent.CommandLine}else{$p.CommandLine}
        if(-not $identity -or -not $identity.Contains($services[$port])){throw "Unexpected service on $port; stopped nothing"}
        if($port -eq 8001){$s=Invoke-RestMethod "http://127.0.0.1:$port/v1/status" -TimeoutSec 5;if($s.activity.in_flight){throw 'Chat is busy'}}
        if($port -in @(8188,8189)){$q=Invoke-RestMethod "http://127.0.0.1:$port/queue" -TimeoutSec 5;if($q.queue_running.Count -or $q.queue_pending.Count){throw 'Media is busy'}}
        $targets[[int]$p.ProcessId]=$p
        if($parent.Name -eq 'python.exe' -and $parent.CommandLine -like '*C:\Users\pjunm\Strata\.venv\*'){$targets[[int]$parent.ProcessId]=$parent}
    }
}
$batches=@('C:\Users\pjunm\Strata\RUN-TPS-TUNING.bat','C:\Users\pjunm\Strata\RUN-CHAT-IMAGE.bat',
    'C:\Users\pjunm\Strata\RUN-CHAT-VIDEO.bat','C:\Users\pjunm\Strata\RUN-STATIC-IMAGE.bat',
    'C:\Users\pjunm\Strata\RUN-H3.bat','C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\_bridge.bat',
    'C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\RUN-IMAGE-PROXY-PAIR.bat',
    'C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\START-FAMILY-GUI.bat')
foreach($p in $all | Where-Object Name -eq 'cmd.exe'){
    if(@($batches | Where-Object {$p.CommandLine -and $p.CommandLine.Contains($_)}).Count){$targets[[int]$p.ProcessId]=$p}
}
# Descendants of the verified service/launcher roots belong to these local services.
do {
    $added=$false
    foreach($p in $all){if($targets.ContainsKey([int]$p.ParentProcessId) -and -not $targets.ContainsKey([int]$p.ProcessId)){$targets[[int]$p.ProcessId]=$p;$added=$true}}
} while($added)
foreach($p in $targets.Values){
    try{Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop}
    catch{if(Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue){throw}}
}
[pscustomobject]@{StoppedIds=@($targets.Keys);Scope='Verified legacy model, bridge, media, and their own console windows only'} | ConvertTo-Json -Compress
