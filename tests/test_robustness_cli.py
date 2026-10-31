import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'apps/server'))
spec=importlib.util.spec_from_file_location('robustness_cli',ROOT/'tools/quant_research.py')
cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)

class RobustnessCLITests(unittest.TestCase):
    def test_opt_in_is_routed_separately_from_settings(self):
        with patch.object(cli,'export_research',return_value={'run_id':'a'*64,'summary':{'total_folds':1}}) as export,contextlib.redirect_stdout(io.StringIO()):
            try: result=cli.main(['fixture.json','--output','new.json','--robustness'])
            except SystemExit: self.fail('--robustness option is missing')
        self.assertEqual(result,0)
        self.assertTrue(export.call_args.kwargs['robustness'])
        self.assertNotIn('robustness',export.call_args.kwargs['settings'])

    def test_default_remains_opt_out(self):
        with patch.object(cli,'export_research',return_value={'run_id':'a'*64,'summary':{'total_folds':1}}) as export,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['fixture.json','--output','new.json']),0)
        self.assertFalse(export.call_args.kwargs.get('robustness',False))

if __name__=='__main__':unittest.main()
