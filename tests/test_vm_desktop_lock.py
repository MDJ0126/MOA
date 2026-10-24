from pathlib import Path
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from app import Store
from setup_config import write_setting
from vm_runtime import VM,BusyVM


class DesktopLockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.vm=VM(self.store);self.lock=self.vm.folder/'desktop.lock'
    def test_repeated_acquisition_does_not_grow_lock_file(self):
        for _ in range(4):
            with self.vm.exclusive():pass
        self.assertEqual(self.lock.read_bytes(),b'0')
    def test_contender_does_not_write_to_an_owned_lock(self):
        other=VM(self.store)
        self.lock.write_bytes(b'0');original=self.lock.read_bytes()
        with self.vm.exclusive():
            with self.assertRaises(BusyVM):
                with other.exclusive():self.fail('contender entered')
        self.assertEqual(self.lock.read_bytes(),original)
        with other.exclusive():pass
    def test_shared_lock_is_rejected_without_mutating_original(self):
        original=self.root/'original.bin';original.write_bytes(b'preserve this file');os.link(original,self.lock)
        with self.assertRaises(ValueError):
            with self.vm.exclusive():self.fail('shared file accepted as lock')
        self.assertEqual(original.read_bytes(),b'preserve this file');self.assertFalse(self.vm.thread_lock.locked())
    def test_status_probe_preserves_shared_file_and_unknown_owner(self):
        original=self.root/'original.bin';original.write_bytes(b'preserve this file');os.link(original,self.lock)
        owner={'task_id':'fixture','agent':'fixture','role':'developer'};write_setting(self.store,'vm_owner',owner)
        result=self.vm.status()
        self.assertEqual(original.read_bytes(),b'preserve this file')
        self.assertEqual(result['owner'],owner);self.assertEqual(result['state'],'unreachable');self.assertTrue(result['error'])
    def test_directory_lock_has_readable_failure_and_releases_thread_lock(self):
        self.lock.mkdir()
        with self.assertRaises(ValueError):
            with self.vm.exclusive():self.fail('directory accepted as lock')
        self.assertFalse(self.vm.thread_lock.locked())

    def test_opened_file_change_is_rejected_and_descriptor_closed(self):
        self.lock.write_bytes(b'preserved lock bytes');native=os.fstat;opened=[]
        def changed(fd):
            info=native(fd);opened.append(fd)
            return SimpleNamespace(st_mode=info.st_mode,st_nlink=2,st_file_attributes=0,st_dev=info.st_dev,st_ino=info.st_ino)
        with patch('vm_runtime.os.fstat',side_effect=changed):
            with self.assertRaises(ValueError):
                with self.vm.exclusive():self.fail('changed file accepted')
        with self.assertRaises(OSError):native(opened[0])
        self.assertEqual(self.lock.read_bytes(),b'preserved lock bytes');self.assertFalse(self.vm.thread_lock.locked())

    def test_reparse_metadata_is_rejected_before_open(self):
        self.lock.write_bytes(b'preserved lock bytes');native=Path.lstat
        def reparse(path):
            info=native(path)
            if path==self.lock:return SimpleNamespace(st_mode=info.st_mode,st_nlink=1,st_file_attributes=0x400)
            return info
        with patch.object(Path,'lstat',reparse),patch('vm_runtime.os.open',side_effect=AssertionError('must reject before open')):
            with self.assertRaises(ValueError):
                with self.vm.exclusive():self.fail('reparse file accepted')
        self.assertEqual(self.lock.read_bytes(),b'preserved lock bytes')
