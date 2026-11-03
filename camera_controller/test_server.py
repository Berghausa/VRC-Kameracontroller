import json
import tempfile
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from controller import Controller, api

class ServerTests(unittest.TestCase):
    def test_remote_mode_requires_key_and_preserves_local_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=Controller(Path(tmp)/'db.sqlite3',receive_port=0)
            server=None
            try:
                with self.assertRaises(ValueError):api(c,0,host='localhost')
                with self.assertRaises(ValueError):c.configure_server('8.8.8.8','192.168.1.12')
                token=c.configure_server('192.168.1.167','192.168.1.12')
                self.assertEqual(token,c.configure_server('192.168.1.167','192.168.1.12'))
                c.world=lambda:None
                server=api(c,0,host='localhost')
                url=f'http://127.0.0.1:{server.server_port}/api/status'
                for header in ({},{'Authorization':'Bearer wrong'}):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(url,headers=header))
                    self.assertEqual(error.exception.code,401)
                with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+token})) as response:
                    self.assertEqual(response.status,200)
                    self.assertNotIn('api_token',json.load(response))
            finally:
                if server:server.shutdown();server.server_close()
                c.close()
