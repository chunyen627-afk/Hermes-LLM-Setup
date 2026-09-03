@echo off
REM ASCII only on purpose -- UTF-8 comments become garbage and get executed.
REM
REM Why this wrapper exists (2026-09-03):
REM   launch_simulation spawns compile.bat, which calls xvlog / xvhdl by bare
REM   name. Those are only on PATH after settings64.bat runs. Without it Vivado
REM   reports just "Spawn failed: Broken pipe" and nothing else -- the real
REM   message ("xvlog is not recognized") only appears if you run compile.bat
REM   by hand. Vivado does not inherit a PATH exported from the calling shell,
REM   so the environment has to be set up in the same cmd session.
REM
REM Usage:  cmd /c C:\Users\pjunm\matmul_axi\_runsim.bat [tcl-script]
REM Default script is vivado/05b_sim.tcl.

call "C:\Xilinx\Vivado\2024.2\settings64.bat"
cd /d "C:\Users\pjunm\matmul_axi"

set "SCRIPT=%~1"
if "%SCRIPT%"=="" set "SCRIPT=vivado/05b_sim.tcl"

vivado -mode batch -nojournal -nolog -source "%SCRIPT%"
