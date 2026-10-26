using System;
using System.Reflection;
using System.Runtime.InteropServices;
public static class MoaTrayNotifications {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
 public struct DATA {
  public uint cbSize;public IntPtr hWnd;public uint uID,uFlags,uCallbackMessage;public IntPtr hIcon;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=128)]public string szTip;
  public uint dwState,dwStateMask;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=256)]public string szInfo;
  public uint uTimeout;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=64)]public string szInfoTitle;
  public uint dwInfoFlags;public Guid guidItem;public IntPtr hBalloonIcon;
 }
 [DllImport("shell32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 [return:MarshalAs(UnmanagedType.Bool)]static extern bool Shell_NotifyIconW(uint message,ref DATA data);
 [DllImport("shell32.dll",CharSet=CharSet.Unicode)]public static extern int SetCurrentProcessExplicitAppUserModelID(string appID);
 public static DATA Create(object notifyIcon,string title,string body,IntPtr logo){
  var type=notifyIcon.GetType();var flags=BindingFlags.Instance|BindingFlags.NonPublic;
  var id=type.GetField("_id",flags)??type.GetField("id",flags);
  var window=type.GetField("_window",flags)??type.GetField("window",flags);
  if(id==null||window==null)throw new InvalidOperationException("트레이 알림 식별자를 확인하지 못했습니다.");
  var nativeWindow=window.GetValue(notifyIcon);var handle=(IntPtr)nativeWindow.GetType().GetProperty("Handle").GetValue(nativeWindow,null);
  return new DATA{cbSize=(uint)Marshal.SizeOf(typeof(DATA)),hWnd=handle,uID=Convert.ToUInt32(id.GetValue(notifyIcon)),uFlags=0x10,hIcon=logo,hBalloonIcon=logo,szTip="",szInfo=body.Length>255?body.Substring(0,255):body,szInfoTitle=title.Length>63?title.Substring(0,63):title,uTimeout=7000,dwInfoFlags=0x4|0x20};
 }
 public static void Show(object notifyIcon,string title,string body,IntPtr logo){
  if(logo==IntPtr.Zero)throw new InvalidOperationException("모아 알림 아이콘을 확인하지 못했습니다.");
  var data=Create(notifyIcon,title,body,logo);
  if(!Shell_NotifyIconW(1,ref data))throw new InvalidOperationException("모아 아이콘 알림 요청 실패: "+Marshal.GetLastWin32Error());
 }
}
