using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using System.Windows.Forms;

public class MoaLauncher {
    static readonly string Root=AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    static string Python {get{return Path.Combine(Root,"runtime/python/python.exe");}}
    static string Quote(string text){return "\""+text.Replace("\"","")+"\"";}
    static Process Child(string exe,string arguments,bool redirect){
        var start=new ProcessStartInfo(exe,arguments){WorkingDirectory=Root,UseShellExecute=false,CreateNoWindow=true};
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        start.RedirectStandardOutput=redirect;start.RedirectStandardError=redirect;return Process.Start(start);
    }
    static void Launch(){
        string shell=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"System32/WindowsPowerShell/v1.0/powershell.exe");
        Child(shell,"-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "+Quote(Path.Combine(Root,"Start-Tray.ps1")),false);
        Child(shell,"-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "+Quote(Path.Combine(Root,"src/desktop.ps1")),false);
    }
    static int RunCli(string arguments){using(var process=Child(Python,arguments,false)){process.WaitForExit();return process.ExitCode;}}
    [STAThread] public static int Main(string[] args){
        try{
            if(args.Length>0&&args[0]=="--verify")return RunCli(Quote(Path.Combine(Root,"src/distribution_setup.py"))+" --verify");
            if(args.Length>0&&args[0]=="--smoke")return Smoke();
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            if(!File.Exists(Python))throw new Exception("Python 실행 파일이 없습니다. MOA를 다시 설치해 주세요.");
            if(!File.Exists(Path.Combine(Root,"runtime/distribution.ready"))){Application.Run(new PrepareWindow());return 0;}
            using(var client=new System.Net.Sockets.TcpClient()){
                try{client.Connect("127.0.0.1",8765);if(!File.Exists(Path.Combine(Root,"runtime/app-8765.json")))throw new Exception("다른 위치의 MOA가 실행 중입니다. 기존 MOA를 종료한 뒤 실행해 주세요.");}
                catch(System.Net.Sockets.SocketException){}
            }
            Launch();return 0;
        }catch(Exception error){MessageBox.Show(error.Message,"MOA 실행",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
    static int Smoke(){
        Directory.CreateDirectory(Path.Combine(Root,"data"));
        var listener=new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback,0);listener.Start();int port=((System.Net.IPEndPoint)listener.LocalEndpoint).Port;listener.Stop();
        using(var backend=Child(Python,Quote(Path.Combine(Root,"src/app.py"))+" --demo --port "+port,false)){
            try{
                bool ready=false;
                for(int i=0;i<100;i++){if(backend.HasExited)break;try{using(var client=new System.Net.WebClient())client.DownloadString("http://127.0.0.1:"+port+"/api/state");ready=true;break;}catch{System.Threading.Thread.Sleep(200);}}
                if(!ready)throw new Exception("배포용 백엔드 실행 검증 실패");
                string icon=Path.Combine(Root,"runtime/desktop/MOA.exe");
                using(var desktop=Process.Start(new ProcessStartInfo(icon,Quote(Root)+" \"https://app.slack.com/\" True "+port){WorkingDirectory=Root,UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden})){
                    if(!desktop.WaitForExit(55000)){desktop.Kill();throw new Exception("관리 창 검증 시간 초과");}
                    if(desktop.ExitCode!=0)throw new Exception("관리 창 실행 실패");
                }
                string log=File.ReadAllText(Path.Combine(Root,"data/desktop.log"));if(!log.Contains("smoke=PASS"))throw new Exception("관리 창 로딩 검증 실패");
                File.WriteAllText(Path.Combine(Root,"data/distribution-smoke.json"),"{\"passed\":true,\"port\":"+port+",\"python\":\"bundled\",\"desktop\":\"native\"}");return 0;
            }finally{if(!backend.HasExited){try{RunCli(Quote(Path.Combine(Root,"src/stop_app.py"))+" --port "+port);if(!backend.WaitForExit(10000))backend.Kill();}catch{backend.Kill();}}}
        }
    }
    class PrepareWindow:Form {
        readonly TextBox log=new TextBox();readonly CheckBox vm=new CheckBox();readonly Button start=new Button();Process process;bool busy;
        public PrepareWindow(){
            Text="MOA 첫 실행 준비";Size=new Size(680,500);MinimumSize=Size;StartPosition=FormStartPosition.CenterScreen;Font=new Font("맑은 고딕",10);BackColor=Color.White;Icon=new Icon(Path.Combine(Root,"runtime/desktop/moa.ico"));
            var title=new Label{Text="팀과 함께 일할 준비를 합니다",AutoSize=true,Font=new Font("맑은 고딕",17,FontStyle.Bold),Location=new Point(25,24)};
            var detail=new Label{Text="무료 AI 모델(약 4.7GB)을 처음 한 번 다운로드합니다.\n인터넷 연결이 필요하며, 중단된 모델 다운로드는 이어받을 수 있습니다.",AutoSize=true,Location=new Point(27,75)};
            vm.Text="무료 VM 작업 환경도 함께 준비";vm.Checked=true;vm.AutoSize=true;vm.Location=new Point(27,132);
            log.Multiline=true;log.ReadOnly=true;log.ScrollBars=ScrollBars.Vertical;log.SetBounds(27,175,610,205);log.BackColor=Color.FromArgb(250,247,244);log.BorderStyle=BorderStyle.FixedSingle;
            start.Text="준비 시작";start.SetBounds(477,398,160,40);start.BackColor=Color.FromArgb(204,79,43);start.ForeColor=Color.White;start.FlatStyle=FlatStyle.Flat;start.Click+=Begin;
            Controls.AddRange(new Control[]{title,detail,vm,log,start});
            FormClosing+=(s,e)=>{if(busy){if(MessageBox.Show("준비를 중단하시겠습니까? 모델 다운로드는 다음에 이어받습니다.","MOA",MessageBoxButtons.YesNo)!=DialogResult.Yes){e.Cancel=true;return;}try{process.Kill();}catch{}}};
        }
        void Append(string text){if(IsDisposed)return;try{BeginInvoke((Action)(()=>{if(!IsDisposed)log.AppendText(text+Environment.NewLine);}));}catch{}}
        async void Begin(object sender,EventArgs args){
            busy=true;start.Enabled=false;vm.Enabled=false;string arguments=Quote(Path.Combine(Root,"src/distribution_setup.py"))+(vm.Checked?" --with-vm":"");
            try{
                process=Child(Python,arguments,true);process.OutputDataReceived+=(s,e)=>{if(e.Data!=null)Append(e.Data);};process.ErrorDataReceived+=(s,e)=>{if(e.Data!=null)Append(e.Data);};process.BeginOutputReadLine();process.BeginErrorReadLine();
                await Task.Run(()=>process.WaitForExit());if(process.ExitCode!=0)throw new Exception("준비를 완료하지 못했습니다. 위 내용을 확인하고 다시 시도해 주세요.");
                File.WriteAllText(Path.Combine(Root,"runtime/distribution.ready"),DateTime.UtcNow.ToString("o"));busy=false;Launch();Close();
            }catch(Exception error){busy=false;Append(error.Message);start.Enabled=true;vm.Enabled=true;start.Text="다시 시도";}
        }
    }
}
