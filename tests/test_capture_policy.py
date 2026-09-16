"""Default capture policy must not retain or submit unselected turns."""
import importlib
import json
import tempfile
import unittest
from unittest.mock import patch
import test_hermes_native  # Shared minimal host contract when Hermes is absent.


class CapturePolicyTests(unittest.TestCase):
    def provider(self, directory, **options):
        plugin = importlib.import_module('adapters.hermes')
        provider = plugin.BigfeelsMemoryProvider({'data_dir':directory, **options})
        provider.initialize('policy-session', platform='cli', agent_context='primary')
        self.addCleanup(provider.shutdown)
        return provider

    def capture(self, provider):
        provider.on_turn_start(1, 'Private user document')
        provider.sync_turn('Private user document', 'Private assistant summary', session_id='policy-session', messages=[
            {'role':'assistant','tool_calls':[{'id':'m365','function':{'name':'read_document'}}]},
            {'role':'tool','tool_call_id':'m365','content':'Private M365 document body'},
        ])

    def test_default_turn_capture_is_off_and_explicit_save_works(self):
        plugin = importlib.import_module('adapters.hermes')
        with tempfile.TemporaryDirectory() as td, patch.object(plugin, '_HostExtractor') as extractor:
            p = self.provider(td)
            try:
                self.capture(p)
                self.assertEqual(p._local.call('export', {})['evidence'], [])
                extractor.assert_not_called()
                saved = json.loads(p.handle_tool_call('bigfeels_remember', {'content':'Approved distilled preference'}))
                self.assertEqual(json.loads(p.handle_tool_call('bigfeels_inspect', {'id':saved['id']}))['content'], 'Approved distilled preference')
            finally:
                p.shutdown()

    def test_opted_in_capture_defaults_to_user_only_and_finite_retention(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.provider(td, capture_roles=['user'], auto_extract=False, evidence_retention_days=7)
            try:
                self.capture(p)
                records = p._local.call('export', {})['evidence']
                self.assertEqual([r['speaker'] for r in records], ['user'])
                self.assertTrue(all(r['expires_at'] for r in records))
                self.assertEqual(p._local.call('status', {})['queue']['pending'], 0)
            finally:
                p.shutdown()


if __name__ == '__main__':
    unittest.main()
