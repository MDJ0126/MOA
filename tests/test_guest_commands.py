import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from app import Store
from vm_runtime import VM


class GuestCommandTests(unittest.TestCase):
    def test_terminal_reset_requires_evidence_of_a_previously_initialized_guest(self):
        import struct
        for previous in (b'',b'MOA_READY_previous\n'):
            with self.subTest(previous=previous):
                self.serial.write_bytes(previous)
                png=self.vm.folder/'fixture.png';png.write_bytes(b'\x89PNG\r\n\x1a\n'+b'\0'*8+struct.pack('>II',1280,800))
                actions=[]
                def action(value):
                    actions.append(value)
                    if value['action']=='text':
                        marker=re.search('MOA_READY_[a-f0-9]{32}',value['text'])[0]
                        with self.serial.open('ab') as stream:stream.write(marker.encode())
                with patch.object(self.vm,'capture',return_value=png),patch.object(self.vm,'action',side_effect=action),patch('vm_runtime.time.sleep'):
                    self.vm.prepare_guest_terminal(self.vm.session(),self.serial,self.tid)
                self.assertEqual(actions[0]['action'],'key' if previous else 'click')

    def test_shell_redirection_characters_use_correct_guest_keys(self):
        from unittest.mock import call
        events=[]
        with patch.object(self.vm,'qmp',side_effect=lambda name,args:events.extend(args['events'])),patch('vm_runtime.time.sleep'):
            self.vm.action({'action':'text','text':'<>'})
        pressed=[e['data']['key']['data'] for e in events if e['type']=='key' and e['data']['down'] and e['data']['key']['data']!='shift']
        self.assertEqual(pressed,['comma','dot'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent);self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name));self.vm=VM(self.store)
        self.tid=self.store.add('guest command probe','llama')
        self.serial=self.vm.folder/('serial-'+'a'*32+'.log');self.serial.write_bytes(b'old serial output\n')
        self.vm.session_file.write_text(json.dumps({'pid':123,'port':12345,'name':'owned-fixture','serial':self.serial.name,'config':{'iso':'runtime/vm-downloads/moa-desktop.iso'}}))
        self.vm.guest_terminal_session='owned-fixture'

    def test_observed_serial_stdout_and_exit_code_are_returned(self):
        typed=[]
        def action(value):
            if value['action']!='text':return
            typed.append(value['text'])
            if value['text'].endswith('\n'):
                line=''.join(typed);token=re.search('MOA_BEGIN_([a-f0-9]{32})',line)[1]
                with self.serial.open('ab') as stream:stream.write(('MOA_BEGIN_'+token+'\nactual fixture stdout\nMOA_END_'+token+':7\n').encode())
        with self.vm.exclusive(),patch.object(self.vm,'action',side_effect=action):
            result=self.vm.command(self.tid,'reviewer',"printf 'hello'; false")
        self.assertEqual(result['exit_code'],7);self.assertEqual(result['output'],'actual fixture stdout')
        self.assertIn('tee /dev/ttyS0',''.join(typed));self.assertNotIn('old serial output',result['output'])

    def test_unowned_or_cancelled_task_never_types_a_command(self):
        with patch.object(self.vm,'action') as action:
            with self.assertRaises(ValueError):self.vm.command(self.tid,'developer','echo no')
            with self.store.connect() as c:c.execute("UPDATE tasks SET status='cancelled' WHERE id=?",(self.tid,))
            with self.vm.exclusive(),self.assertRaises(ValueError):self.vm.command(self.tid,'developer','echo no')
            action.assert_not_called()

    def test_serial_timeout_is_not_replayed(self):
        clock=iter([0,0,40])
        with self.vm.exclusive(),patch.object(self.vm,'action') as action,patch('vm_runtime.time.monotonic',side_effect=lambda:next(clock)),patch('vm_runtime.time.sleep'):
            with self.assertRaisesRegex(ValueError,'시간이 초과'):self.vm.command(self.tid,'developer','echo once')
        typed=''.join(call.args[0]['text'] for call in action.call_args_list if call.args[0]['action']=='text')
        self.assertEqual(typed.count('echo once'),1)

    def test_shell_quoting_expansion_cannot_overflow_terminal_input(self):
        with self.vm.exclusive(),patch.object(self.vm,'action') as action:
            with self.assertRaisesRegex(ValueError,'입력 한도'):self.vm.command(self.tid,'developer',"echo "+"'"*160)
            action.assert_not_called()

    def test_unicode_command_and_untrusted_serial_path_are_rejected(self):
        with self.vm.exclusive(),patch.object(self.vm,'action') as action:
            with self.assertRaises(ValueError):self.vm.command(self.tid,'developer','echo 한글')
            record=json.loads(self.vm.session_file.read_text());record['serial']='../outside.log';self.vm.session_file.write_text(json.dumps(record))
            with self.assertRaises(ValueError):self.vm.command(self.tid,'developer','echo no')
            action.assert_not_called()
