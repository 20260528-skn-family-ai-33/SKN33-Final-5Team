@echo off
chcp 65001 >nul
rem Windows double-click start. macOS uses 실행.command.
cd /d "%~dp0.."
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js 24를 설치한 후 다시 실행해주세요: https://nodejs.org/
  pause
  exit /b 1
)
node scripts\launch-demo.mjs
if errorlevel 1 pause
