import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from local_model import validate_review,Llama

class ReviewTests(unittest.TestCase):
 def check(self,passed=True):return {'criterion':'한국어 사용법 3줄','evidence':'README.md: - 단계 1 / 2 / 3','passed':passed}
 def test_grounded_consistent_verdict(self):
  validate_review({'passed':True,'checks':[self.check()]},{'passed':True})
  validate_review({'passed':False,'checks':[self.check(False)]},{'passed':True})
 def test_task_restatement_without_checks_cannot_trigger_revision(self):
  with self.assertRaises(ValueError):validate_review({'passed':False,'summary':'README를 수정해야 합니다.'},{'passed':True})
 def test_contradictory_verdict_is_not_accepted(self):
  for passed,check in ((False,self.check()),(True,self.check(False))):
   with self.assertRaisesRegex(ValueError,'모순'):validate_review({'passed':passed,'checks':[check]},{'passed':True})
 def test_failed_real_verification_cannot_pass(self):
  with self.assertRaisesRegex(ValueError,'실패'):validate_review({'passed':True,'checks':[self.check()]},{'passed':False})
 def test_inconsistent_model_is_rechecked_with_actual_evidence(self):
  from app import LocalFiles
  with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
   files=LocalFiles(folder);files.execute({'tool':'write','path':'README.md','content':'# 안내\n- 하나\n- 둘\n- 셋'},True)
   outputs=[{'summary':'근거와 판정 모순','passed':False,'checks':[self.check()]},{'summary':'현재 파일 대조 완료','passed':True,'checks':[self.check()]}]
   replies=[{'choices':[{'message':{'content':json.dumps(o)},'finish_reason':'stop'}]} for o in outputs]
   history=[{'agent':'developer','content':json.dumps({'summary':'과거 추측 의견'})}]
   with patch('local_model.request_json',side_effect=replies) as model:
    result=Llama('test','http://127.0.0.1:1').run({'title':'한국어 3개 항목','verification':{'passed':True}},'reviewer',history,files,lambda *args:None)
   self.assertTrue(result['passed']);self.assertEqual(model.call_count,2)
   context=json.loads(json.loads(model.call_args_list[0].args[0].data)['messages'][1]['content'])
   self.assertFalse(context['handoffs']);self.assertIn('안내',context['actual_files'][0]['content'])

if __name__=='__main__':unittest.main()
