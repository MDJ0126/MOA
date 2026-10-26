import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store, checked_plan
from setup_config import PRESETS, save_team, team_settings
from prompt_refinement import refine, FIELDS


class PromptTests(unittest.TestCase):
    def test_source_and_unknown_fields_preserved(self):
        result=refine({'source':'교육 보고서를 작성해 주세요.','fields':{'tone':'정중하게'}}, {})
        self.assertIn('# 원문 요청\n교육 보고서를 작성해 주세요.',result['prompt'])
        self.assertIn('# 톤앤매너\n정중하게',result['prompt'])
        self.assertIn('미확인',result['prompt'])
        self.assertIn('모델 미사용',result['method'])

    def test_model_refines_but_explicit_fields_win(self):
        output={key:'원문에서 도출한 내용' for key in FIELDS}
        response={'choices':[{'message':{'content':json.dumps(output)},'finish_reason':'stop'}]}
        with patch('prompt_refinement.request_json',return_value=response) as request:
            result=refine({'source':'교육 보고서','mode':'llama','fields':{'tone':'정중하게'}},{},'test','http://127.0.0.1:8766')
        self.assertIn('# 톤앤매너\n정중하게',result['prompt'])
        self.assertIn('AI 첨삭',result['method'])
        self.assertIn('교육 보고서',json.loads(request.call_args.args[0].data)['messages'][1]['content'])

    def test_invalid_inputs_and_demo_boundary(self):
        for data in ({'source':None},{'source':'x','fields':[]},{'source':'x','fields':{'tone':1}}, {'source':'x','mode':'llama'}):
            with self.assertRaises(ValueError):refine(data,{},demo_only=True)

    def test_specialty_survives_save_assignment_and_frozen_plan(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as folder:
            store=Store(folder);team=copy.deepcopy(PRESETS[2]);team['agents'][1]['specialty']='교육과정 설계자'
            save_team(store,team)
            self.assertEqual(team_settings(store)['agents'][1]['specialty'],'교육과정 설계자')
            tid=store.add('교육과정을 설계하세요.','demo')
            with store.connect() as c:task=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            plan=checked_plan(task)
            self.assertEqual(next(a for a in plan['agents'] if a['id']==plan['assignment']['developer'])['specialty'],'교육과정 설계자')
            team['agents'][1]['specialty']='변경된 역할';save_team(store,team)
            self.assertEqual(plan['agents'][1]['specialty'],'교육과정 설계자')
