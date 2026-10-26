import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from guest_agent import GuestAgent,compact_guest_preferences,compact_guest_actor,reviewer_command_error,explicit_review_command,compact_assigned_instructions
from organization import instruction
from setup_config import PRESETS
from leader_inbox import validate
from supervisor import prepare_vm

class GuestPerformanceTests(unittest.TestCase):
    def test_reviewer_cannot_rewrite_implementation_as_verification(self):
        self.assertTrue(reviewer_command_error("echo 'echo $(($1 + $2))' > add.sh && sh add.sh 2 3"))
        self.assertTrue(reviewer_command_error('rm add.sh'))
        self.assertFalse(reviewer_command_error('test "$(sh add.sh 2 3)" = 5 && test "$(sh add.sh -2 1)" = -1 && test "$(sh add.sh 0 0)" = 0 && echo REVIEW_PASS'))
        self.assertFalse(reviewer_command_error("printf '%s' '>'"))
    def test_user_skill_and_organization_rules_survive_roster_compaction(self):
        agents=copy.deepcopy(PRESETS[2]['agents']);text='사용자 원문 지침\n\n'+instruction(agents)
        preferences={'global_instructions':text,'skills':[{'name':'필수 기술','instructions':'보존할 전체 지침'}]}
        task={'workspace_preferences':preferences,'execution_plan':json.dumps({'agents':agents,'workspace_context':{'global_instructions':text}})}
        original=copy.deepcopy(task);result=compact_guest_preferences(task)
        self.assertIn('사용자 원문 지침',result['global_instructions']);self.assertIn('존댓말',result['global_instructions'])
        self.assertNotIn('조직 명부:',result['global_instructions']);self.assertEqual(result['skills'],preferences['skills']);self.assertEqual(task,original)
        agents[0]['capabilities'].append('developer');task['execution_plan']=json.dumps({'agents':agents,'workspace_context':{'global_instructions':text}})
        self.assertNotIn('조직 명부:',compact_guest_preferences(task)['global_instructions'])
    def test_unknown_roster_like_user_text_is_preserved(self):
        task={'workspace_preferences':{'global_instructions':'조직 명부: 직접 작성한 사용자 요구사항'},'execution_plan':json.dumps({'agents':PRESETS[2]['agents']})}
        self.assertEqual(compact_guest_preferences(task),task['workspace_preferences'])
    def test_actor_instructions_and_boundaries_are_preserved(self):
        actor={'id':'a','name':'담당','instructions':'전체 지침','responsibilities':'담당 업무','boundaries':'금지 범위','avatar':'large image URL'}
        result=compact_guest_actor(actor);self.assertNotIn('avatar',result)
        for key in ('instructions','responsibilities','boundaries'):self.assertEqual(result[key],actor[key])
    def test_noncode_request_for_a_different_reviewer_is_honored(self):
        agents=copy.deepcopy(PRESETS[2]['agents'])
        result=validate(agents,{'action':'execute','work_kind':'general','summary':'직접 수행','assignments':[]},{'title':'Create a file. A different reviewer must independently check it.'})
        owners={a['role']:a['agent_id'] for a in result['assignments']}
        self.assertNotEqual(owners['developer'],owners['reviewer'])
    def test_vm_preparation_disabled_has_no_side_effects(self):
        with patch('vm_runtime.VM') as vm:self.assertEqual(prepare_vm(Path.cwd(),{}),{'state':'disabled'});vm.assert_not_called()
    def test_vm_preparation_keeps_failure_visible_without_blocking_app(self):
        with patch('app.Store'),patch('vm_runtime.settings',return_value={'enabled':True}),patch('vm_runtime.VM') as vm:
            vm.return_value.start.side_effect=ValueError('RAM 부족')
            self.assertEqual(prepare_vm(Path.cwd(),{'prestart_vm':True})['state'],'failed')
    def test_command_failure_cannot_be_approved_by_finish(self):
        vm=Mock();vm.guest_history.return_value=[];vm.command.return_value={'command':'false','exit_code':1,'output':'failed'}
        def reply(action):return {'choices':[{'message':{'content':json.dumps(action)},'finish_reason':'stop'}]}
        actions=[{'tool':'command','command':'false','summary':'실제 실행','passed':False,'report':''},{'tool':'finish','command':'','summary':'잘못된 통과','passed':True,'report':''}]
        with patch('guest_agent.request_json',side_effect=[reply(a) for a in actions]):
            with self.assertRaisesRegex(ValueError,'실패한 VM 명령'):
                GuestAgent(vm,'model','http://127.0.0.1:18766').run({'id':'abcdef123456','title':'실제 코드 검사','agent':{'id':'a'},'work_kind':'code'},'reviewer',[],None,lambda *a:None)
    def test_legacy_mode_retains_original_prompt_and_context(self):
        vm=Mock();vm.guest_history.return_value=[];captured=[]
        def respond(request,timeout):
            captured.append(json.loads(request.data))
            return {'choices':[{'message':{'content':json.dumps({'tool':'finish','command':'','summary':'계획','passed':False,'report':''})},'finish_reason':'stop'}]}
        task={'id':'abcdef123456','title':'동일 요청','work_kind':'code','agent':{'id':'a','avatar':'original UI metadata'},'workspace_preferences':{'global_instructions':'original complete workspace'}}
        with patch('guest_agent.request_json',side_effect=respond):GuestAgent(vm,'model','http://127.0.0.1:18766',compact=False).run(task,'planner',[],None,lambda *a:None)
        self.assertIn('Choose tools by the GOAL',captured[0]['messages'][0]['content'])
        self.assertIn('original UI metadata',captured[0]['messages'][1]['content'])
    def test_research_verifier_is_not_offered_without_saved_research(self):
        from app import LocalFiles
        vm=Mock();vm.guest_history.return_value=[];vm.command.return_value={'exit_code':0,'output':'REVIEW_PASS','command':'echo REVIEW_PASS'}
        calls=[]
        actions=iter([{'tool':'command','command':'echo REVIEW_PASS','summary':'실제 검증','passed':False,'report':''},{'tool':'finish','command':'','summary':'실행 확인','passed':True,'report':''}])
        def respond(req,timeout):
            body=json.loads(req.data);calls.append(body)
            self.assertNotIn('verify_research',body['response_format']['json_schema']['schema']['properties']['tool']['enum'])
            return {'choices':[{'message':{'content':json.dumps(next(actions))},'finish_reason':'stop'}]}
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder,patch('guest_agent.request_json',side_effect=respond):
            result=GuestAgent(vm,'model','http://127.0.0.1:18766',compact=True).run({'id':'abcdef123456','title':'실제 코드 검증','agent':{'id':'checker'},'work_kind':'code'},'reviewer',[],LocalFiles(folder),lambda *a:None)
        self.assertTrue(result['passed']);self.assertEqual(len(calls),2)

    def test_literal_review_batch_is_exact_and_conservative(self):
        command='test "$(sh check.sh 7 -3)" = 4 && test "$(sh check.sh 0 0)" = 0 && echo CHECKED'
        title='Build it. A different reviewer must independently run '+command+'. Share evidence.'
        self.assertEqual(explicit_review_command(title),command)
        for bad in ['test "$(sh -c reboot)" = 0','echo PASS','test "$(sh check.sh; rm x)" = 4','test "$(sh check.sh)" = 4 > x','test "$(sh check.sh)" = 4 && echo $(touch x)']:
            self.assertIsNone(explicit_review_command('A different reviewer must independently run '+bad+'. Share evidence.'))
        self.assertIsNone(explicit_review_command(title+' '+title))

    def test_literal_review_runs_before_single_model_verdict(self):
        command='test "$(sh check.sh 7 -3)" = 4 && echo CHECKED'
        task={'id':'abcdef123456','title':'A different reviewer must independently run '+command+'. Share evidence.','agent':{'id':'checker'},'work_kind':'code'}
        for exit_code in (0,1):
            vm=Mock();vm.guest_history.return_value=[];vm.command.return_value={'command':command,'exit_code':exit_code,'output':'CHECKED' if not exit_code else ''}
            reply={'choices':[{'message':{'content':json.dumps({'tool':'finish','command':'','summary':'실제 결과 판정','passed':True,'report':''})},'finish_reason':'stop'}]}
            with patch('guest_agent.request_json',return_value=reply) as model:
                if exit_code:
                    with self.assertRaisesRegex(ValueError,'실패한 VM 명령'):GuestAgent(vm,'model','http://localhost',compact=True).run(task,'reviewer',[],None,lambda *a:None)
                else:self.assertTrue(GuestAgent(vm,'model','http://localhost',compact=True).run(task,'reviewer',[],None,lambda *a:None)['passed'])
                self.assertEqual(model.call_count,1);vm.command.assert_called_once_with(task['id'],'reviewer',command)
                body=json.loads(model.call_args.args[0].data)
                self.assertEqual(body['response_format']['json_schema']['schema']['properties']['tool']['enum'],['finish'])
                self.assertIn('CHECKED' if not exit_code else 'exit_code',body['messages'][-1]['content'])

    def test_only_exact_repeated_assignment_is_compacted(self):
        task={'title':'원문 전체','leader_instruction':'사용자와 리더의 추가 규칙\n[담당 단계 지시]\n원문 전체'}
        result=compact_assigned_instructions(task)
        self.assertIn('사용자와 리더의 추가 규칙',result);self.assertNotIn(task['title'],result)
        self.assertEqual(task['leader_instruction'],'사용자와 리더의 추가 규칙\n[담당 단계 지시]\n원문 전체')
        task['leader_instruction']+=' 추가 검증 필수'
        self.assertEqual(compact_assigned_instructions(task),task['leader_instruction'])
