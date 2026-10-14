@echo off
REM Build dist\TTSkyAnnot\ (window TTSkyAnnot.exe + console ttskyannot-cli.exe)
cd /d "%~dp0\.."
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\skyannot.spec
