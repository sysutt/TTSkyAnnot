@echo off
REM Build dist\TTSkyAnnot\ (window TTSkyAnnot.exe + console ttskyannot-cli.exe)
cd /d "%~dp0\.."
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\skyannot.spec
if errorlevel 1 exit /b 1

REM A freshly written file that inherited a "downloaded" mark makes Windows ask before running it.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem 'dist\TTSkyAnnot' -Recurse -File | Unblock-File"

REM Sign the two executables with the local certificate, so Windows knows the publisher and stops asking.
REM (Run  powershell -File packaging\sign.ps1 -Create  once to make the certificate; it lives in the current
REM  user's certificate store and its public half is trusted there as a root and as a publisher.)
powershell -NoProfile -ExecutionPolicy Bypass -File "packaging\sign.ps1"
