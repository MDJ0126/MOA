using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Each site is a separate top-level WebView. No Slack tokens, DOM bridge,
// injected scripts or weakened frame/security headers are involved.
public class MoaDesktopWindow : Form {
    readonly TabControl tabs = new TabControl();
    readonly string root;
    string slackUrl;
    readonly bool smoke;
    readonly int dashboardPort;
    readonly Timer timer = new Timer();
    readonly WebView2[] views = new WebView2[2];
    readonly bool[] loaded = new bool[2];
    bool exitRequested;
    DateTime started = DateTime.UtcNow;
    public MoaDesktopWindow(string projectRoot, string messengerUrl, bool smokeTest, int port=8765) {
        root=projectRoot; slackUrl=messengerUrl; smoke=smokeTest;dashboardPort=port;
        Text="모아 · 로컬 에이전트 팀"; Width=1380; Height=940;
        MinimumSize=new Size(900,650); StartPosition=FormStartPosition.CenterScreen;
        Font=new Font("맑은 고딕",10); BackColor=Color.FromArgb(250,247,244);
        if(File.Exists(Path.Combine(root,"runtime/desktop/moa.ico"))) Icon=new Icon(Path.Combine(root,"runtime/desktop/moa.ico"));
        else using(var image=new Bitmap(Path.Combine(root,"docs/assets/moa-logo.png"))) {
            var handle=image.GetHicon();
            try { Icon=(Icon)Icon.FromHandle(handle).Clone(); } finally { Native.DestroyIcon(handle); }
        }
        tabs.Dock=DockStyle.Fill; tabs.Padding=new Point(24,10);
        tabs.TabPages.Add("MOA 워크스페이스"); tabs.TabPages.Add("Slack 메신저");
        Controls.Add(tabs);
        tabs.SelectedIndexChanged+=(s,e)=>InitializeTab(tabs.SelectedIndex);
        Shown+=(s,e)=>{InitializeTab(0);if(smoke)InitializeTab(1);};
        timer.Interval=500;timer.Tick+=(s,e)=>{
            if(File.Exists(Path.Combine(root,"data/desktop-close.request"))) {File.Delete(Path.Combine(root,"data/desktop-close.request"));exitRequested=true;Close();return;}
            if(File.Exists(Path.Combine(root,"data/desktop-open.request"))) {File.Delete(Path.Combine(root,"data/desktop-open.request"));Show();WindowState=FormWindowState.Normal;Activate();}
            if(smoke && ((loaded[0]&&loaded[1])||(DateTime.UtcNow-started).TotalSeconds>45)) {Log("smoke="+(loaded[0]&&loaded[1]?"PASS":"FAIL"));Close();}
        };timer.Start();
        FormClosing+=(s,e)=>{if(!smoke&&!exitRequested&&e.CloseReason==CloseReason.UserClosing){e.Cancel=true;Hide();}};
        FormClosed+=(s,e)=>{timer.Dispose();foreach(var view in views)if(view!=null)view.Dispose();};
    }
    void Log(string text) {
        try{File.AppendAllText(Path.Combine(root,"data/desktop.log"),DateTimeOffset.Now.ToString("o")+" "+text+Environment.NewLine);}catch{}
    }
    static bool IsSlack(Uri url) {return url.Scheme=="https"&&(url.Host.Equals("slack.com",StringComparison.OrdinalIgnoreCase)||url.Host.EndsWith(".slack.com",StringComparison.OrdinalIgnoreCase));}
    void OpenSlack(string url) {
        slackUrl=url;tabs.SelectedIndex=1;InitializeTab(1);
        if(views[1]!=null&&views[1].CoreWebView2!=null)views[1].CoreWebView2.Navigate(url);
    }
    async void InitializeTab(int index) {
        if(views[index]!=null)return;
        var view=new WebView2();views[index]=view;view.Dock=DockStyle.Fill;
        var profile=Path.Combine(root,"runtime/desktop-profile");
        view.CreationProperties=new CoreWebView2CreationProperties{UserDataFolder=profile};
        tabs.TabPages[index].Controls.Add(view);
        try {
            await view.EnsureCoreWebView2Async(null);
            var core=view.CoreWebView2;
            core.Settings.AreHostObjectsAllowed=false;
            core.Settings.IsWebMessageEnabled=false;
            core.Settings.IsPasswordAutosaveEnabled=false;
            core.Settings.IsGeneralAutofillEnabled=false;
            core.PermissionRequested+=(s,e)=>{ e.State=CoreWebView2PermissionState.Deny; };
            core.NavigationStarting+=(s,e)=>{
                Uri url;if(!Uri.TryCreate(e.Uri,UriKind.Absolute,out url)){e.Cancel=true;return;}
                if(url.Scheme!="https"&&url.Scheme!="http"&&e.Uri!="about:blank")e.Cancel=true;
                if(index==0 && !(url.Scheme=="http"&&url.Host=="127.0.0.1"&&url.Port==dashboardPort)) {
                    e.Cancel=true;
                    if(IsSlack(url)) OpenSlack(e.Uri);
                    else if(url.Scheme=="https")System.Diagnostics.Process.Start(e.Uri);
                }
            };
            core.NewWindowRequested+=(s,e)=>{
                e.Handled=true;Uri url;
                if(Uri.TryCreate(e.Uri,UriKind.Absolute,out url)&&index==0&&url.Scheme=="http"&&url.Host=="127.0.0.1"&&url.Port==dashboardPort&&url.AbsolutePath=="/api/artifact") {System.Diagnostics.Process.Start(e.Uri);return;}
                if(Uri.TryCreate(e.Uri,UriKind.Absolute,out url)&&url.Scheme=="https") {
                    if(index==0&&IsSlack(url))OpenSlack(e.Uri);
                    else if(index==1)core.Navigate(e.Uri);else System.Diagnostics.Process.Start(e.Uri);
                }
            };
            core.NavigationCompleted+=(s,e)=>{
                loaded[index]=e.IsSuccess;
                // Only record the origin; login links can contain sensitive query strings.
                Uri url;string origin=Uri.TryCreate(core.Source,UriKind.Absolute,out url)?url.GetLeftPart(UriPartial.Authority):"unknown";
                Log("tab="+index+" navigation="+e.IsSuccess+" http="+e.HttpStatusCode+" status="+e.WebErrorStatus+" origin="+origin);
            };
            core.Navigate(index==0?"http://127.0.0.1:"+dashboardPort+"/#home":slackUrl);
        } catch(Exception ex) {
            Log("tab="+index+" initialization failed: "+ex.GetType().Name+" "+ex.Message);
            var label=new Label{Dock=DockStyle.Fill,TextAlign=ContentAlignment.MiddleCenter,Text="화면을 열지 못했습니다. 다시 실행해 주세요.\n"+ex.Message};
            tabs.TabPages[index].Controls.Add(label);label.BringToFront();
        }
    }
    [STAThread]
    public static void Main(string[] args){
        string project=args.Length>0?args[0]:Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"../.."));
        if(args.Length==0&&File.Exists(Path.Combine(project,"MOA.exe"))){System.Diagnostics.Process.Start(Path.Combine(project,"MOA.exe"));return;}
        string url=args.Length>1?args[1]:"https://app.slack.com/";
        bool smoke=args.Length>2&&args[2]=="True";
        string hash;
        using(var sha=System.Security.Cryptography.SHA256.Create())hash=BitConverter.ToString(sha.ComputeHash(System.Text.Encoding.UTF8.GetBytes(project.ToLowerInvariant()))).Replace("-","").Substring(0,24);
        Native.SetCurrentProcessExplicitAppUserModelID("MOA.LocalAgentTeam."+hash);
        bool created;
        using(var mutex=new System.Threading.Mutex(true,"Local\\MOA-Desktop-"+hash+(smoke?"-QA":""),out created)){
            if(!created){File.WriteAllText(Path.Combine(project,"data/desktop-open.request"),"open");return;}
            try{File.Delete(Path.Combine(project,"data/desktop-close.request"));Run(project,url,smoke,args.Length>3?int.Parse(args[3]):8765);}
            finally{mutex.ReleaseMutex();}
        }
    }
    public static void Run(string root,string slackUrl,bool smoke,int port=8765){Application.EnableVisualStyles();Application.Run(new MoaDesktopWindow(root,slackUrl,smoke,port));}
    public class Native {
        [System.Runtime.InteropServices.DllImport("shell32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode)] public static extern int SetCurrentProcessExplicitAppUserModelID(string appID);
        [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr icon);
        [System.Runtime.InteropServices.DllImport("kernel32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode)] public static extern bool SetDllDirectory(string path);
    }
}
