import json
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
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
    def test_deeply_nested_manifest_is_rejected_without_publishing(self):
        depth=max(20000,sys.getrecursionlimit()*20)
        self.replace_manifest('{"version":1,"files":{},"extra":'+'['*depth+'0'+']'*depth+'}')
        original=self.archive.read_bytes()
        with self.assertRaisesRegex(ValueError,'백업 목록'):restore(self.archive,self.root/'restored')
        self.assertFalse((self.root/'restored').exists());self.assertEqual(self.archive.read_bytes(),original)
    def test_generated_manifest_still_roundtrips(self):
        restored=self.root/'restored';restore(self.archive,restored)
        with Store(restored).connect() as connection:self.assertEqual(connection.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_inconsistent_file_metadata_is_rejected_before_payload_read(self):
        original_open=zipfile.ZipFile.open
        for changes in ({'size':0},{'size':self.manifest['files']['state.sqlite3']['size']+1},{'sha256':'invalid'}):
            with self.subTest(changes=changes):
                value=json.loads(json.dumps(self.manifest));value['files']['state.sqlite3'].update(changes)
                self.replace_manifest(json.dumps(value));payload_reads=[]
                def observed_open(archive,name,*args,**kwargs):
                    filename=name.filename if isinstance(name,zipfile.ZipInfo) else name
                    if filename.startswith('data/'):payload_reads.append(filename)
                    return original_open(archive,name,*args,**kwargs)
                with patch.object(zipfile.ZipFile,'open',observed_open):
                    with self.assertRaises(ValueError):restore(self.archive,self.root/'restored')
                self.assertEqual(payload_reads,[]);self.assertFalse((self.root/'restored').exists())

