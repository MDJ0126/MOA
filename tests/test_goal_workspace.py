import base64,hashlib,io,json,os,subprocess,tarfile,tempfile,unittest
from pathlib import Path
from app import Store,LocalFiles
from continuous_goals import Goals
from leader_inbox import LeaderInbox
import goal_workspace

class GoalWorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path.cwd());self.s=Store(Path(self.tmp.name).resolve());Goals(self.s,LeaderInbox(self.s,'',''),'','')
  self.old=self.s.add('original','demo');self.new=self.s.add('correction','demo');self.source=LocalFiles(self.s.root/'tasks'/self.old/'workspace');self.target=LocalFiles(self.s.root/'tasks'/self.new/'workspace')
  self.source.path('README.md').write_text('원본 내용',encoding='utf-8');self.digest=hashlib.sha256(self.source.path('README.md').read_bytes()).hexdigest()
  with self.s.connect() as c:
   c.execute("UPDATE tasks SET status='completed',verification=? WHERE id=?",(json.dumps({'passed':True,'source_hashes':{'README.md':self.digest}}),self.old))
   c.execute("INSERT INTO goal_items(id,goal_id,ordinal,title,task_id,state) VALUES('old','g',1,'old',?,'ready')",(self.old,))
   c.execute("INSERT INTO goal_items(id,goal_id,ordinal,title,request_id,parent_item,state) VALUES('new','g',2,'new','request','old','submitted')")
  self.plan={'leader_workflow':{'request_id':'request'},'environment':{'kind':'local'}}
 def tearDown(self):self.tmp.cleanup()
 def seed(self):goal_workspace.seed(self.s,{'id':self.new},self.plan,self.target,None,'developer')
 def test_verified_parent_copied_once_and_original_preserved(self):
  self.seed();self.assertEqual(self.target.path('README.md').read_bytes(),self.source.path('README.md').read_bytes())
  self.target.path('README.md').write_text('수정 결과',encoding='utf-8');self.seed()
  self.assertEqual(self.target.path('README.md').read_text(encoding='utf-8'),'수정 결과');self.assertEqual(self.source.path('README.md').read_text(encoding='utf-8'),'원본 내용')
 def test_changed_parent_is_rejected(self):
  self.source.path('README.md').write_text('changed',encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'검증 후 변경'):self.seed()
  self.assertFalse(self.target.path('README.md').exists())
 def test_existing_different_file_is_not_overwritten(self):
  self.target.path('README.md').write_text('사용자 작성',encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'기존 파일'):self.seed()
  self.assertEqual(self.target.path('README.md').read_text(encoding='utf-8'),'사용자 작성')
 @unittest.skipUnless(os.name=='nt','Windows PowerShell parser verification')
 def test_windows_copy_script_runs_in_powershell_with_fake_vm_transport(self):
  # Host PowerShell verifies script behavior; this is NOT an actual Windows VM test.
  data=self.source.path('README.md').read_bytes();archive=self.source.path('guest-workspace.tar.gz')
  with tarfile.open(archive,'w:gz') as tar:
   info=tarfile.TarInfo('README.md');info.size=len(data);tar.addfile(info,io.BytesIO(data))
  config={'guest_os':'windows','disk':'unit.qcow2'};environment={'kind':'vm','guest_execution':True,'config':config}
  self.plan['environment']=environment
  with self.s.connect() as c:c.execute('UPDATE tasks SET execution_plan=?,verification=? WHERE id=?',(json.dumps({'environment':environment}),json.dumps({'passed':True,'source_hashes':{'README.md':self.digest,'guest-workspace.tar.gz':hashlib.sha256(archive.read_bytes()).hexdigest()}}),self.old))
  class Fake:
   def command(fake,tid,role,command):
    source_prefix='C:\\MOA\\tasks\\'+self.old+'\\';command=command.replace(source_prefix,str(self.source.folder)+'\\')
    script="$ErrorActionPreference='Stop';Set-Location -LiteralPath '"+str(self.target.folder)+"';"+command
    r=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16le')).decode()],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    self.assertEqual(r.returncode,0,r.stderr.decode(errors='replace'))
    return {'exit_code':r.returncode,'output':r.stderr.decode(errors='replace')}
  goal_workspace.seed(self.s,{'id':self.new},self.plan,self.target,Fake(),'developer')
  self.assertEqual(self.target.path('README.md').read_bytes(),data)

if __name__=='__main__':unittest.main()
