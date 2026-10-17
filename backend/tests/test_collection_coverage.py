import asyncio
import unittest
from unittest.mock import patch
from backend.scratchpad import InvestigationScratchpad
from backend.orchestrator import build_options
from backend.collection_coverage import CollectionCoverage, CollectionIncomplete

P='mcp__osint__'
TOOLS=[P+n for n in ('search_publications_crossref','search_username_sherlock','search_username_maigret','inspect_domain_dns','inspect_domain_archive','inspect_domain_theharvester')]

class CoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events=[]
        self.pad=InvestigationScratchpad('case',self.events.append)
        self.gate=CollectionCoverage(self.pad,TOOLS)

    def evidence(self,tool,args,data):
        self.pad.capture(tool,str(len(self.pad.raw_evidence)),data,args)

    async def test_stop_requires_real_calls_for_sourced_handle_and_domain(self):
        self.evidence(P+'inspect_github_developer_profile',{'github_handle':'some-builder'}, {'profile':{'blog':'https://builder.dev'}})
        result=await self.gate.stop({},None,None)
        self.assertEqual(result['decision'],'block')
        self.assertEqual({r['tool'] for r in self.gate.pending()},set(TOOLS))
        self.assertIn('builder.dev',result['reason'])
        self.assertIn('some-builder',result['reason'])

    async def test_receipts_not_narrative_or_started_calls_satisfy_gate(self):
        self.evidence(P+'inspect_github_developer_profile',{'github_handle':'abc'},{'profile':{'login':'abc'}})
        self.gate.started('one',P+'search_username_sherlock',{'username':'abc'})
        self.assertTrue(any(r['tool'].endswith('sherlock') for r in self.gate.pending()))
        self.gate.finished('one')  # a failure also counts as attempted; never retry it
        self.assertFalse(any(r['tool'].endswith('sherlock') for r in self.gate.pending()))
        self.gate.started('two',P+'search_username_maigret',{'username':'other'})
        self.gate.finished('two')
        self.assertTrue(any(r['tool'].endswith('maigret') for r in self.gate.pending()))

    async def test_no_progress_fails_honestly_without_infinite_model_continuation(self):
        await self.gate.stop({},None,None)
        result=await self.gate.stop({'stop_hook_active':True},None,None)
        self.assertFalse(result['continue_'])
        with self.assertRaises(CollectionIncomplete):self.gate.assert_complete()

    async def test_missing_inputs_not_invented_from_search_candidates(self):
        self.evidence(P+'search_web_you',{'query':'common name'},{'url':'https://github.com/lookalike','website':'https://other.dev'})
        self.assertEqual([r['tool'] for r in self.gate.pending()],[P+'search_publications_crossref'])

    async def test_rechecks_new_domain_after_first_domain_completed(self):
        self.evidence('mcp__firecrawl__firecrawl_scrape',{'url':'https://one.dev/'},{'markdown':'hello'})
        for i,tool in enumerate(TOOLS):
            self.gate.started(str(i),tool,{'domain':'one.dev','query':'name with context'});self.gate.finished(str(i))
        self.assertEqual(self.gate.pending(),[])
        self.evidence('mcp__firecrawl__firecrawl_scrape',{'url':'https://two.dev/'},{'markdown':'hello'})
        self.assertEqual(len(self.gate.pending()),3)

    async def test_options_wire_stop_hook_without_network(self):
        with patch('backend.orchestrator.mcp_registry',return_value=({},TOOLS)):
            options=build_options(self.pad,1)
        self.assertIn('Stop',options.hooks)
        result=await options.hooks['Stop'][0].hooks[0]({},None,None)
        self.assertEqual(result['decision'],'block')
