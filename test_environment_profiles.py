import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess
from setup_config import hardware,cpu_usage
from app import Store
from environment_profiles import recommend,save,settings
from setup_config import SettingsConflict

class EnvironmentProfilesTests(unittest.TestCase):
    def host(self,**changes):
        return dict({'os':'Windows','ram_gib':16,'available_ram_gib':10,'logical_cpus':12},**changes)

    def test_pressure_changes_recommendation_without_oversubscription(self):
        config={'target_os':'auto','location':'shared_vm','budget_percent':50}
        idle=recommend(config,self.host());self.assertEqual(idle['recommended'],'development')
        pressure=recommend(config,self.host(available_ram_gib=4));self.assertIsNone(pressure['recommended'])
        self.assertEqual(pressure['memory_budget_mb'],2048)
        self.assertTrue(all(p['fits_now'] is False for p in pressure['presets']))
        self.assertFalse(idle['connected'])

    def test_cpu_pressure_and_nonblocking_linux_samples(self):
        result=recommend({},self.host(cpu_used_percent=99));self.assertIsNone(result['recommended']);self.assertEqual(result['cpu_budget'],0)
        with patch('setup_config._cpu_sample',None),patch('setup_config.Path.read_text',side_effect=['cpu 10 0 10 80 0 0 0 0\n','cpu 30 0 20 150 0 0 0 0\n']):
            self.assertIsNone(cpu_usage('Linux'));self.assertEqual(cpu_usage('Linux'),30)
        self.assertIsNone(cpu_usage('Darwin'))

    def test_os_and_remote_are_independent_of_host(self):
        config={'target_os':'macos','location':'remote','budget_percent':75}
        result=recommend(config,self.host());self.assertEqual(result['target_os'],'macos')
        self.assertIsNone(result['recommended']);self.assertIsNone(result['memory_budget_mb'])
        self.assertIsNone(result['cpu_budget']);self.assertTrue(all(p['fits_now'] is None for p in result['presets']))
        self.assertIn('Mac',result['note'])

    def test_native_detection_and_unknown_measurements(self):
        for native,target in [('Windows','windows'),('Linux','linux'),('Darwin','macos')]:
            result=recommend({'target_os':'auto','location':'shared_vm','budget_percent':25},self.host(os=native,available_ram_gib=None))
            self.assertEqual(result['target_os'],target);self.assertIsNone(result['recommended'])
        result=recommend({'location':'host'},self.host());self.assertIsNone(result['recommended'])
        for bad in (float('nan'),-1,0):
            self.assertIsNone(recommend({},self.host(available_ram_gib=bad))['recommended'])

    def test_platform_memory_probes_and_missing_data(self):
        with patch('setup_config.platform.system',return_value='Linux'),patch('setup_config.Path.read_text',return_value='MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n'):
            measured=hardware();self.assertEqual(measured['ram_gib'],16);self.assertEqual(measured['available_ram_gib'],8);self.assertEqual(measured['memory_used_percent'],50)
        with patch('setup_config.platform.system',return_value='Linux'),patch('setup_config.Path.read_text',side_effect=OSError):
            self.assertIsNone(hardware()['ram_gib'])
        with patch('setup_config.platform.system',return_value='Darwin'),patch('setup_config.subprocess.run',return_value=subprocess.CompletedProcess([],0,str(16*2**30))):
            measured=hardware();self.assertEqual(measured['ram_gib'],16);self.assertIsNone(measured['available_ram_gib']);self.assertIsNone(measured['memory_used_percent'])

    def test_persist_cas_and_reject_invalid_inputs(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            store=Store(temp);self.assertEqual(settings(store)['target_os'],'auto')
            saved=save(store,{'target_os':'linux','location':'remote','budget_percent':25,'expected_updated':0})
            self.assertEqual(settings(store),saved)
            with self.assertRaises(SettingsConflict):save(store,{'expected_updated':0})
            for bad in ({'target_os':[]},{'location':'unknown'},{'budget_percent':True},{'budget_percent':100},None):
                with self.assertRaises(ValueError):save(store,bad)
            self.assertEqual(settings(store),saved)

if __name__=='__main__':unittest.main()
