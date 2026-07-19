@echo off
cd /d %~dp0
start "" "%~dp0runtime\pythonw.exe" gui.py
