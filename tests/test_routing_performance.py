import copy,json,unittest
from unittest.mock import patch
from leader_inbox import routing_context,LeaderInbox
from organization import instruction
from setup_config import PRESETS

class RoutingPerformanceTests(unittest.TestCase):
    def test_only_generated_roster_is_removed_and_semantics_preserved(self):
        agents=copy.deepcopy(PRESETS[2]['agents'])
        agents[0].update(instructions='사용자 지침 전문',avatar='large image URL',boundaries='업무 범위 전문')
        payload={'title':'원문 요청','agents':agents,'acceptance':'변경 금지 조건',
                 'recent_conversation':[{'role':'user','content':'이전 조건'}],
                 'workspace':{'global_instructions':'사용자 규칙\n\n'+instruction(agents),'skills':[{'instructions':'필수 기술 지침'}]}}
        original=copy.deepcopy(payload);result=routing_context(payload)
        self.assertEqual(payload,original)
        self.assertEqual(result['workspace']['global_instructions'],'사용자 규칙')
        for key in ('instructions','boundaries','responsibilities','capabilities','work_kinds','specialty'):
            if key in agents[0]:self.assertEqual(result['agents'][0][key],agents[0][key])
        for key in ('title','acceptance','recent_conversation'):self.assertEqual(result[key],payload[key])
        self.assertEqual(result['workspace']['skills'],payload['workspace']['skills'])
        self.assertNotIn('avatar',result['agents'][0])

    def test_custom_text_resembling_roster_is_not_deleted(self):
        payload={'agents':PRESETS[2]['agents'],'workspace':{'global_instructions':'조직 명부: 사용자 작성 내용'}}
        self.assertEqual(routing_context(payload)['workspace'],payload['workspace'])

    def test_real_request_keeps_recipient_and_schema(self):
        agents=PRESETS[2]['agents'];payload={'agents':agents,'title':'안녕하세요','recipient_id':agents[0]['id']}
        inbox=object.__new__(LeaderInbox);inbox.model='test';inbox.url='http://127.0.0.1:18766'
        def capture(req,timeout):
            body=json.loads(req.data);context=json.loads(body['messages'][1]['content'])
            self.assertEqual(context['recipient_id'],agents[0]['id'])
            self.assertEqual(body['response_format']['json_schema']['schema']['properties']['assignments']['items']['properties']['agent_id']['enum'],[a['id'] for a in agents if a['id']!=agents[0]['id']])
            return {'choices':[{'message':{'content':json.dumps({'action':'reply','summary':'인사','answer':'안녕하세요.'})},'finish_reason':'stop'}]}
        with patch('leader_inbox.request_json',side_effect=capture):self.assertEqual(inbox.decide(payload)['action'],'reply')
    def test_deliverable_schema_does_not_generate_an_unused_conversational_answer(self):
        agents=PRESETS[2]['agents'];payload={'agents':agents,'title':'파일 구현과 독립 검증','required_files':['result.sh']}
        inbox=object.__new__(LeaderInbox);inbox.model='test';inbox.url='http://127.0.0.1:18766'
        def capture(req,timeout):
            body=json.loads(req.data);props=body['response_format']['json_schema']['schema']['properties']
            self.assertEqual(props['answer']['enum'],['']);self.assertNotIn('reply',props['action']['enum'])
            self.assertEqual(props['summary']['maxLength'],80)
            self.assertEqual(props['refinement']['properties']['acceptance']['maxLength'],300)
            self.assertIn('complete original',body['messages'][0]['content'])
            self.assertEqual(props['assignments']['items']['properties']['instruction']['maxLength'],120)
            self.assertEqual(props['assignments']['maxItems'],2)
            self.assertNotIn('reporter',props['assignments']['items']['properties']['role']['enum'])
            self.assertEqual(json.loads(body['messages'][1]['content'])['title'],payload['title'])
            from leader_inbox import REFINEMENT_FIELDS
            refinement={key:('파일 구현과 독립 검증' if key=='goal' else '') for key in REFINEMENT_FIELDS}
            return {'choices':[{'message':{'content':json.dumps({'action':'execute','work_kind':'code','summary':'직접 구현과 독립 검증','answer':'','assignments':[],'refinement':refinement})},'finish_reason':'stop'}]}
        with patch('leader_inbox.request_json',side_effect=capture):self.assertEqual(inbox.decide(payload)['action'],'delegate')
    def test_explicit_reporter_remains_available(self):
        from leader_inbox import explicit_reporter_requested
        self.assertFalse(explicit_reporter_requested({'title':'코드 구현과 독립 검증 후 Slack으로 전달해 주세요.'}))
        self.assertTrue(explicit_reporter_requested({'title':'별도 보고 담당자도 배정해 주세요.'}))
        self.assertTrue(explicit_reporter_requested({'title':'Assign a separate reporter.'}))
