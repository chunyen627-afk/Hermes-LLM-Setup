@echo off
rem Asks the guarded miner to stop; it restores GPU clocks and the CPU limit itself.
type nul > C:\Users\pjunm\Pearl-mining\STOP
echo Stop requested. The miner stops within a few seconds.
timeout /t 5
