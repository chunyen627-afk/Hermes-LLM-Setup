Pearl balanced mining candidate — 2026-10-24

LATEST THERMAL POLICY: 76C triggers a step down every 30 seconds (-5W to
minimum 100W, -150MHz to minimum 300MHz). At 80C immediately request
100W/300MHz and continue mining. If not below 78C within 30 seconds, or any
GPU reaches 82C, stop all mining. Driver/readback failures also stop.
No automatic upward recovery during a session; restart only after cooling
below 76C. Older 80C-stop notes below describe the previous revision.

AFTERBURNER-INDEPENDENT MODE (latest): START-PEARL.bat opens one elevated
mining window via RUN-MINING.ps1. It stops Afterburner and disables its startup
task to avoid competing controllers. Original Profiles and task XML were backed
up in afterburner-backup-*. Memory/core offsets are explicitly set to zero.
fan_control.py uses NVIDIA NVML for all six fans: 40% at 40C, rising linearly
to 100% at 75C, with a small downward hysteresis. Targets are read back; a
failure stops mining. fan_restore_watch.py waits on the exact guard process
and restores NVIDIA automatic fan policy after it exits, including crashes.
Normal stop and RESTORE-AI also restore automatic fan policy. This protection
does not run when Windows itself is frozen. No Afterburner dependency for mining.
To revert to Afterburner after stopping mining, run RESTORE-AFTERBURNER.ps1
as administrator. Its existing profiles are preserved; review settings before AI.
Earlier notes about retaining Afterburner curves below are historical.

START-PEARL.bat: applies GPU settings (Windows administrator prompt), then
opens one visible mining console. Ctrl+C or close its window to stop.
Before changing GPU settings, it requests AI Console Stop, then waits up to
60 seconds for ports 8001/8188/8189/5000 to close and all three GPUs to stay
below 2048 MiB used for three checks. Desktop GPU usage is allowed; this is
a low-memory gate, not proof that every GPU context is gone. Existing mining,
telemetry failures or unreleased resources block startup. Hermes stays open.
No startup task or autostart was installed.

GPU 0 RTX 3070: 110W maximum, 900MHz core ceiling.
GPU 1 RTX 3060: 100W maximum, 600MHz core ceiling (hotter card).
GPU 2 RTX 3060: 125W maximum, 1500MHz core ceiling.
Original mining settings are in baseline-before-tuning. To revert: stop mining,
copy its four files back into this folder, then run START-PEARL.bat.
Balanced trial: about 72TH/s versus baseline 62TH/s. Final stage about four
minutes, following four minutes at GPU0=100W. Not a 24h stability certification.
Raising GPU2 without lowering hot GPU1 triggered the 78C trial cutoff.
Memory clock locks released; existing Afterburner offsets/fan curves retained.
Actual core frequency can be below the ceiling because of the power cap.

Guard: 80C stops mining; 78C warns. GPU telemetry checked every ~2 seconds.
Read failure, changed power/core limits, or 15 minutes without an accepted
share stops mining. Runs continuously without a daily time limit. A Windows Job
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
