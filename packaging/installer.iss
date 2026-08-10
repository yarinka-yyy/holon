#ifndef PACKAGE_ROOT
  #error PACKAGE_ROOT is required
#endif
#ifndef OUTPUT_DIR
  #error OUTPUT_DIR is required
#endif
#ifndef LICENSE_FILE
  #error LICENSE_FILE is required
#endif
#ifndef NOTICE_FILE
  #error NOTICE_FILE is required
#endif
#ifndef SETUP_ICON
  #error SETUP_ICON is required
#endif

#define ProductName "Holon"
#define ProductVersion "0.2.0-alpha"
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
; Hermes uv environments legitimately traverse user-owned junctions. This installer never elevates.
RedirectionGuard=no
MinVersion=10.0.22000
WizardStyle=modern dynamic
LicenseFile={#LICENSE_FILE}
InfoBeforeFile={#NOTICE_FILE}
SetupIconFile={#SETUP_ICON}
UninstallDisplayIcon={localappdata}\Holon\app\HolonWallet.exe
OutputDir={#OUTPUT_DIR}
OutputBaseFilename=Holon-0.2.0-alpha-Setup
VersionInfoVersion=0.2.0.0
VersionInfoProductName={#ProductName}
VersionInfoProductVersion=0.2.0.0
Compression=lzma2/max
SolidCompression=yes
CloseApplications=no
RestartApplications=no
ChangesEnvironment=no
AllowCancelDuringInstall=no
UsePreviousAppDir=no
UsePreviousLanguage=yes
LanguageDetectionMethod=none
Uninstallable=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalShortcuts}"; Flags: unchecked

[Files]
Source: "{#PACKAGE_ROOT}\detect-hermes.ps1"; Flags: dontcopy noencryption
Source: "{#PACKAGE_ROOT}\*"; DestDir: "{tmp}\HolonPackage"; Flags: dontcopy noencryption recursesubdirs createallsubdirs
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
english.HermesRunning=Hermes is still running. Allow Setup to close it, or close its processes manually and try again.
english.HermesClosePrompt=Hermes is running and must be closed before Holon is installed.%n%nAllow Setup to close only processes running from the selected Hermes installation?
english.HermesCloseFailed=Setup could not close Hermes. Close its remaining processes in Task Manager and click Install again.
english.HermesProcessCheckFailed=Setup could not verify whether Hermes is running. No files were changed. Click Install to try again.
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
russian.HermesRunning=Hermes всё ещё запущен. Разрешите установщику закрыть его либо завершите процессы вручную и повторите попытку.
russian.HermesClosePrompt=Hermes запущен, и перед установкой Holon его необходимо закрыть.%n%nРазрешить установщику закрыть только процессы из выбранной установки Hermes?
russian.HermesCloseFailed=Установщику не удалось закрыть Hermes. Завершите оставшиеся процессы в диспетчере задач и снова нажмите «Установить».
russian.HermesProcessCheckFailed=Установщик не смог проверить, запущен ли Hermes. Файлы не изменялись. Нажмите «Установить», чтобы повторить проверку.
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
  InstallBackendCompleted: Boolean;

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

function IsCompatibleHermesVersion(const Version: String): Boolean;
var
  PatchVersion: Integer;
begin
  Result := False;
  if Pos('0.18.', Version) <> 1 then
    Exit;
  PatchVersion := StrToIntDef(Copy(Version, Length('0.18.') + 1, MaxInt), -1);
  Result := PatchVersion >= 2;
end;

function ReadHermesVersion(const HermesHome: String; var Version: String): Boolean;
var
  FindRec: TFindRec;
  MetadataLines: TArrayOfString;
  MetadataPath: String;
  SearchPath: String;
  SitePackages: String;
  Index: Integer;
begin
  Result := False;
  Version := '';
  SitePackages := AddBackslash(HermesHome) +
    'hermes-agent\venv\Lib\site-packages';
  SearchPath := AddBackslash(SitePackages) + 'hermes_agent-*.dist-info';
  if not FindFirst(SearchPath, FindRec) then
    Exit;
  try
    repeat
      if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
      begin
        MetadataPath := AddBackslash(SitePackages) + FindRec.Name + '\METADATA';
        if LoadStringsFromFile(MetadataPath, MetadataLines) then
          for Index := 0 to GetArrayLength(MetadataLines) - 1 do
            if Pos('Version: ', MetadataLines[Index]) = 1 then
            begin
              Version := Trim(Copy(MetadataLines[Index], Length('Version: ') + 1, MaxInt));
              if IsCompatibleHermesVersion(Version) then
              begin
                Result := True;
                Exit;
              end;
            end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

function TryNativeHermesRoot(const HermesHome: String; var DetectionCode: String): Boolean;
var
  HermesCommand: String;
  HermesDesktop: String;
  HermesVersion: String;
begin
  Result := False;
  if (HermesHome = '') or not DirExists(HermesHome) then
    Exit;
  HermesCommand := AddBackslash(HermesHome) +
    'hermes-agent\venv\Scripts\hermes.exe';
  if not FileExists(HermesCommand) then
    Exit;
  if not ReadHermesVersion(HermesHome, HermesVersion) then
    Exit;
  HermesDesktop := AddBackslash(HermesHome) +
    'hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe';
  if not FileExists(HermesDesktop) then
    HermesDesktop := '';
  DetectedHermesHome := HermesHome;
  DetectedHermesCommand := HermesCommand;
  DetectedHermesDesktop := HermesDesktop;
  DetectedHermesVersion := HermesVersion;
  DetectionCode := 'HERMES_READY';
  Log('Compatible Hermes found from installed package metadata: ' + HermesHome);
  Result := True;
end;

function IsPathInsideHermes(const ProcessPath, HermesHome: String): Boolean;
var
  HermesPrefix: String;
begin
  HermesPrefix := AddBackslash(HermesHome);
  Result := (Length(ProcessPath) > Length(HermesPrefix)) and
    (CompareText(Copy(ProcessPath, 1, Length(HermesPrefix)), HermesPrefix) = 0);
end;

function QueryHermesProcesses: Variant;
var
  Locator: Variant;
  Services: Variant;
begin
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Services := Locator.ConnectServer('.', 'root\CIMV2');
  Result := Services.ExecQuery(
    'SELECT ProcessId, ExecutablePath FROM Win32_Process ' +
    'WHERE ExecutablePath IS NOT NULL');
end;

function CountHermesProcesses(const HermesHome: String): Integer;
var
  ProcessItem: Variant;
  Processes: Variant;
  ProcessPath: String;
  Index: Integer;
begin
  Result := -1;
  try
    Processes := QueryHermesProcesses;
    Result := 0;
    for Index := 0 to Integer(Processes.Count) - 1 do
    begin
      ProcessItem := Processes.ItemIndex(Index);
      ProcessPath := String(ProcessItem.ExecutablePath);
      if IsPathInsideHermes(ProcessPath, HermesHome) then
        Result := Result + 1;
    end;
  except
    Log('Hermes process query failed: ' + GetExceptionMessage);
    Result := -1;
  end;
end;

function CloseHermesProcesses(const HermesHome: String): Boolean;
var
  ProcessItem: Variant;
  Processes: Variant;
  ProcessPath: String;
  TerminateResult: Variant;
  Index: Integer;
begin
  Result := False;
  try
    Processes := QueryHermesProcesses;
    for Index := 0 to Integer(Processes.Count) - 1 do
    begin
      ProcessItem := Processes.ItemIndex(Index);
      ProcessPath := String(ProcessItem.ExecutablePath);
      if IsPathInsideHermes(ProcessPath, HermesHome) then
      begin
        Log('Closing Hermes process: ' + ProcessPath);
        try
          TerminateResult := ProcessItem.Terminate(0);
          if Integer(TerminateResult) <> 0 then
            Log('Hermes process termination failed with code ' +
              IntToStr(Integer(TerminateResult)) + ': ' + ProcessPath +
              '; checking the selected installation again.');
        except
          Log('Hermes process disappeared while closing: ' + ProcessPath +
            '; checking the selected installation again.');
        end;
      end;
    end;
    Sleep(500);
    Result := CountHermesProcesses(HermesHome) = 0;
  except
    Log('Hermes process termination failed: ' + GetExceptionMessage +
      '; checking the selected installation again.');
    Result := CountHermesProcesses(HermesHome) = 0;
  end;
end;

function RunDetector(RequireClosed, UninstallMode: Boolean; var DetectionCode: String): Boolean;
var
  NativeFound: Boolean;
  ProcessCount: Integer;
  Parameters: String;
  ResultCode: Integer;
  Output: TArrayOfString;
  ResultPath: String;
  ScriptPath: String;
begin
  if UninstallMode then
    ScriptPath := ExpandConstant('{app}\detect-hermes.ps1')
  else
    ScriptPath := ExpandConstant('{tmp}\detect-hermes.ps1');
  NativeFound := False;
  if UninstallMode then
  begin
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesHome', DetectedHermesHome);
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesCommand', DetectedHermesCommand);
    RegQueryStringValue(HKCU, 'Software\Holon', 'HermesDesktop', DetectedHermesDesktop);
    NativeFound := TryNativeHermesRoot(DetectedHermesHome, DetectionCode);
  end
  else
  begin
    NativeFound := TryNativeHermesRoot(GetEnv('HERMES_HOME'), DetectionCode);
    if not NativeFound then
      NativeFound := TryNativeHermesRoot(
        ExpandConstant('{localappdata}\hermes'), DetectionCode);
  end;
  if NativeFound then
  begin
    if RequireClosed then
    begin
      ProcessCount := CountHermesProcesses(DetectedHermesHome);
      if ProcessCount < 0 then
      begin
        DetectionCode := 'HERMES_PROCESS_CHECK_FAILED';
        Result := False;
        Exit;
      end;
      if ProcessCount > 0 then
      begin
        DetectionCode := 'HERMES_RUNNING';
        Result := False;
        Exit;
      end;
    end;
    Result := True;
    Exit;
  end;
  ResultPath := ExpandConstant('{tmp}\holon-hermes-detection.txt');
  DeleteFile(ResultPath);
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + Quoted(ScriptPath) +
    ' -LocalAppDataRoot ' + Quoted(ExpandConstant('{localappdata}')) +
    ' -OutputPath ' + Quoted(ResultPath);
  if UninstallMode then
    Parameters := Parameters + ' -HermesHomeOverride ' + Quoted(DetectedHermesHome) +
      ' -HermesCommandOverride ' + Quoted(DetectedHermesCommand);
  if RequireClosed then
    Parameters := Parameters + ' -RequireClosed';
  try
    Result := Exec(PowerShellPath, Parameters, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
  except
    Result := False;
    Log(GetExceptionMessage);
  end;
  if Result and FileExists(ResultPath) and LoadStringsFromFile(ResultPath, Output) then
  begin
    DeleteFile(ResultPath);
    DetectionCode := FindOutputValue(Output, 'code');
    DetectedHermesHome := FindOutputValue(Output, 'hermes_home');
    DetectedHermesCommand := FindOutputValue(Output, 'hermes_command');
    DetectedHermesDesktop := FindOutputValue(Output, 'hermes_desktop');
    DetectedHermesVersion := FindOutputValue(Output, 'version');
    Result := (ResultCode = 0) and (DetectionCode = 'HERMES_READY');
    Exit;
  end;
  if FileExists(ResultPath) then
    DeleteFile(ResultPath);
  DetectionCode := 'HERMES_NOT_FOUND';
  Result := False;
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

function JoinOutput(const Values: TArrayOfString): String;
var
  Index: Integer;
begin
  Result := '';
  for Index := 0 to GetArrayLength(Values) - 1 do
    Result := Result + Values[Index] + ' ';
end;

function FindJsonStringValue(const Text, Key: String): String;
var
  Needle: String;
  StartIndex: Integer;
  EndIndex: Integer;
begin
  Result := '';
  Needle := '"' + Key + '":"';
  StartIndex := Pos(Needle, Text);
  if StartIndex = 0 then
    Exit;
  StartIndex := StartIndex + Length(Needle);
  EndIndex := StartIndex;
  while (EndIndex <= Length(Text)) and (Text[EndIndex] <> '"') do
    EndIndex := EndIndex + 1;
  if EndIndex > Length(Text) then
    Exit;
  Result := Copy(Text, StartIndex, EndIndex - StartIndex);
end;

function RunInstallBackend(var Details: String): Boolean;
var
  Parameters: String;
  ResultCode: Integer;
  Output: TArrayOfString;
  ResultPath: String;
  BackendCode: String;
  BackendMessage: String;
begin
  Result := False;
  Details := 'INSTALL_BACKEND_FAILED';
  ResultPath := ExpandConstant('{tmp}\holon-install-result.json');
  DeleteFile(ResultPath);
  Parameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quoted(ExpandConstant('{tmp}\HolonPackage\install.ps1')) +
    ' -PackageRoot ' + Quoted(ExpandConstant('{tmp}\HolonPackage')) +
    ' -LocalAppDataRoot ' + Quoted(ExpandConstant('{localappdata}')) +
    ' -HermesHome ' + Quoted(DetectedHermesHome) +
    ' -HermesCommand ' + Quoted(DetectedHermesCommand) +
    ' -HermesVersion ' + Quoted(DetectedHermesVersion) +
    ' -OutputPath ' + Quoted(ResultPath) +
    ' -ConfirmHermesClosed -EnableHermesPlugin';
  try
    if not Exec(PowerShellPath, Parameters, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
    begin
      Details := 'PROCESS_START_FAILED';
      Exit;
    end;
  except
    Log('Holon install backend exception: ' + GetExceptionMessage);
    Details := 'INSTALL_BACKEND_EXCEPTION';
    Exit;
  end;
  if not FileExists(ResultPath) or not LoadStringsFromFile(ResultPath, Output) then
  begin
    if FileExists(ResultPath) then
      DeleteFile(ResultPath);
    Details := 'INSTALL_BACKEND_RESULT_MISSING';
    Exit;
  end;
  DeleteFile(ResultPath);
  Details := JoinOutput(Output);
  BackendCode := FindJsonStringValue(Details, 'code');
  BackendMessage := FindJsonStringValue(Details, 'message');
  if BackendCode = '' then
    Details := 'INSTALL_BACKEND_FAILED';
  if (BackendCode <> '') and (BackendMessage <> '') then
    Details := BackendCode + ': ' + BackendMessage
  else if BackendCode <> '' then
    Details := BackendCode;
  Result := (ResultCode = 0) and (BackendCode = 'INSTALL_OK');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  DetectionCode: String;
  Details: String;
  ProcessCount: Integer;
begin
  Result := '';
  InstallBackendCompleted := False;
  if not RunDetector(False, False, DetectionCode) then
  begin
    Result := CustomMessage('HermesUnavailable');
    Exit;
  end;
  ProcessCount := CountHermesProcesses(DetectedHermesHome);
  if ProcessCount < 0 then
  begin
    Result := CustomMessage('HermesProcessCheckFailed');
    Exit;
  end;
  if ProcessCount > 0 then
  begin
    if MsgBox(CustomMessage('HermesClosePrompt'), mbConfirmation, MB_YESNO) <> IDYES then
    begin
      Result := CustomMessage('HermesRunning');
      Exit;
    end;
    if not CloseHermesProcesses(DetectedHermesHome) then
    begin
      Result := CustomMessage('HermesCloseFailed');
      Exit;
    end;
  end;
  try
    ExtractTemporaryFiles('{tmp}\HolonPackage\*');
  except
    Log('Holon package extraction failed: ' + GetExceptionMessage);
    Result := FmtMessage(CustomMessage('InstallFailed'), ['INSTALLER_PAYLOAD_FAILED']);
    Exit;
  end;
  if not RunInstallBackend(Details) then
  begin
    Result := FmtMessage(CustomMessage('InstallFailed'), [Details]);
    Exit;
  end;
  InstallBackendCompleted := True;
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
  Result := InstallBackendCompleted and (GetHermesLaunchTarget('') <> '');
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

#ifdef DETECTION_SELFTEST
#ifndef DETECTION_SELFTEST_OUTPUT
  #error DETECTION_SELFTEST_OUTPUT is required
#endif
function InitializeSetup: Boolean;
var
  DetectionCode: String;
  HermesHome: String;
  ProcessCount: Integer;
  SelfTestResult: String;
  CaptureOutput: TExecOutput;
  CaptureResultCode: Integer;
  CaptureStarted: Boolean;
  ExtractedPackage: Boolean;
begin
  Result := False;
#ifdef EXTRACTION_SELFTEST
  ExtractTemporaryFiles('{tmp}\HolonPackage\*');
  ExtractedPackage := FileExists(ExpandConstant('{tmp}\HolonPackage\install.ps1')) and
    FileExists(ExpandConstant('{tmp}\HolonPackage\payload\app\HolonGuard.exe')) and
    FileExists(ExpandConstant('{tmp}\HolonPackage\payload\skills\crypto\holon\SKILL.md'));
#endif
#ifdef DETECTION_SELFTEST_HERMES_HOME
  HermesHome := '{#DETECTION_SELFTEST_HERMES_HOME}';
#else
  HermesHome := ExpandConstant('{localappdata}\hermes');
#endif
  if not TryNativeHermesRoot(HermesHome, DetectionCode) then
    SelfTestResult := 'code=HERMES_NOT_FOUND'
  else
  begin
    ProcessCount := CountHermesProcesses(HermesHome);
    SelfTestResult := 'code=' + DetectionCode + #13#10 +
      'version=' + DetectedHermesVersion + #13#10 +
      'hermes_home=' + DetectedHermesHome + #13#10 +
      'process_count=' + IntToStr(ProcessCount);
#ifdef EXTRACTION_SELFTEST
    SelfTestResult := SelfTestResult + #13#10 +
      'package_extracted=' + IntToStr(Integer(ExtractedPackage));
#endif
#ifdef DETECTION_SELFTEST_CLOSE
    SelfTestResult := SelfTestResult + #13#10 +
      'close_result=' + IntToStr(Integer(CloseHermesProcesses(HermesHome))) + #13#10 +
      'remaining_count=' + IntToStr(CountHermesProcesses(HermesHome));
#endif
  end;
#ifdef EXEC_CAPTURE_SELFTEST
  CaptureStarted := ExecAndCaptureOutput(PowerShellPath,
    '-NoProfile -NonInteractive -Command "Write-Output HOLON_CAPTURE_OK"',
    '', SW_HIDE, ewWaitUntilTerminated, CaptureResultCode, CaptureOutput);
  SelfTestResult := SelfTestResult + #13#10 +
    'capture_started=' + IntToStr(Integer(CaptureStarted)) + #13#10 +
    'capture_error=' + IntToStr(Integer(CaptureOutput.Error)) + #13#10 +
    'capture_exit=' + IntToStr(CaptureResultCode) + #13#10 +
    'capture_stdout=' + JoinOutput(CaptureOutput.StdOut) + #13#10 +
    'capture_stderr=' + JoinOutput(CaptureOutput.StdErr);
#endif
  SaveStringToFile('{#DETECTION_SELFTEST_OUTPUT}', SelfTestResult, False);
end;
#endif
