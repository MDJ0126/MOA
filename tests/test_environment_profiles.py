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
        return dict({'os':'Windows','architecture':'AMD64','cpu_used_percent':0,'ram_gib':16,'available_ram_gib':10,'logical_cpus':12},**changes)

    def test_pressure_changes_recommendation_without_oversubscription(self):
        config={'target_os':'auto','location':'shared_vm','budget_percent':50}
        idle=recommend(config,self.host());self.assertEqual(idle['recommended'],'development')
        self.assertTrue(all(p['cpus']>=2 for p in idle['presets']))
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
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent) as temp:
            store=Store(temp);self.assertEqual(settings(store)['target_os'],'windows')
            saved=save(store,{'target_os':'linux','location':'remote','budget_percent':25,'expected_updated':0})
            self.assertEqual(settings(store),saved)
            with self.assertRaises(SettingsConflict):save(store,{'expected_updated':0})
            for bad in ({'target_os':[]},{'location':'unknown'},{'budget_percent':True},{'budget_percent':100},None):
                with self.assertRaises(ValueError):save(store,bad)
            self.assertEqual(settings(store),saved)

    def test_unavailable_preparation_targets_are_not_recommended(self):
        for change,profile in (({'architecture':'arm64'},{}),({}, {'target_os':'macos'}),({'os':'Unknown'},{})):
            with self.subTest(change=change,profile=profile):
                result=recommend(profile,self.host(**change))
                self.assertIsNone(result['recommended']);self.assertTrue(all(p['fits_now'] is not True for p in result['presets']))

    def test_unknown_cpu_load_or_architecture_holds_auto_selection(self):
        for changes in ({'cpu_used_percent':None},{'cpu_used_percent':float('nan')},{'architecture':None}):
            with self.subTest(changes=changes):self.assertIsNone(recommend({},self.host(**changes))['recommended'])

    def test_fractional_cpu_budget_does_not_round_up_to_a_whole_core(self):
        result=recommend({'target_os':'linux','budget_percent':25},self.host(logical_cpus=2))
        self.assertEqual(result['cpu_budget'],0);self.assertIsNone(result['recommended'])

    def test_zero_free_memory_is_known_insufficient_not_unknown(self):
        result=recommend({},self.host(available_ram_gib=0))
        self.assertEqual(result['memory_budget_mb'],0);self.assertTrue(all(p['fits_now'] is False for p in result['presets']))

    def test_supported_host_matrix_never_exceeds_measured_budgets(self):
        for os_name in ('Windows','Linux','Darwin'):
            for arch in ('AMD64','x86_64'):
                for target in ('windows','linux'):
                    for percent in (25,50,75):
                        for usage in (0,50,99,100):
                            result=recommend({'target_os':target,'budget_percent':percent},self.host(os=os_name,architecture=arch,cpu_used_percent=usage))
                            self.assertTrue(result['preparation_supported']);self.assertFalse(result['connected'])
                            if result['recommended']:
                                selected=next(p for p in result['presets'] if p['id']==result['recommended'])
                                self.assertTrue(selected['fits_now']);self.assertLessEqual(selected['cpus'],result['cpu_budget']);self.assertLessEqual(selected['memory_mb'],result['memory_budget_mb'])
                                self.assertLessEqual(selected['cpus'],int(12*percent/100))

    def test_inconsistent_ram_or_cpu_measurements_hold_recommendation(self):
        for changes in ({'available_ram_gib':17},{'logical_cpus':1.5},{'logical_cpus':True},{'cpu_used_percent':101}):
            with self.subTest(changes=changes):self.assertIsNone(recommend({},self.host(**changes))['recommended'])

    def test_memory_and_cpu_pressure_matrix_preserves_host_reserves(self):
        for free in (0,2,4,8,16):
            for cores in (1,2,4,12):
                for percent in (25,50,75):
                    for usage in (0,50,99,100):
                        with self.subTest(free=free,cores=cores,percent=percent,usage=usage):
                            result=recommend({'target_os':'linux','budget_percent':percent},self.host(available_ram_gib=free,logical_cpus=cores,cpu_used_percent=usage))
                            self.assertLessEqual(result['memory_budget_mb']/1024,max(0,free-2))
                            self.assertLessEqual(result['memory_budget_mb']/1024,16-6)
                            self.assertLessEqual(result['memory_budget_mb']/1024,16*percent/100)
                            self.assertLessEqual(result['cpu_budget'],int(cores*percent/100))
                            self.assertLessEqual(result['cpu_budget'],int(cores*(1-usage/100)))
                            if result['recommended']:
                                selected=next(p for p in result['presets'] if p['id']==result['recommended'])
                                self.assertLessEqual(selected['memory_mb'],result['memory_budget_mb'])
                                self.assertLessEqual(selected['cpus'],result['cpu_budget'])
                            else:self.assertFalse(any(p['fits_now'] is True for p in result['presets']))

if __name__=='__main__':unittest.main()
