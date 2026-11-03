import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import updates

def release(tag='v99.0.0'):
    return {'tag_name':tag,'assets':[{'name':name,'browser_download_url':updates.PREFIX+tag+'/'+name} for name in (updates.INSTALLER,'SHA256SUMS.txt')]}

class UpdatesTests(unittest.TestCase):
    def test_stable_version_selection(self):
        self.assertIsNotNone(updates.release_info(release('v1.10.0'),'1.9.0'))
        self.assertIsNone(updates.release_info(release('v1.0.0'),'1.0.0'))
        data=release();data['prerelease']=True
        self.assertIsNone(updates.release_info(data))
        data=release();data['assets']=[]
        self.assertIsNone(updates.release_info(data))

    def test_external_download_rejected(self):
        data=release();data['assets'][0]['browser_download_url']='https://example.com/setup.exe'
        with self.assertRaises(ValueError):updates.release_info(data)

    def test_download_only_after_matching_checksum(self):
        info=updates.release_info(release())
        content=b'MZtest-installer'
        sums=f'{hashlib.sha256(content).hexdigest()}  {updates.INSTALLER}\n'.encode()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(updates,'fetch',side_effect=[sums,b'MZtampered']):
                with self.assertRaisesRegex(ValueError,'Prüfsumme'):updates.download(info,tmp)
            self.assertEqual(list(Path(tmp).iterdir()),[])
            with patch.object(updates,'fetch',side_effect=[sums,content]):
                path=updates.download(info,tmp)
            self.assertEqual(path.read_bytes(),content)
