@echo off
rem Part 1: the controller in simulation (about 20 seconds)
cd /d "%~dp0.."
set PYTHONPATH=.
python demo_run.py %*
