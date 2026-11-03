; EngineerDiff Inno Setup 6 安装包脚本
; ---------------------------------------------------------------------------
; 目标：
;   * Windows x64，默认 per-user 安装（无需管理员）：
;       {localappdata}\Programs\EngineerDiff
;   * 完全离线：无任何下载/联机行为，OCR 模型随包；
;   * 简体中文界面；
;   * 开始菜单快捷方式 + 可选桌面快捷方式；
;   * 许可页（MIT），随包安装 使用说明 / 已知限制 / 第三方许可证；
;   * 干净卸载（用户 OCR 缓存默认保留，见 [Code] 说明）。
;
; 编译（在项目根目录执行，Inno Setup 6.5+）：
;   ISCC.exe packaging\engineerdiff.iss
; 输入目录：
;   ..\dist\EngineerDiff\        PyInstaller one-dir 产物（EngineerDiff.exe + _internal\）
;   ..\docs\*.md                 交付文档
;   ..\LICENSE                   MIT 许可证
; 输出：
;   ..\release\EngineerDiff-1.2.5-Setup-x64.exe
;
; 版本号约定：与 src/engineerdiff/version.py、pyproject.toml 保持一致。
; ---------------------------------------------------------------------------

#define APP_NAME "EngineerDiff"
#define APP_NAME_CN "EngineerDiff 工程文件差异比较"
#define APP_VERSION "1.2.5"
#define APP_PUBLISHER "jzh"
#define APP_EXE "EngineerDiff.exe"
#define APP_GUID "{{245CB9EB-4ABF-4388-BCAB-0DF568AED985}"
#define SOURCE_DIR "..\dist\EngineerDiff"
#define OUTPUT_DIR "..\release"

[Setup]
; AppId 必须永久稳定（升级/卸载识别），不要随版本变化。
AppId={#APP_GUID}
AppName={#APP_NAME_CN}
AppVersion={#APP_VERSION}
AppVerName={#APP_NAME} {#APP_VERSION}
AppPublisher={#APP_PUBLISHER}
AppCopyright=Copyright (c) 2026 jzh
VersionInfoVersion={#APP_VERSION}.0
VersionInfoCompany={#APP_PUBLISHER}
VersionInfoDescription={#APP_NAME_CN} 安装程序
VersionInfoProductName={#APP_NAME}
VersionInfoProductVersion={#APP_VERSION}
VersionInfoCopyright=Copyright (c) 2026 EngineerDiff contributors

; 默认用户级安装（HKCU / {localappdata}，无需管理员权限）。
; 高级用户可通过命令行 /ALLUSERS 覆盖为全局安装（需管理员）。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
DefaultDirName={localappdata}\Programs\{#APP_NAME}
DefaultGroupName={#APP_NAME}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes

; 仅支持 64 位 Windows 10+；安装目录为 64 位模式。
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; 输出与外观
OutputDir={#OUTPUT_DIR}
OutputBaseFilename=EngineerDiff-{#APP_VERSION}-Setup-x64
SetupIconFile=..\src\engineerdiff\resources\engineerdiff.ico
UninstallDisplayIcon={app}\{#APP_EXE}
UninstallDisplayName={#APP_NAME_CN}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
SetupLogging=yes
CloseApplications=yes
RestartApplications=no

[Languages]
; 简体中文语言文件随仓库分发（与 Inno Setup 6.5 兼容，MIT，来源见
; packaging/languages/README.md）；未在此列出的语言回退到英文默认消息。
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller one-dir 全量产物（含 _internal 下的 Qt 插件、OCR 模型、ONNX Runtime DLL）
; PyInstaller one-dir 全量产物已包含 docs\、LICENSES\、NOTICE、LICENSE 与截图，
; 不再逐项重复添加，避免 Inno 的重复目标文件条目。
Source: "{#SOURCE_DIR}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#APP_NAME}"; Filename: "{app}\{#APP_EXE}"; WorkingDir: "{app}"; Comment: "{#APP_NAME_CN}"
Name: "{group}\{cm:UninstallProgram,{#APP_NAME}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#APP_NAME}"; Filename: "{app}\{#APP_EXE}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#APP_EXE}"; Description: "{cm:LaunchProgram,{#APP_NAME}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理安装器生成的空目录；用户 OCR 缓存/比较设置位于 %LOCALAPPDATA%\EngineerDiff，
; 默认保留，便于重装后继续使用（如确需彻底清除请手动删除该目录）。
Type: dirifempty; Name: "{app}\docs"
Type: dirifempty; Name: "{app}"
