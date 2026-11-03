import json
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from pathlib import Path
from controller import Controller, api, osc, decode, world_from_log, same_path

W = 'wrld_00000000-0000-0000-0000-000000000001'

class Tests(unittest.TestCase):
    def test_repeated_import_reuses_file_without_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(Path(tmp)/'test.db',receive_port=0)
            try:
                c.world=lambda:W
                row={'id':7,'payload':'[{"IsLocal":false,"Position":{"X":1}}]'}
                sent=[]
                c.send=lambda address,value:sent.append((address,value))
                with patch.object(c.closed,'wait',return_value=False):
                    c.import_path(row,W)
                    path=Path(sent[-1][1])
                    stamp=path.stat().st_mtime_ns
                    c.import_path(row,W)
                self.assertEqual(sent[0],sent[1])
                self.assertEqual(path.stat().st_mtime_ns,stamp)
                self.assertEqual([a for a,v in sent],['/dolly/Import','/dolly/Import'])
                self.assertEqual(len(list(path.parent.glob('*.json'))),1)
            finally:
                c.close()

    def test_offline_and_port_conflict_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            blocker.bind(('127.0.0.1',0))
            port=blocker.getsockname()[1]
            c=Controller(Path(tmp)/'offline.db',receive_port=port)
            try:
                self.assertIsNone(c.sock)
                self.assertTrue(c.transport_error)
                with patch('controller.subprocess.run') as process:
                    process.return_value.stdout='INFO: No tasks'
                    c.playing=True
                    c.osc_at=123
                    s=c.status()
                    self.assertEqual(s['connection_state'],'offline')
                    self.assertIsNone(s['playing'])
                    self.assertFalse(s['osc_connected'])
                blocker.close()
                import time
                deadline=time.monotonic()+4
                while c.sock is None and time.monotonic()<deadline:
                    time.sleep(.05)
                self.assertIsNotNone(c.sock)
                self.assertEqual(c.transport_error,'')
                c.world=lambda:W
                c.running=True
                c.query_at=time.time()
                c.playing=False
                s=c.status()
                self.assertTrue(s['osc_connected'])
                self.assertFalse(s['playing'])
                c.query_at=time.time()-30
                self.assertFalse(c.status()['osc_connected'])
            finally:
                blocker.close()
                c.close()

    def test_log_transitions(self):
        joining = f'Joining {W}:123\n'
        self.assertIsNone(world_from_log(joining))
        self.assertEqual(world_from_log(joining+'Successfully joined room'), W)
        self.assertIsNone(world_from_log(joining+'Successfully joined room\nOnLeftRoom'))

    def test_protocol(self):
        for value in (True, False):
            self.assertEqual(decode(osc('/dolly/Play',value)),('/dolly/Play',value))
        self.assertEqual(len(osc('/dolly/Import','ä')) % 4, 0)
        self.assertEqual(decode(osc('/dolly/Import','C:/Pfad/ä.json')),('/dolly/Import','C:/Pfad/ä.json'))
        self.assertTrue(same_path([{'X':1.0}],[{'X':1.0000001}]))
        self.assertFalse(same_path([{'X':1.0}],[{'X':2.0}]))
        packet=osc('/usercamera/Pose')[:-4]+b',ffffff\0'+struct.pack('>6f',1,2,3,10,20,30)
        self.assertEqual(decode(packet),('/usercamera/Pose',[1,2,3,10,20,30]))

    def test_failed_import_never_starts_old_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(Path(tmp)/'test.db',receive_port=0)
            try:
                source=Path(tmp)/'source.json'
                source.write_text('[{"IsLocal":false,"Position":{"X":1}}]')
                ident=c.register('Broken import',source,W)
                c.configure(9000)
                c.world=lambda:W
                sent=[]
                def send(address,value=None):
                    sent.append((address,value))
                    c.playing=False
                    import time
                    c.feedback_at=time.time()
                c.send=send
                with patch.object(c,'import_path',side_effect=ValueError('Import nicht bestätigt')):
                    with self.assertRaisesRegex(ValueError,'nicht bestätigt'):
                        c.play(ident)
                self.assertNotIn(('/dolly/Play',True),sent)
                self.assertIsNone(c.active)
                self.assertEqual(c.load_state,'failed')
            finally:
                c.close()

    def test_world_guard_and_osc_and_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Controller(Path(tmp)/'test.db', receive_port=0)
            server = api(c,0)
            receiver = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            receiver.bind(('127.0.0.1',0))
            receiver.settimeout(3)
            try:
                c.configure(receiver.getsockname()[1])
                source = Path(tmp)/'path.json'
                original='[{"IsLocal":false,"Position":{"X":1,"Y":2,"Z":3},"Index":0,"PathIndex":0}]'
                source.write_text(original)
                ident = c.register('Test',source,W)
                c.world = lambda: None
                with self.assertRaisesRegex(ValueError,'gesperrt'):
                    c.play(ident)
                c.world = lambda: W
                packets = []
                def vrchat():
                    for _ in range(3):
                        packet, peer = receiver.recvfrom(65535)
                        packets.append(packet)
                        if decode(packet)[0] == '/dolly/Play':
                            receiver.sendto(packet,peer)
                        if decode(packet)[0] == '/dolly/Import':
                            loaded=Path(decode(packet)[1]).read_text(encoding='utf-8')
                            self.assertEqual(json.loads(loaded),json.loads(original))
                worker = threading.Thread(target=vrchat)
                worker.start()
                base = f'http://127.0.0.1:{server.server_port}'
                with urllib.request.urlopen(urllib.request.Request(base+f'/api/paths/{ident}/play',method='POST')) as response:
                    self.assertEqual(response.status,200)
                worker.join(4)
                self.assertEqual([decode(p)[0] for p in packets],['/dolly/Play','/dolly/Import','/dolly/Play'])
                self.assertTrue(decode(packets[1])[1].endswith('path-1.json'))
                with urllib.request.urlopen(base+'/api/status') as response:
                    self.assertTrue(json.load(response)['paths'][0]['available'])
                c.world = lambda: 'other'
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base+f'/api/paths/{ident}/play',method='POST'))
                self.assertEqual(error.exception.code,409)
                self.assertEqual(source.read_text(),original)
                self.assertEqual(list(Path(tmp).glob('VRChat_CameraPath*.json')),[])
                self.assertEqual(len(list((Path(tmp)/'camera_runtime').glob('*.json'))),1)
            finally:
                server.shutdown()
                server.server_close()
                receiver.close()
                c.close()

if __name__ == '__main__':
    unittest.main()
