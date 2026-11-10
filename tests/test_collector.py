import importlib.util
import pathlib
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector_test',ROOT/'modules/usage/collector.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)

class Hy2AccountingTests(unittest.TestCase):
    def test_hysteria_lowercase_login_keeps_canonical_identity(self):
        payload=collector.hy2_payload({'owner':{'tx':663,'rx':3535}}, {'Owner':{'counter_user':'California,Owner'}})
        self.assertEqual(payload,{'stat':[
            {'name':'user>>>California,Owner>>>traffic>>>uplink','value':663},
            {'name':'user>>>California,Owner>>>traffic>>>downlink','value':3535}]})
        state=collector.apply_samples({'users':{},'hy2_last_counters':{},'collector_generations':{}}, {'hy2':('generation1',payload)})
        self.assertEqual(state['users']['California,Owner'],{'uplink':663,'downlink':3535})
        again=collector.apply_samples(state,{'hy2':('generation1',payload)})
        self.assertEqual(again['users'],state['users'])

    def test_case_collisions_and_unknown_logins_are_rejected(self):
        mapping={'Owner':{'counter_user':'California,Owner'},'owner':{'counter_user':'California,Other'}}
        with self.assertRaises(ValueError):collector.hy2_payload({},mapping)
        with self.assertRaises(ValueError):collector.hy2_payload({'unknown':{'tx':1,'rx':2}}, {'Owner':{'counter_user':'California,Owner'}})

if __name__=='__main__':unittest.main()
