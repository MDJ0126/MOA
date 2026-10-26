import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from app import Store,Worker
from setup_config import PRESETS,save_team,SettingsConflict
import workspace_resources as wr


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1]);self.manager=wr.Resources(self.store)
        self.patches=[patch.object(wr.shutil,'which',side_effect=lambda name:'C:/test/winget.exe' if name=='winget' else None),patch.object(wr,'installed_programs',return_value=[]),patch.object(wr,'engine_installs',return_value={})]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_guest_slack_is_orchestrated_without_install_request(self):
        task={'id':'task','execution_plan':json.dumps({'environment':{'guest_execution':True}})}
        with patch.object(self.manager,'request') as request:
            result=self.manager.bot_program(task,'slack','builder')
        request.assert_not_called();self.assertFalse(result['installation_required']);self.assertEqual(result['target'],'orchestrator')
        self.assertNotIn('program:slack',[p['id'] for p in self.manager.available(guest=True)['programs']])

    def allow(self,**values):return wr.save_policy(self.store,{**wr.policy(self.store),'auto_programs':True,'allowed_programs':['git','python'],'package_terms':True,'source_terms':True,**values})

    def test_free_cost_gate_blocks_conditional_and_unverified_packages(self):
        self.allow(allowed_programs=['unity','git'])
        self.assertEqual(self.manager.request('unity',actor='builder')['state'],'action_required')
        with patch.dict(wr.PROGRAM_BY_ID['git'],cost_kind='unknown'):
            self.assertEqual(self.manager.request('git')['state'],'action_required')

    def test_frozen_instructions_always_include_free_use_rule(self):
        self.assertIn(wr.FREE_USE_RULE,wr.frozen_context(self.store)['global_instructions'])
        self.assertEqual(wr.PROGRAM_BY_ID['affinity']['cost_kind'],'free_plan')

    def test_catalog_and_uninstalled_icons_are_available(self):
        value=self.manager.snapshot();self.assertEqual(len(value['programs']),15);self.assertEqual(len(value['skills']),8)
        self.assertFalse(any(p['installed'] for p in value['programs']))
        for p in value['programs']:self.assertTrue((Path('src/web/resource-icons')/(p['icon']+'.svg')).is_file())

    def test_inventory_uses_registry_evidence_not_catalog_name(self):
        with patch.object(wr,'installed_programs',return_value=[{'name':'Git','version':'2.0','publisher':'Git','evidence':'registry'}]):
            found=next(p for p in wr.inventory()['programs'] if p['id']=='git');self.assertTrue(found['installed']);self.assertEqual(found['version'],'2.0')

    def test_game_engine_catalog_distinguishes_launchers_from_editors(self):
        entries=[{'name':'Unity Hub','version':'3','evidence':'registry'},{'name':'Epic Games Launcher','version':'1','evidence':'registry'}]
        with patch.object(wr,'installed_programs',return_value=entries),patch.object(wr,'engine_installs',return_value={}):
            values={p['id']:p for p in wr.inventory()['programs']}
        self.assertTrue(values['unity-hub']['installed']);self.assertTrue(values['epic-launcher']['installed'])
        self.assertFalse(values['unity']['installed']);self.assertFalse(values['unreal']['installed'])
        self.allow(allowed_programs=['unity','unreal'])
        with patch.object(self.manager,'start') as launch:
            for key in ('unity','unreal'):
                self.assertEqual(self.manager.request(key)['state'],'action_required')
            launch.assert_not_called()
        self.assertIn('unity',wr.recommend(self.store,'유니티로 게임 제작')['programs'])
        self.assertIn('unreal',wr.recommend(self.store,'언리얼 게임 개발')['programs'])

    def test_bot_requires_allowlist_and_terms(self):
        job=self.manager.request('git','builder','task');self.assertEqual(job['state'],'approval_required')
        self.allow(package_terms=False)
        job=self.manager.request('git','builder','task');self.assertEqual(job['state'],'approval_required')
        with self.assertRaises(ValueError):self.manager.request('evil; curl x')

    def test_policy_conflict_preserves_existing_instructions(self):
        current=self.allow(global_instructions='근거를 남겨 주세요.')
        with self.assertRaises(SettingsConflict):wr.save_policy(self.store,{**current,'expected_updated':0,'global_instructions':'overwrite'})
        self.assertEqual(wr.policy(self.store)['global_instructions'],'근거를 남겨 주세요.')

    def test_policy_rejects_arbitrary_programs_and_non_booleans(self):
        for value in ({'allowed_programs':['curl https://evil']},{'auto_programs':'yes'},{'global_instructions':'a'*12001}):
            with self.assertRaises(ValueError):wr.save_policy(self.store,{**wr.policy(self.store),**value})

    def test_install_deduplicates_active_request(self):
        self.allow()
        with patch.object(self.manager,'start'):
            one=self.manager.request('git');two=self.manager.request('git')
        self.assertEqual(one['id'],two['id'])
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM resource_jobs').fetchone()[0],1)

    def test_install_exact_arguments_and_verified_result(self):
        self.allow();counter=[0]
        def state(refresh=False):
            counter[0]+=1
            return {'programs':[{'id':'git','installed':counter[0]>1}]}
        with patch.object(self.manager,'start'):job=self.manager.request('git')
        with patch.object(self.manager,'snapshot',side_effect=state),patch.object(wr.subprocess,'run',return_value=type('Result',(),{'returncode':0,'stdout':'ok','stderr':''})()) as run:
            self.manager.loop()
        args=run.call_args.args[0];self.assertIn('Git.Git',args);self.assertIn('--exact',args);self.assertIn('--scope',args);self.assertIn('user',args);self.assertNotIn('--force',args)
        self.assertNotIn('shell',run.call_args.kwargs)
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT state FROM resource_jobs WHERE id=?',(job['id'],)).fetchone()[0],'completed')

    def test_zero_exit_without_install_evidence_is_failure(self):
        self.allow()
        with patch.object(self.manager,'start'):job=self.manager.request('git')
        with patch.object(self.manager,'snapshot',return_value={'programs':[{'id':'git','installed':False}]}),patch.object(wr.subprocess,'run',return_value=type('R',(),{'returncode':0,'stdout':'','stderr':''})()):self.manager.loop()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT state FROM resource_jobs WHERE id=?',(job['id'],)).fetchone()[0],'failed')

    def test_restart_never_replays_unknown_install(self):
        self.allow()
        with patch.object(self.manager,'start'):job=self.manager.request('git')
        wr.Resources(self.store).recover()
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT state FROM resource_jobs WHERE id=?',(job['id'],)).fetchone()[0],'interrupted')

    def test_demo_never_installs(self):
        with self.assertRaises(ValueError):wr.Resources(self.store,True).request('git')

    def test_custom_skill_is_saved_assigned_and_frozen(self):
        self.allow(global_instructions='정중한 한국어')
        saved=wr.save_skill(self.store,{'name':'내 검증','description':'실제 검증','instructions':'실행 결과를 남깁니다.','agents':['checker']})
        tid=self.store.add('test frozen context','demo')
        with self.store.connect() as c:plan=json.loads(c.execute('SELECT execution_plan FROM tasks WHERE id=?',(tid,)).fetchone()[0])
        task={'execution_plan':json.dumps(plan),'agent':{'id':'builder'}}
        self.assertIn('정중한 한국어',wr.context(task)['global_instructions']);self.assertIn(wr.FREE_USE_RULE,wr.context(task)['global_instructions']);self.assertEqual(wr.context(task)['skills'],[])
        task['agent']['id']='checker';self.assertEqual(wr.context(task)['skills'][0]['instructions'],'실행 결과를 남깁니다.')
        wr.save_skill(self.store,{**saved,'instructions':'새 지침'})
        self.assertEqual(wr.context(task)['skills'][0]['instructions'],'실행 결과를 남깁니다.')

    def test_bot_skill_permission_and_assignment(self):
        task={'id':'abcd12345678'}
        outcome=self.manager.bot_skill(task,'quality-check','builder');self.assertIn('instructions',outcome)
        self.assertTrue(next(s for s in wr.skills(self.store) if s['id']=='quality-check')['installed'])
        wr.save_policy(self.store,{**wr.policy(self.store),'auto_skills':False})
        with self.assertRaises(wr.PendingResource):self.manager.bot_skill(task,'research','builder')
        with self.assertRaises(ValueError):self.manager.bot_skill(task,'nonexistent','builder')

    def test_invalid_skill_or_agent_is_rejected(self):
        with self.assertRaises(ValueError):wr.save_skill(self.store,{'name':'x','description':'d','instructions':'i','agents':['bad']})
        with self.assertRaises(ValueError):wr.save_skill(self.store,{'id':'../escape','name':'x','description':'d','instructions':'i'})

    def test_keywords_are_recommendations_only(self):
        value=wr.recommend(self.store,'Python으로 데이터 분석하고 QA 테스트')
        self.assertIn('python',value['programs']);self.assertIn('quality-check',value['skills'])
        with self.store.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM resource_jobs').fetchone()[0],0)

    def test_guest_network_off_does_not_install_on_host(self):
        self.allow();task={'id':'abcd12345678','execution_plan':json.dumps({'environment':{'kind':'vm','guest_execution':True}})}
        class VM:
            def session(self):return {'name':'session','config':{'network':False}}
        with patch.object(wr.subprocess,'run') as run:
            with self.assertRaisesRegex(wr.PendingResource,'네트워크'):self.manager.bot_program(task,'git','builder',VM())
            run.assert_not_called()

    def test_guest_scan_uses_actual_returned_paths(self):
        task={'id':'abcd12345678','agent':{'id':'builder'}}
        class VM:
            def session(self):return {'name':'guest-session','config':{'network':False}}
            def command(self,tid,role,command):
                self.command_text=command
                return {'exit_code':0,'output':'git:/usr/local/bin/git\npython:\n'}
        vm=VM();value=self.manager.guest_scan(task,vm,'developer')
        self.assertLessEqual(len(vm.command_text),1000)
        self.assertTrue(next(p for p in value['programs'] if p['id']=='git')['installed'])
        self.assertFalse(next(p for p in value['programs'] if p['id']=='python')['installed'])
        self.assertNotIn('tce-load',vm.command_text)

    def test_resource_wait_interrupts_task_without_spending_retry_budget(self):
        tid=self.store.add('prepare first','demo');worker=Worker(self.store)
        with patch('app.Demo.run',side_effect=wr.PendingResource('준비를 확인하세요.')):self.assertTrue(worker.tick())
        with self.store.connect() as c:row=c.execute('SELECT status,wait_reason,failures FROM tasks WHERE id=?',(tid,)).fetchone()
        self.assertEqual(tuple(row),('interrupted','resource_setup',0))

    def test_guest_model_can_request_skill_and_receives_instruction_content(self):
        from guest_agent import GuestAgent
        task={'id':'abcd12345678','agent':{'id':'builder','name':'지우','instructions':'개별 지침'},'title':'QA',
              'workspace_preferences':{'global_instructions':'전역 지침','skills':[]}}
        class VM:
            def guest_history(self,tid):return []
        vm=VM();agent=GuestAgent(vm,'model','http://127.0.0.1:8766');agent.resources=self.manager
        actions=[{'tool':'resource','command':'skill:quality-check','summary':'QA 지침을 준비합니다.','passed':False},
                 {'tool':'finish','command':'','summary':'실행 근거를 확인합니다.','passed':False}]
        payloads=[]
        def response(req,timeout):
            payloads.append(json.loads(req.data));return {'choices':[{'message':{'content':json.dumps(actions.pop(0))},'finish_reason':'stop'}]}
        with patch.object(self.manager,'guest_prepare'),patch('guest_agent.request_json',side_effect=response):result=agent.run(task,'planner',[],None,lambda *a:None)
        self.assertEqual(result['summary'],'실행 근거를 확인합니다.')
        self.assertIn('전역 지침',payloads[0]['messages'][1]['content']);self.assertIn('개별 지침',payloads[0]['messages'][1]['content'])
        self.assertIn('instructions',payloads[1]['messages'][-1]['content'])
        self.assertTrue(next(s for s in wr.skills(self.store) if s['id']=='quality-check')['installed'])


if __name__=='__main__':unittest.main()
