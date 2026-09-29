@echo off
rem Development launcher now uses the same checked live main chain as Runtime Base.
call "%~dp0START_RUNTIME_BASE.bat"
exit /b %errorlevel%
