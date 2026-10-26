Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!define VERSION "0.1.0"
!define APPKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MOA"
!define STAGE "..\runtime\release-staging\MOA-${VERSION}"
Name "MOA · 모아"
OutFile "..\dist\MOA-Setup-${VERSION}-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\MOA"
InstallDirRegKey HKCU "${APPKEY}" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
BrandingText "MOA · Local Agent Team"
VIProductVersion "0.1.0.0"
VIAddVersionKey /LANG=1042 "ProductName" "MOA"
VIAddVersionKey /LANG=1042 "FileDescription" "MOA 설치 프로그램"
VIAddVersionKey /LANG=1042 "FileVersion" "0.1.0"
VIAddVersionKey /LANG=1042 "LegalCopyright" "MOA"
!define MUI_ICON "${STAGE}\runtime\desktop\moa.ico"
!define MUI_UNICON "${STAGE}\runtime\desktop\moa.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "MOA 설치를 시작합니다"
!define MUI_WELCOMEPAGE_TEXT "로컬 AI 팀과 함께 일하는 MOA를 설치합니다.$\r$\n$\r$\nPython과 필수 라이브러리가 포함되어 있습니다. 첫 실행에서 무료 AI 모델(약 4.7GB)과 선택한 VM 환경을 준비합니다.$\r$\n$\r$\n개인 Slack 인증과 업무 기록은 이 설치 파일에 포함되지 않습니다."
!define MUI_FINISHPAGE_RUN "$INSTDIR\MOA.exe"
!define MUI_FINISHPAGE_RUN_TEXT "MOA 실행 및 첫 준비"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
 ${IfNot} ${RunningX64}
  MessageBox MB_ICONSTOP "MOA는 Windows x64를 지원합니다."
  Abort
 ${EndIf}
 ${IfNot} ${AtLeastWin10}
  MessageBox MB_ICONSTOP "Windows 10 이상이 필요합니다."
  Abort
 ${EndIf}
FunctionEnd
Section "MOA" SEC_APP
 SetShellVarContext current
 IfFileExists "$INSTDIR\scripts\StopInstalled.ps1" 0 +4
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\StopInstalled.ps1"' $0
  IntCmp $0 0 +2
  Abort "실행 중인 MOA 창을 닫고 다시 설치해 주세요."
 SetOutPath "$INSTDIR"
 SetOverwrite on
 File /r /x config.json "${STAGE}\*"
 SetOverwrite off
 File "${STAGE}\config.json"
 SetOverwrite on
 WriteUninstaller "$INSTDIR\Uninstall.exe"
 CreateDirectory "$SMPROGRAMS\MOA"
 CreateShortcut "$SMPROGRAMS\MOA\모아.lnk" "$INSTDIR\MOA.exe" "" "$INSTDIR\runtime\desktop\moa.ico"
 CreateShortcut "$SMPROGRAMS\MOA\모아 제거.lnk" "$INSTDIR\Uninstall.exe"
 CreateShortcut "$DESKTOP\모아.lnk" "$INSTDIR\MOA.exe" "" "$INSTDIR\runtime\desktop\moa.ico"
 WriteRegStr HKCU "${APPKEY}" "DisplayName" "MOA · 모아"
 WriteRegStr HKCU "${APPKEY}" "DisplayVersion" "${VERSION}"
 WriteRegStr HKCU "${APPKEY}" "Publisher" "MOA"
 WriteRegStr HKCU "${APPKEY}" "InstallLocation" "$INSTDIR"
 WriteRegStr HKCU "${APPKEY}" "DisplayIcon" "$INSTDIR\MOA.exe"
 WriteRegStr HKCU "${APPKEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
 WriteRegStr HKCU "${APPKEY}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
 WriteRegDWORD HKCU "${APPKEY}" "NoModify" 1
 WriteRegDWORD HKCU "${APPKEY}" "NoRepair" 1
SectionEnd
Section "Uninstall"
 SetShellVarContext current
 IfFileExists "$INSTDIR\scripts\StopInstalled.ps1" 0 +4
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\StopInstalled.ps1"' $0
  IntCmp $0 0 +2
  Abort "실행 중인 MOA 창을 닫고 다시 제거해 주세요."
 !include "..\runtime\release-staging\uninstall-files.nsh"
 Delete "$SMPROGRAMS\MOA\모아.lnk"
 Delete "$SMPROGRAMS\MOA\모아 제거.lnk"
 RMDir "$SMPROGRAMS\MOA"
 Delete "$DESKTOP\모아.lnk"
 DeleteRegKey HKCU "${APPKEY}"
 Delete "$INSTDIR\Uninstall.exe"
 RMDir "$INSTDIR"
SectionEnd
