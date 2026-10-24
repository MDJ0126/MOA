import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from app import Store,Worker
from setup_config import PRESETS,save_team,team_settings,SettingsConflict


class _TeamRecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[0])
        self.tid=self.store.add('original frozen team task','demo',request_key='existing-task-request')
    def corrupt(self,raw):
        with self.store.connect() as connection:connection.execute("UPDATE settings SET value=? WHERE key='workspace'",(raw,))
    def raw(self):
        with self.store.connect() as connection:return connection.execute("SELECT value FROM settings WHERE key='workspace'").fetchone()[0]

class TeamRecoveryTests(_TeamRecoveryFixture):
    def test_read_returns_validated_fields_without_private_or_nonfinite_extras(self):
        value={**PRESETS[0],'max_revisions':'2','updated':123,'private_corrupt_note':'do not expose','unused':float('nan')}
        raw=json.dumps(value);self.corrupt(raw);team=team_settings(self.store)
        self.assertNotIn('private_corrupt_note',team);self.assertNotIn('unused',team)
        self.assertEqual(team['max_revisions'],2);self.assertEqual(team['updated'],123)
        json.dumps(team,allow_nan=False);self.assertEqual(self.raw(),raw)

    def test_invalid_preset_and_revision_are_recoverable_not_silently_coerced(self):
        for changes in ({'preset':float('nan')},{'preset':[]},{'max_revisions':True},{'max_revisions':2.5}):
            with self.subTest(changes=changes):
                self.corrupt(json.dumps({**PRESETS[0],**changes}));self.assertTrue(team_settings(self.store)['recovery_required'])

    def test_invalid_control_values_do_not_overwrite_valid_team(self):
        original=self.raw()
        for changes in ({'preset':float('nan')},{'preset':{}},{'max_revisions':False},{'max_revisions':1.5}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):save_team(self.store,{**PRESETS[0],**changes})
                self.assertEqual(self.raw(),original)

    def test_corruption_is_visible_without_replacing_original(self):
        for raw in ('{','[]','null','{}',b'\xff\x00',json.dumps({**PRESETS[0],'updated':'broken'})):
            with self.subTest(raw=repr(raw)):
                self.corrupt(raw);team=team_settings(self.store)
                self.assertTrue(team['recovery_required']);self.assertFalse(team['configured']);self.assertTrue(team['agents']);self.assertEqual(self.raw(),raw)
    def test_new_tasks_are_held_but_existing_frozen_work_can_finish(self):
        self.corrupt('{')
        with self.assertRaisesRegex(ValueError,'팀'):self.store.add('do not guess the team','demo')
        self.assertEqual(self.store.add('original frozen team task','demo',request_key='existing-task-request'),self.tid)
        Worker(self.store).tick()
        with self.store.connect() as connection:
            task=connection.execute('SELECT status,execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()
            self.assertEqual(task['status'],'completed');self.assertEqual(json.loads(task['execution_plan'])['agents'][0]['id'],'maker')
            self.assertEqual(connection.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
    def test_confirmed_save_archives_exact_original_and_recovers(self):
        raw=b'\xff\x00original team';self.corrupt(raw);team=team_settings(self.store)
        saved=save_team(self.store,{**PRESETS[1],'expected_updated':0,'recovery_token':team['recovery_token']})
        self.assertEqual(saved['agents'][0]['id'],'builder');self.assertNotIn('recovery_required',team_settings(self.store))
        with self.store.connect() as connection:self.assertEqual(connection.execute("SELECT value FROM settings_recovery_archive WHERE key='workspace'").fetchone()[0],raw)
        self.store.add('new recovered team task','demo')
    def test_missing_or_stale_recovery_token_cannot_overwrite(self):
        self.corrupt('{first');team=team_settings(self.store)
        with self.assertRaises(SettingsConflict):save_team(self.store,{**PRESETS[1],'expected_updated':0})
        self.corrupt('{second')
        with self.assertRaises(SettingsConflict):save_team(self.store,{**PRESETS[1],'expected_updated':0,'recovery_token':team['recovery_token']})
        self.assertEqual(self.raw(),'{second')

    def test_already_repaired_team_rejects_old_recovery_confirmation(self):
        self.corrupt('{');team=team_settings(self.store)
        save_team(self.store,{**PRESETS[1],'recovery_token':team['recovery_token'],'expected_updated':0})
        original=self.raw()
        with self.assertRaises(SettingsConflict):save_team(self.store,{**PRESETS[0],'recovery_token':team['recovery_token'],'expected_updated':0})
        self.assertEqual(self.raw(),original)

    def test_archive_failure_leaves_original_and_does_not_recover(self):
        self.corrupt('{');team=team_settings(self.store)
        with self.store.connect() as connection:
            connection.execute('CREATE TABLE settings_recovery_archive(id INTEGER PRIMARY KEY,key TEXT NOT NULL,value,token TEXT NOT NULL,created REAL NOT NULL)')
            connection.execute("CREATE TRIGGER reject_settings_archive BEFORE INSERT ON settings_recovery_archive BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):save_team(self.store,{**PRESETS[1],'recovery_token':team['recovery_token'],'expected_updated':0})
        self.assertEqual(self.raw(),'{')

    def test_recovery_archive_survives_backup_and_restore(self):
        from data_backup import backup,restore
        original=b'\xff original settings';self.corrupt(original);team=team_settings(self.store)
        save_team(self.store,{**PRESETS[1],'recovery_token':team['recovery_token'],'expected_updated':0})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as temporary:
            root=Path(temporary);backup(self.store.root,root/'backup.zip');restore(root/'backup.zip',root/'restored')
            restored=Store(root/'restored')
            with restored.connect() as connection:self.assertEqual(connection.execute('SELECT value FROM settings_recovery_archive').fetchone()[0],original)
            self.assertEqual(team_settings(restored)['agents'][0]['id'],'builder')
import json,socket,subprocess,sys,time,urllib.request,urllib.error
from app_lifecycle import request_stop
from soak import freeze

class TeamRecoveryHttpTests(_TeamRecoveryFixture):
    def test_http_recovery_holds_new_work_and_preserves_old_task_assignment(self):
        self.corrupt('{original corrupt team');self.store.control('pause')
        project=Path(self.temp.name)/'source';freeze(project)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        def request(path,payload=None):
            message=urllib.request.Request(f'http://127.0.0.1:{port}'+path,None if payload is None else json.dumps(payload).encode(),{'Content-Type':'application/json','X-Local-Agent':'1'})
            with urllib.request.urlopen(message,timeout=3) as response:return json.load(response,parse_constant=lambda value:self.fail('non-finite JSON response: '+value))
        with (Path(self.temp.name)/'server.log').open('wb') as log:
            process=subprocess.Popen([sys.executable,str(project/'app.py'),'--data',str(self.store.root),'--port',str(port)],stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(100):
                    try:request('/api/health');break
                    except OSError:
                        if process.poll() is not None:self.fail('isolated server exited')
                        time.sleep(.05)
                else:self.fail('isolated server startup timeout')
                setup=request('/api/setup');team=setup['team'];self.assertTrue(team['recovery_required']);self.assertEqual(self.raw(),'{original corrupt team')
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/tasks',{'title':'blocked new task','mode':'demo'})
                self.assertEqual(error.exception.code,400);error.exception.close()
                saved=request('/api/setup/team',{**PRESETS[1],'expected_updated':0,'recovery_token':team['recovery_token']})
                self.assertNotIn('recovery_required',saved)
                created=request('/api/tasks',{'title':'new recovered task','mode':'demo'})
                with self.store.connect() as connection:
                    self.assertEqual(connection.execute('SELECT value FROM settings_recovery_archive').fetchone()[0],'{original corrupt team')
                    old=connection.execute('SELECT execution_plan FROM tasks WHERE id=?',(self.tid,)).fetchone()[0]
                    new=connection.execute('SELECT execution_plan FROM tasks WHERE id=?',(created['id'],)).fetchone()[0]
                self.assertEqual(json.loads(old)['agents'][0]['id'],'maker');self.assertEqual(json.loads(new)['agents'][0]['id'],'builder')
                self.assertEqual(request('/api/health')['app']['pid'],process.pid)
                raw=json.dumps({**saved,'private_corrupt_note':'do not expose','unused':float('nan')});self.corrupt(raw)
                exposed=request('/api/setup')['team']
                self.assertNotIn('private_corrupt_note',exposed);self.assertNotIn('unused',exposed);self.assertEqual(exposed['updated'],saved['updated'])
                self.assertEqual(self.raw(),raw)
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/setup/team',{**saved,'preset':float('nan'),'expected_updated':saved['updated']})
                self.assertEqual(error.exception.code,400);error.exception.close();self.assertEqual(self.raw(),raw)
            finally:
                if process.poll() is None:
                    try:request_stop(project,port,expected_pid=process.pid);process.wait(timeout=10)
                    except Exception:process.terminate();process.wait(timeout=10)
