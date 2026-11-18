Pearl conservative mining setup — 2026-10-24

START-PEARL.bat: applies GPU settings (Windows administrator prompt), then
opens one visible mining console. Ctrl+C or close its window to stop.
No startup task or autostart was installed.

GPU 0 RTX 3070: 100W maximum, 900MHz core ceiling.
GPU 1 RTX 3060: 100W maximum, 750MHz core ceiling (hotter card).
GPU 2 RTX 3060: 100W maximum, 900MHz core ceiling.
Memory clock locks released; existing Afterburner offsets/fan curves retained.
Actual core frequency can be below the ceiling because of the power cap.

Guard: 80C stops mining; 78C warns. GPU telemetry checked every ~2 seconds.
Read failure, changed power/core limits, or 15 minutes without an accepted
share stops mining. Each run stops after at most 24 hours. A Windows Job
Object kills the child miner when the supervising console closes.
Thermal polling cannot guarantee zero transient overshoot.

Log files mining-*.log, gpu-*.jsonl and result-*.json record each session.
GPU wattage excludes CPU/motherboard/PSU losses. Use a wall meter for net profit.
Kryptex worker: llmrig. No withdrawal address or transaction configured.

RESTORE-AI.bat: stop miner first; releases our core/memory locks and restores
the original power caps (192W / 110.5W / 136W), then use the existing AI Console.
Mining clock caps remain low until restored; otherwise AI speed will be reduced.
AI configuration backup and Afterburner profiles:
C:\Users\pjunm\LLM-backups\before-pearl-20261024
Model weights remain in place, not duplicated. No model was removed.

Validation: original 3070 reached 91C, so that run was stopped. 100W alone
caused GPU1 to hit the original conservative 76C cutoff. 810MHz memory lock
was rejected due to severe hashrate loss. 900MHz core / unlocked memory
completed a 3-minute trial, ending at 66C / 76C / 56C and about 65TH/s.
GPU1 was subsequently lowered to 750MHz for the guarded observation run.
This is a candidate for prolonged observation, NOT a completed 24h stability
or income validation. Pool-accepted shares and credited balance must be checked.
