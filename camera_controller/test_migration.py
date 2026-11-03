import sqlite3
import tempfile
import unittest
from pathlib import Path
from controller import Controller

class MigrationTests(unittest.TestCase):
    def test_read_only_source_stable_ids_and_no_analytics_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'old.sqlite3'
            old=Controller(source,receive_port=0)
            with old.connection() as db:
                db.execute('INSERT INTO camera_paths VALUES(42,?,?,?,?,?)',('Test','wrld_00000000-0000-0000-0000-000000000001','[]','original.json',1))
                db.execute('CREATE TABLE private_visitors(name TEXT)')
                db.execute("INSERT INTO private_visitors VALUES('Do not migrate')")
            old.close()
            before=source.read_bytes()
            new=Controller(Path(tmp)/'new.sqlite3',receive_port=0)
            try:
                self.assertEqual(new.migrate(source),1)
                self.assertEqual(new.paths()[0]['id'],42)
                self.assertEqual(source.read_bytes(),before)
                with new.connection() as db:
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='private_visitors'").fetchone())
                with self.assertRaisesRegex(ValueError,'leere'):new.migrate(source)
            finally:new.close()
