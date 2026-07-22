@echo off
rem Launch the music DJ: daemon + overlay, no consoles. The browser side
rem needs nothing from you -- the extension opens its own Apple Music tab.
rem For logs, run instead:  python -m daemon.main --verbose
cd /d "%~dp0"
start "" /b pythonw -m daemon.main
start "" /b pythonw -m overlay.app
