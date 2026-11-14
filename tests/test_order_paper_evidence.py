"""Complete single-revision evidence; original SQLite account, not mock arithmetic."""
from decimal import Decimal as D
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import subprocess
import stat
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/core'))
from trading_core.order_manager import Order, OrderStatus, OrderSide, OrderType, OrderOutcomeUnknown
from trading_core.paper_session import PaperTradingSession
from trading_core.paper_session_observer import PaperSessionObserver


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'account.sqlite'
        self.output = self.root / 'observation.paper-evidence.json'
        self.session = PaperTradingSession(self.path, create=True, initial_balance='1000', fee_pct='0.1')
        await self.session.__aenter__()
        self.session.exchange.set_price('BTC/USDT', D('100'), timestamp=1700000000000)
        self.reader = PaperSessionObserver(self.path)

    async def asyncTearDown(self):
        await self.session.close()
        self.tmp.cleanup()

    def api(self):
        self.assertTrue(callable(getattr(self.reader, 'read_evidence', None)), 'complete evidence projection missing')
        return importlib.import_module('trading_core.paper_evidence')

    async def buy(self, key='buy-1'):
        return await self.session.manager.place_order('BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, D('2'), client_order_id=key)

    async def test_export_roundtrip_preserves_source_and_exact_account(self):
        await self.buy()
        api = self.api()
        before = self.path.read_bytes()
        report = api.export_evidence(self.reader, self.output)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(report['valid'])
        self.assertEqual(report['orders']['total'], 1)
        self.assertEqual(D(report['account']['cash']), D('799.8'))
        self.assertEqual(D(report['account']['total_fees']), D('0.2'))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        verified = api.verify_evidence(self.output)
        self.assertEqual(report['content_sha256'], verified['content_sha256'])
        self.assertEqual(report['account'], verified['account'])
        self.assertEqual(verified['scope'], 'offline_snapshot_not_current_account')
        self.assertFalse(verified['live_enabled'])

    async def test_lost_ack_remains_unknown_and_is_not_recharged(self):
        original = self.session.exchange.create_order
        async def lost(**kwargs):
            await original(**kwargs)
            raise TimeoutError('private transport error')
        with patch.object(self.session.exchange, 'create_order', side_effect=lost):
            with self.assertRaises(OrderOutcomeUnknown): await self.buy('lost')
        api = self.api()
        before = self.path.read_bytes()
        report = api.export_evidence(self.reader, self.output)
        saved = json.loads(self.output.read_text())
        self.assertEqual(saved['payload']['orders']['lost']['status'], 'unknown')
        self.assertEqual(report['orders']['unconfirmed_account_fills'], 1)
        self.assertEqual(report['orders']['unresolved'], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertNotIn('private transport error', self.output.read_text())

    async def test_existing_output_is_never_overwritten(self):
        api = self.api()
        self.output.write_bytes(b'keep')
        with self.assertRaises(api.PaperEvidenceWriteError): api.export_evidence(self.reader, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep')

    async def test_verify_does_not_need_original_database(self):
        await self.buy()
        api = self.api()
        api.export_evidence(self.reader, self.output)
        await self.session.close()
        self.path.unlink()
        with patch('sqlite3.connect', side_effect=AssertionError('offline verification opened database')):
            report = api.verify_evidence(self.output)
        self.assertEqual(report['orders']['account_fills'], 1)
        self.assertFalse(self.path.exists())



    def rewrite(self, change):
        from trading_core.order_state_store import _canonical, _digest
        data = json.loads(self.output.read_text())
        change(data)
        data['snapshot']['digest'] = _digest(_canonical(data['payload']))
        data['content_sha256'] = _digest(_canonical({k: v for k, v in data.items() if k != 'content_sha256'}))
        self.output.write_text(_canonical(data) + '\n')

    async def test_all_137_records_and_exact_quantities_survive_export(self):
        api = self.api()
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        qty = D('1.000000000000000000001')
        orders = {f'o-{i:04}': Order(f'o-{i:04}', 'BTC/USDT', OrderSide.BUY,
            OrderType.MARKET, qty, remaining=qty,
            status=OrderStatus.UNKNOWN if i % 2 else OrderStatus.SUBMITTING,
            created_at=when, updated_at=when) for i in range(137)}
        self.session._store.save(orders)
        report = api.export_evidence(self.reader, self.output)
        payload = json.loads(self.output.read_text())['payload']
        self.assertEqual(set(payload['orders']), set(orders))
        self.assertEqual(report['orders']['total'], 137)
        self.assertEqual(report['orders']['unresolved'], 137)
        self.assertEqual(payload['orders']['o-0000']['amount'], str(qty))
        self.assertEqual(payload['orders']['o-0000']['status'], 'submitting')

    async def test_optional_snapshot_anchors_reject_newer_source_without_output(self):
        api = self.api(); first = self.reader.read()
        self.session.exchange.set_price('BTC/USDT', D('101'), timestamp=1700000000001)
        with self.assertRaises(ValueError):
            api.export_evidence(self.reader, self.output,
                expected_revision=first['revision'], expected_digest=first['digest'])
        self.assertFalse(self.output.exists())

    async def test_partial_or_invalid_anchors_rejected_before_source_read(self):
        api = self.api()
        with patch('trading_core.paper_session_observer._read_row', side_effect=AssertionError('should not read')):
            for args in ({'expected_revision':'1'}, {'expected_digest':'0'*64},
                         {'expected_revision':1,'expected_digest':'0'*64}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    api.export_evidence(self.reader,self.output,**args)
        self.assertFalse(self.output.exists())

    async def test_new_commit_during_projection_does_not_mix_rows(self):
        api = self.api(); first = self.reader.read()
        original = api._make_evidence
        def intervening(*args):
            self.session.exchange.set_price('BTC/USDT', D('123'), timestamp=1700000000001)
            return original(*args)
        with patch.object(api,'_make_evidence',side_effect=intervening):
            report=api.export_evidence(self.reader,self.output)
        saved=json.loads(self.output.read_text())
        self.assertEqual(report['snapshot']['revision'],first['revision'])
        self.assertEqual(saved['payload']['book']['tickers']['BTC/USDT']['last'],'100')
        self.assertNotEqual(self.reader.read()['revision'],first['revision'])

    async def test_all_account_versions_and_krw_are_preserved(self):
        api=self.api()
        for version, options in [(1,{}),(2,{'max_order_notional':'200'}),(3,{'enable_control':True})]:
            path=self.root/f'v{version}.sqlite'; output=self.root/f'v{version}.json'
            async with PaperTradingSession(path,create=True,quote_currency='KRW',**options):
                before=path.read_bytes()
                report=api.export_evidence(PaperSessionObserver(path),output)
                self.assertEqual(report['snapshot']['session_version'],version)
                self.assertEqual(report['account']['quote_currency'],'KRW')
                self.assertEqual(path.read_bytes(),before)
                self.assertEqual(report['control']['enabled'],version==3)

    async def test_forged_balance_rejected_even_when_both_checksums_recomputed(self):
        await self.buy(); api=self.api(); api.export_evidence(self.reader,self.output)
        self.rewrite(lambda x:x['payload']['book']['balances'].__setitem__('USDT','12345'))
        with self.assertRaises(api.PaperEvidenceInvalid): api.verify_evidence(self.output)

    async def test_extra_secret_field_is_not_accepted_even_with_valid_checksums(self):
        api=self.api(); api.export_evidence(self.reader,self.output)
        self.rewrite(lambda x:x['payload'].__setitem__('api_key','do-not-copy'))
        with self.assertRaises(api.PaperEvidenceInvalid): api.verify_evidence(self.output)

    async def test_invalid_envelope_versions_dates_identity_and_flags(self):
        api=self.api(); api.export_evidence(self.reader,self.output); original=self.output.read_bytes()
        changes=[lambda x:x.__setitem__('format_version',True),
                 lambda x:x.__setitem__('live_enabled',True),
                 lambda x:x.__setitem__('captured_at','2026-02-30T00:00:00+00:00'),
                 lambda x:x['snapshot'].__setitem__('revision','01'),
                 lambda x:x['snapshot'].__setitem__('revision',str(2**62)),
                 lambda x:x['snapshot'].__setitem__('session_version',3),
                 lambda x:x['snapshot'].__setitem__('account_id','a'*32),
                 lambda x:x.__setitem__('unrecognized','value')]
        for change in changes:
            self.output.write_bytes(original);self.rewrite(change)
            with self.subTest(change=change),self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(self.output)

    async def test_malformed_duplicate_nonfinite_or_truncated_json_rejected(self):
        api=self.api();api.export_evidence(self.reader,self.output);original=self.output.read_bytes()
        for raw in (b'',original[:-5],b'{"x":1,"x":2}',b'{"x":NaN}',original+b'{}',b'\xff'):
            with self.subTest(raw=raw[:10]),self.assertRaises(api.PaperEvidenceInvalid):api.validate_evidence(raw)

    async def test_large_revision_is_kept_as_exact_string(self):
        api=self.api()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE paper_session SET revision=?',(2**53+7,));db.commit()
        report=api.export_evidence(self.reader,self.output)
        self.assertEqual(report['snapshot']['revision'],str(2**53+7))

    async def test_busy_observer_creates_no_file_and_can_retry_explicitly(self):
        api=self.api();self.reader._gate.acquire()
        try:
            with self.assertRaises(ValueError):api.export_evidence(self.reader,self.output)
        finally:self.reader._gate.release()
        self.assertFalse(self.output.exists())
        api.export_evidence(self.reader,self.output)

    async def test_missing_corrupt_source_never_publishes_empty_account(self):
        api=self.api()
        for path in [self.root/'missing.sqlite',self.root/'corrupt.sqlite']:
            if path.name.startswith('corrupt'):path.write_bytes(b'not a database');path.chmod(0o600)
            with self.assertRaises(ValueError):api.export_evidence(PaperSessionObserver(path),self.output)
            self.assertFalse(self.output.exists())
        self.assertFalse((self.root/'missing.sqlite').exists())

    async def test_file_replacement_at_projection_end_is_not_exported(self):
        api=self.api();original=api._make_evidence
        def replace(*args):
            copy=self.root/'replacement';copy.write_bytes(self.path.read_bytes());copy.chmod(0o600)
            os.replace(copy,self.path)
            return original(*args)
        with patch.object(api,'_make_evidence',side_effect=replace):
            with self.assertRaises(ValueError):api.export_evidence(self.reader,self.output)
        self.assertFalse(self.output.exists())

    async def test_source_path_and_credentials_are_not_exported(self):
        api=self.api()
        with patch.dict(os.environ,{'API_KEY':'private-env-key','ACCOUNT_NUMBER':'private-env-number'}):
            api.export_evidence(self.reader,self.output)
        text=self.output.read_text()
        for value in (str(self.path),'private-env-key','private-env-number'):
            self.assertNotIn(value,text)

    async def test_verification_rejects_missing_symlink_public_and_oversized_files(self):
        api=self.api()
        with self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(self.output)
        api.export_evidence(self.reader,self.output)
        alias=self.root/'link';alias.symlink_to(self.output)
        with self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(alias)
        self.output.chmod(0o644)
        with self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(self.output)
        self.output.chmod(0o600)
        with self.output.open('r+b') as f:f.truncate(api.MAX_EVIDENCE_BYTES+1)
        with self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(self.output)

    async def test_read_replacement_is_detected_even_with_valid_new_evidence(self):
        api=self.api();api.export_evidence(self.reader,self.output);original=api.validate_evidence
        def replace(raw):
            report=original(raw);other=self.root/'new';other.write_bytes(raw);other.chmod(0o600)
            os.replace(other,self.output);return report
        with patch.object(api,'validate_evidence',side_effect=replace):
            with self.assertRaises(api.PaperEvidenceInvalid):api.verify_evidence(self.output)

    async def test_existing_source_cannot_be_used_as_output(self):
        api=self.api();before=self.path.read_bytes()
        with self.assertRaises(api.PaperEvidenceWriteError):api.export_evidence(self.reader,self.path)
        self.assertEqual(self.path.read_bytes(),before)

    async def test_output_symlink_and_nonprivate_directory_rejected(self):
        api=self.api();target=self.root/'target';target.write_bytes(b'keep');self.output.symlink_to(target)
        with self.assertRaises(api.PaperEvidenceWriteError):api.export_evidence(self.reader,self.output)
        self.assertEqual(target.read_bytes(),b'keep')
        self.output.unlink();public=self.root/'public';public.mkdir(mode=0o755)
        with self.assertRaises(api.PaperEvidenceWriteError):api.export_evidence(self.reader,public/'out.json')
        self.assertEqual(list(public.iterdir()),[])

    async def test_temporary_name_collision_never_deletes_unowned_file(self):
        api=self.api();temp=self.root/('.paper-evidence-'+'a'*32+'.tmp');temp.write_bytes(b'keep-other-writer')
        with patch.object(api.secrets,'token_hex',return_value='a'*32):
            with self.assertRaises(api.PaperEvidenceWriteError):api.export_evidence(self.reader,self.output)
        self.assertTrue(temp.exists(),'another writer temporary file was deleted')
        self.assertEqual(temp.read_bytes(),b'keep-other-writer')

    async def test_failure_before_publication_cleans_owned_temporary_only(self):
        api=self.api();before=set(self.root.iterdir())
        with patch.object(api.os,'fsync',side_effect=OSError('private disk failure')):
            with self.assertRaises(api.PaperEvidenceWriteError) as ctx:api.export_evidence(self.reader,self.output)
        self.assertNotIsInstance(ctx.exception,api.PaperEvidencePublishedError)
        self.assertEqual(set(self.root.iterdir()),before)
        self.assertNotIn('private disk failure',str(ctx.exception))

    async def test_directory_sync_failure_preserves_complete_output_and_reports_uncertainty(self):
        api=self.api();original=api.os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('directory fsync failed')
            return original(fd)
        with patch.object(api.os,'fsync',side_effect=sync):
            with self.assertRaises(api.PaperEvidencePublishedError):api.export_evidence(self.reader,self.output)
        self.assertTrue(api.verify_evidence(self.output)['valid'])

    async def test_link_success_with_lost_reply_does_not_delete_output(self):
        api=self.api();original=api.os.link
        def link(*args,**kwargs):original(*args,**kwargs);raise OSError('reply lost')
        with patch.object(api.os,'link',side_effect=link):
            with self.assertRaises(api.PaperEvidencePublishedError):api.export_evidence(self.reader,self.output)
        self.assertTrue(api.verify_evidence(self.output)['valid'])

    async def test_racing_final_file_is_preserved(self):
        api=self.api();original=api.os.link
        def link(*args,**kwargs):self.output.write_bytes(b'winner');return original(*args,**kwargs)
        with patch.object(api.os,'link',side_effect=link):
            with self.assertRaises(api.PaperEvidenceWriteError):api.export_evidence(self.reader,self.output)
        self.assertEqual(self.output.read_bytes(),b'winner')

    async def test_keyboard_interrupt_is_propagated_without_partial_final(self):
        api=self.api()
        with patch.object(api.os,'link',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):api.export_evidence(self.reader,self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.paper-evidence-*.tmp')),[])

    async def test_cli_export_verify_and_no_clobber(self):
        self.api();tool=ROOT/'tools/paper_evidence.py'
        self.assertTrue(tool.exists(),'export/verify CLI missing')
        result=subprocess.run([sys.executable,'-B',str(tool),'export','--database',str(self.path),'--output',str(self.output)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stderr,'')
        self.assertTrue(json.loads(result.stdout)['valid'])
        result=subprocess.run([sys.executable,'-B',str(tool),'verify',str(self.output)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stderr,'')
        before=self.output.read_bytes()
        result=subprocess.run([sys.executable,'-B',str(tool),'export','--database',str(self.path),'--output',str(self.output)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_output_unavailable'})
        self.assertEqual(self.output.read_bytes(),before)
    async def test_unknown_without_fill_is_preserved_as_no_evidence_not_rejection(self):
        api=self.api()
        with patch.object(self.session.exchange,'create_order',side_effect=TimeoutError('private')):
            with self.assertRaises(OrderOutcomeUnknown):await self.buy('not-observed')
        report=api.export_evidence(self.reader,self.output)
        self.assertEqual(report['orders']['unresolved'],1)
        self.assertEqual(report['orders']['account_fills'],0)
        self.assertEqual(report['account']['cash'],'1000')
        self.assertEqual(json.loads(self.output.read_text())['payload']['orders']['not-observed']['status'],'unknown')

    def crash_export(self, after):
        code='''import os, sys
sys.path.insert(0,sys.argv[1]+'/packages/core')
from trading_core.paper_evidence import export_evidence
from trading_core.paper_session_observer import PaperSessionObserver
original=os.link
def die(*args,**kwargs):
    if sys.argv[4]=='after':original(*args,**kwargs)
    os._exit(86)
os.link=die
export_evidence(PaperSessionObserver(sys.argv[2]),sys.argv[3])
'''
        return subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT),str(self.path),str(self.output),'after' if after else 'before'],capture_output=True,text=True,timeout=10)

    async def test_process_death_before_publication_leaves_no_final_evidence(self):
        self.api();before=self.path.read_bytes();result=self.crash_export(False)
        self.assertEqual(result.returncode,86,result.stderr)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.path.read_bytes(),before)
        self.assertTrue(list(self.root.glob('.paper-evidence-*.tmp')))

    async def test_process_death_after_publication_leaves_verifiable_complete_evidence(self):
        api=self.api();await self.buy();before=self.path.read_bytes();result=self.crash_export(True)
        self.assertEqual(result.returncode,86,result.stderr)
        self.assertEqual(api.verify_evidence(self.output)['orders']['total'],1)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.output.stat().st_nlink,2)  # orphan temp is harmless for read-only verification

    async def test_two_processes_same_output_have_exactly_one_winner(self):
        api=self.api();before=self.path.read_bytes()
        command=[sys.executable,'-B',str(ROOT/'tools/paper_evidence.py'),'export','--database',str(self.path),'--output',str(self.output)]
        children=[subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        try:
            receipts=[p.communicate(timeout=15) for p in children]
            self.assertEqual(sorted(p.returncode for p in children),[0,2],receipts)
        finally:
            for p in children:
                if p.poll() is None:p.kill();p.communicate()
        self.assertTrue(api.verify_evidence(self.output)['valid'])
        self.assertEqual(self.path.read_bytes(),before)

    async def test_cli_missing_input_and_changed_revision_are_fixed_json_errors(self):
        self.api();tool=str(ROOT/'tools/paper_evidence.py')
        result=subprocess.run([sys.executable,'-B',tool,'verify',str(self.root/'private-missing')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_evidence_invalid'})
        anchor=self.reader.read();self.session.exchange.set_price('BTC/USDT',D('101'),timestamp=1700000000001)
        result=subprocess.run([sys.executable,'-B',tool,'export','--database',str(self.path),'--output',str(self.output),'--revision',anchor['revision'],'--digest',anchor['digest']],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,4);self.assertEqual(result.stdout,'')
        self.assertEqual(json.loads(result.stderr),{'error':'paper_session_changed'})
        self.assertFalse(self.output.exists())

    async def test_same_order_set_full_capture_has_stable_payload_digest(self):
        api=self.api();await self.buy();first=api.export_evidence(self.reader,self.output)
        second=api.export_evidence(self.reader,self.root/'second.json')
        self.assertEqual(first['snapshot'],second['snapshot'])
        self.assertEqual(first['account'],second['account'])
        self.assertEqual(first['orders'],second['orders'])
        self.assertNotEqual(first['captured_at'],second['captured_at'])
