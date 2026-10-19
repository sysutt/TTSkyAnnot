; Inno Setup 6 script for TTSkyAnnot.
;
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DVersion=0.2.6 /DBuildDir="E:\SkyAnnot\dist\TTSkyAnnot" packaging\installer.iss
;
; Installs per user by default (no admin prompt), which keeps the program folder writable: the app keeps its
; config.json beside the exe and its in-app updater swaps that folder, both of which need write access.
; An administrator install is still offered through the privileges dialog.

#ifndef Version
  #define Version "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\dist\TTSkyAnnot"
#endif

#define AppName "TTSkyAnnot"
#define AppTitle "TTSkyAnnot 星图标注"
#define AppPublisher "TTastro"
#define AppURL "https://www.ttastro.com/ttskyannot/"
#define AppExe "TTSkyAnnot.exe"

[Setup]
AppId={{8F3B7C2E-4D61-4A9B-8C5F-2E7D1B6A9C34}
AppName={#AppTitle}
AppVersion={#Version}
AppVerName={#AppTitle} {#Version}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
VersionInfoVersion={#Version}
VersionInfoDescription={#AppTitle}
VersionInfoCompany={#AppPublisher}
VersionInfoProductName={#AppName}
DefaultDirName={localappdata}\Programs\TTSkyAnnot
DefaultGroupName={#AppTitle}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\setup
OutputBaseFilename=TTSkyAnnot-Setup-{#Version}
SetupIconFile=..\data\app\skyannot.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppTitle}
LicenseFile=..\LICENSE
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
AllowNoIcons=yes
ShowLanguageDialog=auto

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
    Excludes: "config.json,*.log,*.old,使用说明.txt"

[Icons]
Name: "{group}\{#AppTitle}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppTitle}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppTitle}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppTitle}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\*.log"
Type: dirifempty; Name: "{app}"
