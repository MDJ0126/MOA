import base64
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib
from app import Store
from setup_config import PRESETS,save_team
from office_chat import OfficeChat
import team_profiles

def png():
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',512,512,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xa0\xb0\xc0'*512)*512))+chunk(b'IEND',b'')

class OfficeChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);save_team(self.store,PRESETS[1]);self.chat=OfficeChat(self.store,'model','http://127.0.0.1:8766')

    def test_profiles_roundtrip_integrity_and_known_agent_only(self):
        unchanged=team_profiles.public(self.store)['checker']['image_hash']
        body=png();value=team_profiles.save(self.store,{'agent_id':'builder','image_base64':base64.b64encode(body).decode()})
        self.assertIn('checker',value);self.assertEqual(value['checker']['image_hash'],unchanged)
        self.assertEqual(team_profiles.image_path(Store(self.temp.name),'builder').read_bytes(),body)
        for data in ({'agent_id':'missing','image_base64':base64.b64encode(body).decode()},{'agent_id':'builder','image_base64':'not image'}):
            with self.assertRaises(ValueError):team_profiles.save(self.store,data)
        with self.assertRaises(ValueError):team_profiles.validate_png(body[:-12])

    def test_chat_is_separate_from_tasks_and_slack_and_replay_is_idempotent(self):
        value={'agent_id':'builder','content':'안녕하세요','request_key':'chat-test-1234'}
        with patch.object(self.chat,'start'):
            one=self.chat.send(value);two=self.chat.send(value)
            self.assertEqual(len(one['messages']),2);self.assertEqual(one,two)
            with self.assertRaises(ValueError):self.chat.send({**value,'content':'다른 내용'})
            with self.assertRaises(ValueError):self.chat.send({**value,'request_key':'second-key-1234'})
        self.assertEqual(self.chat.messages('checker')['messages'],[])
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)

    def test_real_backend_prompt_has_name_and_never_claims_tools(self):
        with patch.object(self.chat,'start'):self.chat.send({'agent_id':'builder','content':'역할이 무엇인가요?','request_key':'chat-test-1234'})
        captured=[]
        def respond(request,timeout):
            captured.append(json.loads(request.data));return {'choices':[{'message':{'content':'저는 지우입니다. 함께 계획하고 개발합니다.'},'finish_reason':'stop'}]}
        with patch('office_chat.request_json',side_effect=respond):self.chat.loop()
        messages=self.chat.messages('builder')['messages'];self.assertEqual(messages[-1]['state'],'completed');self.assertIn('지우',messages[-1]['content'])
        self.assertIn('NO tools',captured[0]['messages'][0]['content']);self.assertIn('지우',captured[0]['messages'][0]['content'])
        self.assertEqual(captured[0]['messages'][-1]['content'],'역할이 무엇인가요?')

    def test_error_and_restart_are_visible_without_automatic_replay(self):
        with patch.object(self.chat,'start'):self.chat.send({'agent_id':'builder','content':'hello','request_key':'chat-test-1234'})
        self.chat.recover();self.assertEqual(self.chat.messages('builder')['messages'][-1]['state'],'interrupted')
        with patch.object(self.chat,'start'):self.chat.send({'agent_id':'builder','content':'retry','request_key':'chat-test-5678'})
        with patch('office_chat.request_json',side_effect=RuntimeError('xoxb-secret')):self.chat.loop()
        failed=self.chat.messages('builder')['messages'][-1];self.assertEqual(failed['state'],'failed');self.assertNotIn('secret',failed['error'])

    def test_task_requires_explicit_delegate_and_keeps_recipient(self):
        chat=OfficeChat(self.store,'','http://127.0.0.1:0',True)
        value={'agent_id':'checker','content':'결과를 검증해 주세요.','request_key':'work-test-1234'}
        one=chat.delegate(value);two=chat.delegate(value);self.assertEqual(one['task_id'],two['task_id'])
        with self.store.connect() as c:plan=json.loads(c.execute('SELECT execution_plan FROM tasks').fetchone()[0])
        self.assertEqual(plan['assignment']['reviewer'],'checker');self.assertEqual(len(two['messages']),2)

if __name__=='__main__':unittest.main()
