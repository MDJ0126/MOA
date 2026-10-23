import json
import socket
import threading
import unittest
from unittest.mock import MagicMock,patch
from vm_runtime import VM


class QMPTests(unittest.TestCase):
    def exchange(self,frames,clock=None):
        vm=VM.__new__(VM)
        sock=MagicMock();sock.__enter__.return_value=sock
        sock.recv.side_effect=[json.dumps(frame).encode()+b'\n' for frame in frames]
        with patch.object(vm,'session',return_value={'port':1,'name':'owned'}),patch('vm_runtime.socket.create_connection',return_value=sock),patch('vm_runtime.time.monotonic',side_effect=clock or (lambda:0)):
            return vm.qmp('query-status'),sock

    def test_normal_exchange_closes_socket(self):
        value,sock=self.exchange([{'QMP':{}},{'return':{}},{'return':{'name':'owned'}},{'event':'RESUME'},{'return':{'status':'running'}}])
        self.assertEqual(value,{'status':'running'})
        sock.__exit__.assert_called_once()
        sock.makefile.assert_not_called()

    def test_invalid_shapes_are_control_errors(self):
        for frames in ([[]],[{'QMP':{}},[]],[{'QMP':{}},{'error':None}],[{'QMP':{}},{'return':{}},{'return':[]}],
                       [{'QMP':{}},{'error':{'desc':['bad']}}]):
            with self.subTest(frames=frames),self.assertRaises(ValueError):self.exchange(frames)

    def test_events_do_not_extend_total_exchange_deadline(self):
        frames=[{'QMP':{}},{'event':'NOTICE'},{'event':'NOTICE'},{'return':{}},{'return':{'name':'owned'}},{'return':{'status':'running'}}]
        with self.assertRaisesRegex(ValueError,'시간'):
            self.exchange(frames,clock=iter([0,0,1,6,7,8,9]).__next__)

    def test_partial_bytes_do_not_extend_deadline(self):
        vm=VM.__new__(VM);sock=MagicMock();sock.__enter__.return_value=sock
        sock.recv.side_effect=[b'{',b'"QMP":{}',b'}\n']
        with patch.object(vm,'session',return_value={'port':1,'name':'owned'}),patch('vm_runtime.socket.create_connection',return_value=sock),patch('vm_runtime.time.monotonic',side_effect=[0,0,2,6]):
            with self.assertRaisesRegex(ValueError,'시간'):vm.qmp('query-status')
        self.assertEqual(sock.recv.call_count,2)
        sock.__exit__.assert_called_once()

    def test_oversized_unterminated_response_is_rejected(self):
        vm=VM.__new__(VM);sock=MagicMock();sock.__enter__.return_value=sock
        sock.recv.return_value=b'x'*65536
        with patch.object(vm,'session',return_value={'port':1,'name':'owned'}),patch('vm_runtime.socket.create_connection',return_value=sock):
            with self.assertRaisesRegex(ValueError,'너무 큽니다'):vm.qmp('query-status')
        self.assertEqual(sock.recv.call_count,16)

    def test_loopback_protocol_with_split_and_combined_frames(self):
        errors=[];commands=[]
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(3)
            def serve():
                try:
                    with listener.accept()[0] as peer:
                        peer.settimeout(3)
                        with peer.makefile('rb') as stream:
                            peer.sendall(b'{"QM');peer.sendall(b'P":{}}\n')
                            for response in (b'{"return":{}}\n',b'{"return":{"name":"owned"}}\n',b'{"event":"NOTICE"}\n{"return":{"status":"running"}}\n'):
                                commands.append(json.loads(stream.readline())['execute']);peer.sendall(response)
                except Exception as error:errors.append(error)
            server=threading.Thread(target=serve,daemon=True);server.start()
            vm=VM.__new__(VM)
            try:
                with patch.object(vm,'session',return_value={'port':listener.getsockname()[1],'name':'owned'}):
                    self.assertEqual(vm.qmp('query-status'),{'status':'running'})
            finally:server.join(timeout=4)
            self.assertFalse(server.is_alive());self.assertEqual(errors,[])
            self.assertEqual(commands,['qmp_capabilities','query-name','query-status'])


if __name__=='__main__':unittest.main()
