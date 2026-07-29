#ifndef PACKAGE_ROOT
  #error PACKAGE_ROOT is required
#endif
#ifndef OUTPUT_DIR
  #error OUTPUT_DIR is required
#endif
#ifndef LICENSE_FILE
  #error LICENSE_FILE is required
#endif
#ifndef SETUP_ICON
  #error SETUP_ICON is required
#endif

#define ProductName "Holon"
#define ProductVersion "0.1.0-alpha"
#define ProductId "{{F690E30A-1C8E-47E1-AF5E-65243A4662CC}"

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#ProductVersion}
AppVerName={#ProductName} {#ProductVersion}
AppPublisher=Holon
AppPublisherURL=https://github.com/yarinka-yyy/holon
AppSupportURL=https://github.com/yarinka-yyy/holon/issues
DefaultDirName={localappdata}\Holon\installer
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
WizardStyle=modern dynamic
LicenseFile={#LICENSE_FILE}
SetupIconFile={#SETUP_ICON}
UninstallDisplayIcon={localappdata}\Holon\app\HolonWallet.exe
OutputDir={#OUTPUT_DIR}
OutputBaseFilename=Holon-0.1.0-alpha-Setup
VersionInfoVersion=0.1.0.0
VersionInfoProductName={#ProductName}
VersionInfoProductVersion=0.1.0.0
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
RestartApplications=no
ChangesEnvironment=no
UsePreviousAppDir=no
UsePreviousLanguage=yes
Uninstallable=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalShortcuts}"; Flags: unchecked

[Files]
Source: "{#PACKAGE_ROOT}\detect-hermes.ps1"; Flags: dontcopy noencryption
Source: "{#PACKAGE_ROOT}\*"; DestDir: "{tmp}\HolonPackage"; Flags: ignoreversion recursesubdirs createallsubdirs deleteafterinstall
Source: "{#PACKAGE_ROOT}\uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PACKAGE_ROOT}\detect-hermes.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PACKAGE_ROOT}\InstallSupport.psm1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PACKAGE_ROOT}\release-manifest.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Holon\Holon Wallet"; Filename: "{localappdata}\Holon\app\HolonWallet.exe"; WorkingDir: "{localappdata}\Holon\app"
Name: "{autodesktop}\Holon Wallet"; Filename: "{localappdata}\Holon\app\HolonWallet.exe"; WorkingDir: "{localappdata}\Holon\app"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Holon"; ValueType: string; ValueName: "HermesHome"; ValueData: "{code:GetDetectedHermesHome}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Holon"; ValueType: string; ValueName: "HermesCommand"; ValueData: "{code:GetDetectedHermesCommand}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Holon"; ValueType: string; ValueName: "HermesDesktop"; ValueData: "{code:GetDetectedHermesDesktop}"; Flags: uninsdeletekey

[Run]
Filename: "{code:GetHermesLaunchTarget}"; Description: "{cm:LaunchHermes}"; Flags: postinstall nowait skipifsilent shellexec; Check: HermesLaunchAvailable; BeforeInstall: PrepareHermesLaunch

[CustomMessages]
english.HermesPageTitle=Hermes integration
english.DesktopShortcut=Create a desktop shortcut
english.AdditionalShortcuts=Additional shortcuts:
english.HermesPageDescription=Holon connects to an existing compatible Hermes installation.
english.HermesDetecting=Checking Hermes...
english.HermesReady=Hermes %1 was found.%n%nLocation: %2%n%nInstall will enable the Holon plugin without allowing tool overrides.
english.HermesUnavailable=Compatible Hermes was not found. Install Hermes 0.18.2 through 0.18.x, then run Holon Setup again.
english.HermesRunning=Hermes is running. Close it and click Next again. Setup will never terminate Hermes automatically.
english.InstallFailed=Holon installation failed and previous files were restored. Details: %1
english.UninstallDataQuestion=Remove local Holon Wallet data too?%n%nChoose No to preserve accounts, encrypted vault data, settings, and history.
english.UninstallDataConfirm=This permanently removes local Holon Wallet data and cannot be undone. Continue?
english.UninstallRunning=Close Hermes before removing Holon. The uninstaller will not terminate it automatically.
english.UninstallWarning=Holon files could not be fully cleaned. Details: %1
english.FinishInstruction=Holon is installed.%n%nOpen Hermes and type /holon to begin.
english.LaunchHermes=Open Hermes (then type /holon)
russian.HermesPageTitle=Интеграция с Hermes
russian.DesktopShortcut=Создать ярлык на рабочем столе
russian.AdditionalShortcuts=Дополнительные ярлыки:
russian.HermesPageDescription=Holon подключается к уже установленной совместимой версии Hermes.
russian.HermesDetecting=Проверяем Hermes...
russian.HermesReady=Найден Hermes %1.%n%nПуть: %2%n%nНажатие «Установить» включит плагин Holon без разрешения подменять tools.
russian.HermesUnavailable=Совместимый Hermes не найден. Установите Hermes версии 0.18.2–0.18.x и снова запустите установщик Holon.
russian.HermesRunning=Hermes запущен. Закройте его и снова нажмите «Далее». Установщик никогда не завершает Hermes принудительно.
russian.InstallFailed=Установка Holon не выполнена; предыдущие файлы восстановлены. Подробности: %1
russian.UninstallDataQuestion=Удалить также локальные данные Holon Wallet?%n%nВыберите «Нет», чтобы сохранить аккаунты, зашифрованное хранилище, настройки и историю.
russian.UninstallDataConfirm=Локальные данные Holon Wallet будут удалены без возможности восстановления. Продолжить?
russian.UninstallRunning=Закройте Hermes перед удалением Holon. Деинсталлятор не завершает его принудительно.
russian.UninstallWarning=Не удалось полностью удалить файлы Holon. Подробности: %1
russian.FinishInstruction=Holon установлен.%n%nОткройте Hermes и введите /holon, чтобы начать.
russian.LaunchHermes=Открыть Hermes (затем введите /holon)

[Code]
var
  HermesPage: TWizardPage;
  HermesLabel: TNewStaticText;
  DetectedHermesHome: String;
  DetectedHermesCommand: String;
  DetectedHermesDesktop: String;
  DetectedHermesVersion: String;
  RemoveWalletData: Boolean;

function SetEnvironmentVariable(Name, Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function Quoted(Value: String): String;
begin
  StringChangeEx(Value, '"', '', True);
  Result := '"' + Value + '"';
end;

function PowerShellPath: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function FindOutputValue(const Output: TArrayOfString; const Name: String): String;
var
  Index: Integer;
  Prefix: String;
begin
  Result := '';
  Prefix := Name + '=';
  for Index := 0 to GetArrayLength(Output) - 1 do
    if Pos(Prefix, Output[Index]) = 1 then
    begin
      Result := Copy(Output[Index], Length(Prefix) + 1, MaxInt);
      Exit;
    end;
end;

function RunDetector(RequireClosed, UninstallMode: Boolean; var DetectionCode: String): Boolean;
var
  Parameters: String;
  ResultCode: Integer;
  Output: TExecOutput;
  ScriptPath: String;
begin
  if UninstallMode then
    ScriptPath := ExpandConstant('{app}\detect-hermes.ps1')
  else
    ScriptPath := ExpandConstant('{tmp}\detect-hermes.ps1');
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quoted(ScriptPath) +
    ' -LocalAppDataRoot ' + Quoted(ExpandConstant('{localappdata}'));
  if UninstallMode then
  begin
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesHome', DetectedHermesHome);
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesCommand', DetectedHermesCommand);
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesDesktop', DetectedHermesDesktop);
    Parameters := Parameters + ' -HermesHomeOverride ' + Quoted(DetectedHermesHome) +
      ' -HermesCommandOverride ' + Quoted(DetectedHermesCommand);
  end;
  if RequireClosed then
    Parameters := Parameters + ' -RequireClosed';
  try
    Result := ExecAndCaptureOutput(PowerShellPath, Parameters, '', SW_SHOWNORMAL,
      ewWaitUntilTerminated, ResultCode, Output);
  except
    Result := False;
    Log(GetExceptionMessage);
  end;
  if not Result or Output.Error then
  begin
    DetectionCode := 'HERMES_NOT_FOUND';
    Result := False;
    Exit;
  end;
  DetectionCode := FindOutputValue(Output.StdOut, 'code');
  DetectedHermesHome := FindOutputValue(Output.StdOut, 'hermes_home');
  DetectedHermesCommand := FindOutputValue(Output.StdOut, 'hermes_command');
  DetectedHermesDesktop := FindOutputValue(Output.StdOut, 'hermes_desktop');
  DetectedHermesVersion := FindOutputValue(Output.StdOut, 'version');
  Result := (ResultCode = 0) and (DetectionCode = 'HERMES_READY');
end;

procedure RefreshHermesPage;
var
  DetectionCode: String;
begin
  HermesLabel.Caption := CustomMessage('HermesDetecting');
  if RunDetector(False, False, DetectionCode) then
    HermesLabel.Caption := FmtMessage(CustomMessage('HermesReady'), [DetectedHermesVersion, DetectedHermesHome])
  else if DetectionCode = 'HERMES_RUNNING' then
    HermesLabel.Caption := CustomMessage('HermesRunning')
  else
    HermesLabel.Caption := CustomMessage('HermesUnavailable');
end;

procedure InitializeWizard;
begin
  ExtractTemporaryFile('detect-hermes.ps1');
  HermesPage := CreateCustomPage(wpLicense, CustomMessage('HermesPageTitle'),
    CustomMessage('HermesPageDescription'));
  HermesLabel := TNewStaticText.Create(HermesPage);
  HermesLabel.Parent := HermesPage.Surface;
  HermesLabel.Left := 0;
  HermesLabel.Top := 8;
  HermesLabel.Width := HermesPage.SurfaceWidth;
  HermesLabel.Height := ScaleY(160);
  HermesLabel.WordWrap := True;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = HermesPage.ID then
    RefreshHermesPage;
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := CustomMessage('FinishInstruction');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  DetectionCode: String;
begin
  Result := True;
  if CurPageID = HermesPage.ID then
  begin
    Result := RunDetector(False, False, DetectionCode);
    if not Result then
    begin
      if DetectionCode = 'HERMES_RUNNING' then
        MsgBox(CustomMessage('HermesRunning'), mbInformation, MB_OK)
      else
        MsgBox(CustomMessage('HermesUnavailable'), mbError, MB_OK);
      RefreshHermesPage;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  DetectionCode: String;
begin
  Result := '';
  if not RunDetector(True, False, DetectionCode) then
  begin
    if DetectionCode = 'HERMES_RUNNING' then
      Result := CustomMessage('HermesRunning')
    else
      Result := CustomMessage('HermesUnavailable');
  end;
end;

function JoinOutput(const Values: TArrayOfString): String;
var
  Index: Integer;
begin
  Result := '';
  for Index := 0 to GetArrayLength(Values) - 1 do
    Result := Result + Values[Index] + ' ';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Parameters: String;
  ResultCode: Integer;
  Output: TExecOutput;
  Details: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{tmp}\HolonPackage\install.ps1')) +
    ' -PackageRoot ' + Quoted(ExpandConstant('{tmp}\HolonPackage')) +
    ' -LocalAppDataRoot ' + Quoted(ExpandConstant('{localappdata}')) +
    ' -HermesHome ' + Quoted(DetectedHermesHome) +
    ' -HermesCommand ' + Quoted(DetectedHermesCommand) +
    ' -ConfirmHermesClosed -EnableHermesPlugin';
  try
    if not ExecAndCaptureOutput(PowerShellPath, Parameters, '', SW_SHOWNORMAL,
      ewWaitUntilTerminated, ResultCode, Output) then
      RaiseException(FmtMessage(CustomMessage('InstallFailed'), ['PROCESS_START_FAILED']));
  except
    RaiseException(FmtMessage(CustomMessage('InstallFailed'), [GetExceptionMessage]));
  end;
  Details := JoinOutput(Output.StdErr) + JoinOutput(Output.StdOut);
  if Output.Error or (ResultCode <> 0) then
    RaiseException(FmtMessage(CustomMessage('InstallFailed'), [Details]));
end;

function GetDetectedHermesHome(Param: String): String;
begin
  Result := DetectedHermesHome;
end;

function GetDetectedHermesCommand(Param: String): String;
begin
  Result := DetectedHermesCommand;
end;

function GetDetectedHermesDesktop(Param: String): String;
begin
  Result := DetectedHermesDesktop;
end;

function GetHermesLaunchTarget(Param: String): String;
begin
  if DetectedHermesDesktop <> '' then
    Result := DetectedHermesDesktop
  else
    Result := DetectedHermesCommand;
end;

function HermesLaunchAvailable: Boolean;
begin
  Result := GetHermesLaunchTarget('') <> '';
end;

procedure PrepareHermesLaunch;
begin
  SetEnvironmentVariable('HERMES_HOME', DetectedHermesHome);
end;

function InitializeUninstall: Boolean;
var
  DetectionCode: String;
begin
  RemoveWalletData := MsgBox(CustomMessage('UninstallDataQuestion'),
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if RemoveWalletData then
    RemoveWalletData := MsgBox(CustomMessage('UninstallDataConfirm'),
      mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  RunDetector(True, True, DetectionCode);
  if DetectionCode = 'HERMES_RUNNING' then
  begin
    MsgBox(CustomMessage('UninstallRunning'), mbInformation, MB_OK);
    Result := False;
  end
  else
    Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Parameters: String;
  ResultCode: Integer;
  Output: TExecOutput;
  Details: String;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{app}\uninstall.ps1')) +
    ' -LocalAppDataRoot ' + Quoted(ExpandConstant('{localappdata}')) +
    ' -HermesHome ' + Quoted(DetectedHermesHome) +
    ' -HermesCommand ' + Quoted(DetectedHermesCommand) +
    ' -ConfirmHermesClosed';
  if RemoveWalletData then
    Parameters := Parameters + ' -RemoveData -ConfirmDataDeletion';
  try
    if not ExecAndCaptureOutput(PowerShellPath, Parameters, '', SW_SHOWNORMAL,
      ewWaitUntilTerminated, ResultCode, Output) then
    begin
      MsgBox(FmtMessage(CustomMessage('UninstallWarning'), ['PROCESS_START_FAILED']),
        mbError, MB_OK);
      Exit;
    end;
  except
    MsgBox(FmtMessage(CustomMessage('UninstallWarning'), [GetExceptionMessage]),
      mbError, MB_OK);
    Exit;
  end;
  Details := JoinOutput(Output.StdErr) + JoinOutput(Output.StdOut);
  if Output.Error or (ResultCode <> 0) then
    MsgBox(FmtMessage(CustomMessage('UninstallWarning'), [Details]), mbError, MB_OK);
end;
