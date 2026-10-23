import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch
from app import Store,Worker,Demo,checked_plan
from environment_profiles import save,resolve,identity,UnavailableEnvironment
from setup_config import SettingsConflict

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.store=Store(self.temp.name);self.worker=Worker(self.store)
    def tearDown(self):self.temp.cleanup()
    def task(self,tid):
        with self.store.connect() as c:return dict(c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
    def host(self):return save(self.store,{'location':'host','target_os':'auto'})

    def test_native_os_resolution_and_unconnected_targets(self):
        for osname in ('windows','linux','macos'):
            contract=resolve({'location':'host'},{'os':osname,'architecture':'test-arch'})
            self.assertEqual(contract['target_os'],osname)
            with self.assertRaises(UnavailableEnvironment):resolve({'location':'host','target_os':'linux' if osname!='linux' else 'windows'},{'os':osname,'architecture':'test-arch'})
        for location in ('shared_vm','remote'):
            save(self.store,{'location':location})
            with self.assertRaisesRegex(UnavailableEnvironment,'아직 연결'):self.store.add('not connected','demo',environment='profile')
        self.assertEqual(self.store.snapshot()['tasks'],[])

    def test_frozen_profile_survives_preference_changes_and_replay(self):
        saved=self.host();tid=self.store.add('frozen native','demo',environment='profile',expected_profile_updated=saved['updated'],request_key='routing-replay-001')
        save(self.store,{'location':'remote','target_os':'macos'})
        replay=self.store.add('frozen native','demo',environment='profile',expected_profile_updated=saved['updated'],request_key='routing-replay-001')
        self.assertEqual(replay,tid)
        self.worker.tick();task=self.task(tid);self.assertEqual(task['status'],'completed')
        report=json.loads((self.store.root/'tasks'/tid/'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['execution_environment']['execution_contract']['runner'],'host')
        self.assertFalse(report['verification']['execution_target']['execution_tests_performed'])
        self.assertIn('## 실행 대상',(self.store.root/'tasks'/tid/'report.md').read_text(encoding='utf-8'))

    def test_stale_setting_and_vm_actions_do_not_enqueue(self):
        self.host()
        with self.assertRaises(SettingsConflict):self.store.add('stale','demo',environment='profile',expected_profile_updated=0)
        with self.assertRaises(ValueError):self.store.add('wrong action','demo',environment='profile',vm_actions=[{'role':'developer','action':'capture'}])
        self.assertEqual(self.store.snapshot()['tasks'],[])

    def test_foreign_restored_host_holds_before_backend_and_next_task_runs(self):
        self.host();tid=self.store.add('restored foreign host','demo',environment='profile');next_id=self.store.add('ordinary host','demo')
        current=identity();current['architecture']='different-architecture'
        with patch('environment_profiles.identity',return_value=current),patch.object(Demo,'run',side_effect=AssertionError('must not run')):
            self.worker.tick()
        self.assertEqual(self.task(tid)['status'],'interrupted');self.assertIn('아키텍처',self.task(tid)['error'])
        self.assertFalse((self.store.root/'tasks'/tid/'workspace').exists())
        with patch('environment_profiles.identity',return_value=current):
            with self.assertRaises(UnavailableEnvironment):self.store.control('retry',tid)
            self.worker.recover();self.assertEqual(self.task(tid)['status'],'interrupted')
        self.worker.tick();self.assertEqual(self.task(next_id)['status'],'completed')
        self.store.control('retry',tid);self.worker.tick();self.assertEqual(self.task(tid)['status'],'completed')

    def test_corrupt_contract_never_reaches_backend(self):
        for change in ('version','target_os','requested','runner','kind','missing'):
            self.host();tid=self.store.add('corrupt '+change,'demo',environment='profile')
            plan=json.loads(self.task(tid)['execution_plan']);environment=plan['environment']
            if change=='kind':environment['kind']='vm';environment['config']={}
            elif change=='missing':del environment['execution_contract']
            else:environment['execution_contract'][change]=None
            with self.store.connect() as c:c.execute('UPDATE tasks SET execution_plan=? WHERE id=?',(json.dumps(plan),tid))
            with patch.object(Demo,'run',side_effect=AssertionError('corrupt contract ran')):self.worker.tick()
            self.assertEqual(self.task(tid)['status'],'interrupted')

    def test_real_python_checks_record_native_target_without_inference(self):
        self.host();tid=self.store.add('native Python verification','ollama',environment='profile')
        observed=[]
        class FakeModel:
            def __init__(self,*args):pass
            def run(self,task,role,history,files,log):
                observed.append(task['execution_environment']['execution_contract']['target_os'])
                if role=='developer':
                    source='import platform,unittest\nclass Native(unittest.TestCase):\n def test_native(self):self.assertEqual(platform.system(),'+repr(platform.system())+')\n'
                    files.execute({'tool':'write','path':'test_native.py','content':source},True)
                return {'summary':'fake model; actual Python test','passed':True}
        with patch('app.Ollama',FakeModel):self.worker.tick()
        task=self.task(tid);self.assertEqual(task['status'],'completed',task['error'])
        result=json.loads(task['verification']);self.assertEqual(result['tests_run'],1)
        self.assertTrue(result['execution_target']['execution_tests_performed'])
        self.assertEqual(result['execution_target']['target_os'],identity()['os'])
        self.assertEqual(observed,[identity()['os']]*4)

if __name__=='__main__':unittest.main()
