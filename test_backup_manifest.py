import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from app import Store
from data_backup import backup,restore


class BackupManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.archive=self.root/'backup.zip'
        backup(self.store.root,self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.entries={info.filename:archive.read(info) for info in archive.infolist()}
            self.manifest=json.loads(self.entries['manifest.json'])
    def replace_manifest(self,text):
        with zipfile.ZipFile(self.archive,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in self.entries.items():archive.writestr(name,text if name=='manifest.json' else data)
    def test_duplicate_json_keys_are_rejected_without_publishing(self):
        files=json.dumps(self.manifest['files']);meta=json.dumps(self.manifest['files']['state.sqlite3']);size=self.manifest['files']['state.sqlite3']['size'];sha=json.dumps(self.manifest['files']['state.sqlite3']['sha256'])
        cases=['{"version":2,"version":1,"files":'+files+'}',
               '{"version":1,"files":{"state.sqlite3":'+meta+',"state.sqlite3":'+meta+'}}',
               '{"version":1,"files":{"state.sqlite3":{"size":0,"size":'+str(size)+',"sha256":'+sha+'}}}']
        for index,text in enumerate(cases):
            with self.subTest(text=text):
                self.replace_manifest(text);original=self.archive.read_bytes()
                destination=self.root/f'restored-{index}'
                with self.assertRaisesRegex(ValueError,'중복'):restore(self.archive,destination)
                self.assertFalse(destination.exists());self.assertEqual(self.archive.read_bytes(),original)
    def test_boolean_manifest_version_is_not_version_one(self):
        value={**self.manifest,'version':True};self.replace_manifest(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'지원'):restore(self.archive,self.root/'restored')
        self.assertFalse((self.root/'restored').exists())
    def test_generated_manifest_still_roundtrips(self):
        restored=self.root/'restored';restore(self.archive,restored)
        with Store(restored).connect() as connection:self.assertEqual(connection.execute('SELECT count(*) FROM tasks').fetchone()[0],0)

