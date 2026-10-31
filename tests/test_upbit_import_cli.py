"""CLI has an explicit network boundary and never overwrites user data."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import test_upbit_history as history

ROOT=Path(__file__).resolve().parents[1]


class ImportCLITests(unittest.TestCase):
    def setUp(self):
        path=ROOT/'tools/import_upbit_history.py'
        self.assertTrue(path.is_file(),'explicit history CLI is missing')
        spec=importlib.util.spec_from_file_location('market_import_cli',path)
        self.m=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.m)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.output=Path(self.tmp.name)/'market.json'

    def test_download_and_export_immutable_fixture(self):
        helper=history.HistoryTests('test_invalid_request_makes_no_network_call');helper.setUp()
        fixture=helper.fixture();client=Mock();client.download.return_value=fixture
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=self.m.main(['--market','KRW-BTC','--unit','60','--count','3','--output',str(self.output)],client=client)
        self.assertEqual(code,0)
        self.assertEqual(json.loads(self.output.read_text()),fixture)
        self.assertEqual(json.loads(out.getvalue())['bars'],3)
        self.assertFalse(json.loads(out.getvalue())['live_enabled'])

    def test_existing_file_is_rejected_before_network(self):
        self.output.write_text('private user data');client=Mock()
        with self.assertRaises(FileExistsError):
            self.m.main(['--market','KRW-BTC','--output',str(self.output)],client=client)
        client.download.assert_not_called();self.assertEqual(self.output.read_text(),'private user data')

    def test_endpoint_is_not_user_configurable(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            self.m.main(['--market','KRW-BTC','--output',str(self.output),'--url','http://evil.example'])
        self.assertEqual(error.exception.code,2)


if __name__=='__main__':unittest.main()
