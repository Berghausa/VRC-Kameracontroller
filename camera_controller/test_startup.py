import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from controller import Controller,osc

W='wrld_00000000-0000-0000-0000-000000000001'
OTHER='wrld_00000000-0000-0000-0000-000000000002'

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.c=Controller(Path(self.temp.name)/'test.db',receive_port=0)
    def tearDown(self):
        self.c.close();self.temp.cleanup()

    def test_camera_preparation_uses_integer_mode_and_confirms_spout(self):
        sent=[]
        def send(address,value):
            sent.append((address,value))
            if address=='/usercamera/Mode':self.c.camera_mode=value
            if address=='/usercamera/Streaming':
                self.c.spout_active=value;self.c.camera_confirmed_at=time.time()
        self.c.send=send
        with patch.object(self.c.closed,'wait',return_value=False):self.c.prepare_camera()
        self.assertEqual(sent,[('/usercamera/Mode',2),('/usercamera/Streaming',True)])
        self.assertTrue(osc('/usercamera/Mode',2).endswith(b',i\0\0\0\0\0\x02'))

    def test_repeat_once_per_end_stop_cancels_and_world_change_disarms(self):
        c=self.c;c.world=lambda:W;c._repeat=True;c._repeat_world=W
        c.feedback_at=time.time();c._repeat_sent_at=time.time()-1
        c.playing=False;c._repeat_seen_play=True
        sent=[];c.send=lambda a,v:sent.append((a,v))
        c.repeat_tick();c.repeat_tick()
        self.assertEqual(sent,[('/dolly/Play',True)])
        c.stop();c.repeat_tick()
        self.assertEqual(sent[-1],('/dolly/Play',False));self.assertFalse(c._repeat)
        c._repeat=True;c.world=lambda:OTHER
        c.repeat_tick();self.assertFalse(c._repeat)
        self.assertEqual(len(sent),2)

    def test_slots_follow_current_world_and_reject_empty_slots(self):
        c=self.c
        with c.connection() as db:
            for ident,name,world in [(1,'Club A',W),(2,'Other Club',OTHER),(3,'Club B',W)]:
                db.execute('INSERT INTO camera_paths VALUES(?,?,?,?,?,?)',(ident,name,world,'[]','test.json',1))
        c.play=Mock();c.world=lambda:W
        c.play_slot(2);c.play.assert_called_with(3)
        c.world=lambda:OTHER
        c.play_slot(1);c.play.assert_called_with(2)
        with self.assertRaisesRegex(ValueError,'keine Fahrt'):c.play_slot(2)
        c.world=lambda:None
        with self.assertRaises(ValueError):c.play_slot(1)
