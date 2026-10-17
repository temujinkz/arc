"""Product integration checks; no providers or production databases."""

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend.research.blackboard import Blackboard
from backend.research.evidence import EvidenceReader
from backend.research.models import RunPolicy, TaskProposal, WorkerResult


class RetrievalTests(unittest.TestCase):
    def test_initial_passages_cover_non_developer_sources_without_name_matches(self):
        with tempfile.TemporaryDirectory() as tmp, Blackboard(Path(tmp) / "case.db") as board:
            board.create_run("r", "Research a ceramic artist")
            board.create_run("other", "Other person")
            reader = EvidenceReader(board, "r")
            for key, body in (
                ("exhibition", "Solo exhibition: porcelain vessels shown at the city gallery."),
                ("interview", "Interview: clay firing and glaze experiments in the studio."),
                ("search", "Another person has a matching name."),
            ):
                board.add_source("r", key, body)
                reader.index_source(key)
            board.add_source("other", "unrelated", "Other case must stay excluded")
            EvidenceReader(board, "other").index_source("unrelated")
            with board.transaction():
                board._put("r", "identity_context", "operator", {"excluded_discovery_sources": ["search"]})
            chosen = reader.initial_passages("A name absent from all passages", limit=24, max_bytes=12000)
            self.assertEqual({s.evidence_id for s in chosen}, {"exhibition", "interview"})
            self.assertTrue(all(s.run_id == "r" for s in chosen))
            self.assertLessEqual(len(json.dumps([s.model_dump() for s in chosen]).encode()), 12000)
            self.assertEqual(reader.initial_passages("unmatched", limit=24, max_bytes=1), [])
            manifest = board._get("r", "selection_manifest", "initial")
            self.assertEqual(set(manifest["excluded_span_ids"]), {s.id for s in board.snapshot("r").spans})

    def test_identity_context_excludes_discovery_candidates_from_packets(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            Blackboard(Path(tmp) / "case.db") as board,
        ):
            board.create_run("r", "Common name")
            board.add_source(
                "r", "search", "Other Person built a project with ten million users"
            )
            board.add_source("r", "profile", "Selected Person built an editor project")
            board.add_source("r", "lookalike", "Unrelated Person built an editor project")
            reader = EvidenceReader(board, "r")
            rejected = reader.index_source("search")[0]
            selected = reader.index_source("profile")[0]
            lookalike = reader.index_source("lookalike")[0]
            context = {
                "operator_decisions": [{"answer": "Selected Person"}],
                "excluded_discovery_sources": ["search"],
                "excluded_identity_sources": ["lookalike"],
            }
            with board.transaction():
                board._put("r", "identity_context", "operator", context)
            h = board.add_hypothesis(
                "r", "Selected Person", "Built an editor project", 0
            )
            task = board.admit_task(
                "r",
                TaskProposal(
                    hypothesis_id=h.id,
                    goal="Check project",
                    role="artifact",
                    span_ids=[selected.id],
                ),
                RunPolicy(),
            )
            packet = reader.build_packet(task, 4000)
            self.assertNotIn(rejected.id, {s.id for s in packet.spans})
            self.assertNotIn(lookalike.id, {s.id for s in packet.spans})
            self.assertNotIn(lookalike.id, {s.id for s in reader.search("editor project")})
            self.assertNotIn(lookalike.id, {s.id for s in reader.initial_passages("editor", limit=10, max_bytes=8000)})
            self.assertEqual(packet.identity_context, context)
            self.assertNotIn(
                rejected.id, {s.id for s in reader.search("built project")}
            )

    def test_related_readme_survives_large_manifest_and_case_scoping(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            Blackboard(Path(tmp) / "case.db") as board,
        ):
            board.create_run("r", "Flightdeck")
            board.create_run("other", "Other")
            reader = EvidenceReader(board, "r")
            board.add_source(
                "r",
                "post",
                {
                    "text": "I built Flightdeck; it compares git history. https://github.com/Jack/Flightdeck"
                },
            )
            post = reader.index_source("post")[0]
            board.add_source(
                "r",
                "repo",
                "# Flightdeck\nGit history comparison is described here. Local-only telemetry.",
                url="https://github.com/Jack/Flightdeck/blob/main/README.md",
            )
            repo = reader.index_source("repo")[0]
            for i in range(220):
                board.add_source("r", f"noise{i}", f"Unrelated navigation skill {i}")
                reader.index_source(f"noise{i}")
            board.add_source("other", "secret", "Flightdeck OTHER CASE MUST NOT APPEAR")
            EvidenceReader(board, "other").index_source("secret")
            h = board.add_hypothesis(
                "r", "person", "Flightdeck compares git history locally", 0
            )
            task = board.admit_task(
                "r",
                TaskProposal(
                    hypothesis_id=h.id,
                    goal="Inspect implementation",
                    role="artifact",
                    span_ids=[post.id],
                ),
                RunPolicy(),
            )
            packet = reader.build_packet(task, 3000)
            self.assertIn(repo.id, {s.id for s in packet.spans})
            self.assertLessEqual(packet.token_upper_bound, 3000)
            self.assertGreater(packet.excluded_span_count, 200)
            self.assertFalse(packet.excluded_span_ids)
            self.assertNotIn("OTHER CASE", packet.model_dump_json())
            manifest = board._get("r", "packet_manifest", packet.manifest_id)
            self.assertEqual(
                len(manifest["excluded_span_ids"]), packet.excluded_span_count
            )

    def test_fts_zero_strict_matches_expands_keywords(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            Blackboard(Path(tmp) / "case.db") as board,
        ):
            board.create_run("r", "Project")
            board.add_source("r", "s", "Open Agent Network escrow on Base Sepolia")
            reader = EvidenceReader(board, "r")
            span = reader.index_source("s")[0]
            self.assertIn(
                span.id,
                [
                    s.id
                    for s in reader.search(
                        "Open Agent Network nonexistentword", limit=8
                    )
                ],
            )


class OutputTests(unittest.TestCase):
    def test_critic_retains_all_citations_when_full_request_fits_wire_limit(self):
        import asyncio
        from types import SimpleNamespace
        from backend.research.eval_adapters import CachedModelCritic
        from backend.research.critic import CriticDecision
        def record(**values):
            return SimpleNamespace(**values, model_dump=lambda: values)
        spans = [record(id=f"s{i}", text="evidence " * 1100) for i in range(3)]
        finding = record(id="f", span_ids=[s.id for s in spans])
        report = SimpleNamespace(wave=1, after=SimpleNamespace(
            findings=[finding], hypotheses=[], tasks=[], leads=[]))
        reader = SimpleNamespace(read_span=lambda sid: next(s for s in spans if s.id == sid), identity_context=lambda: {})
        with tempfile.TemporaryDirectory() as tmp:
            model = SimpleNamespace(generate=AsyncMock(return_value=(CriticDecision(), 0)), ledger=SimpleNamespace(directory=Path(tmp)))
            asyncio.run(CachedModelCritic(model)(report, reader))
            payload = model.generate.call_args.args[2]
            self.assertGreater(len(json.dumps(payload).encode()), 28000)
            self.assertEqual(payload["passages"], [s.model_dump() for s in spans])
            from backend.research.eval_model import prepare_model_request, MAX_REQUEST_BYTES
            _, _, wire = prepare_model_request(model.generate.call_args.args[1], payload, CriticDecision)
            self.assertLessEqual(len(wire), MAX_REQUEST_BYTES)
            from backend.research.critic import CriticInputError
            spans[0] = record(id="s0", text="x" * MAX_REQUEST_BYTES)
            model.generate.reset_mock()
            with self.assertRaises(CriticInputError):
                asyncio.run(CachedModelCritic(model)(report, reader))
            model.generate.assert_not_awaited()

    def test_bad_claim_citations_are_quarantined_without_losing_valid_claims(self):
        from backend.research.eval_adapters import ExtractedClaims, scope_extraction
        from types import SimpleNamespace
        spans = [SimpleNamespace(id="passage_a", evidence_id="source_a")]
        data = ExtractedClaims(claims=[
            {"statement": "Invalid source-level citation", "span_ids": ["source_a"], "limitation": "Self report"},
            {"statement": "Valid passage citation", "span_ids": ["passage_a"], "limitation": "Self report"},
            {"statement": "Mixed valid and fabricated citations", "span_ids": ["passage_a", "invented"], "limitation": "Self report"},
        ], selection_reason="Distinct claims", source_assessments=[
            {"evidence_id": "source_a", "status": "relevant", "reason": "Operator-selected profile"},
        ])
        selected, audit = scope_extraction(data, spans)
        self.assertEqual([c.statement for c in selected], ["Valid passage citation"])
        self.assertEqual([r["invalid_span_ids"] for r in audit["citation_excluded_claims"]], [["source_a"], ["invented"]])
        self.assertEqual(len(data.claims), 3)  # Preserve original model response.
        data.claims = [data.claims[0]]
        selected, audit = scope_extraction(data, spans)
        self.assertEqual(selected, [])  # Never invent a claim if every citation is bad.
        self.assertEqual(len(audit["citation_excluded_claims"]), 1)

    def test_identity_scope_excludes_unresolved_sources_and_mixed_claims(self):
        from backend.research.eval_adapters import ExtractedClaims, scope_extraction
        data = ExtractedClaims(claims=[
            {"statement": "Authored a cloud guide", "span_ids": ["company"], "limitation": "Authorship only"},
            {"statement": "Works at an unrelated university", "span_ids": ["lookalike"], "limitation": "Unresolved identity"},
        ], selection_reason="Evidence", source_assessments=[
            {"evidence_id": "a", "status": "relevant", "reason": "Company byline matches target affiliation"},
            {"evidence_id": "b", "status": "unresolved", "reason": "Same name; no employer match"},
        ])
        from types import SimpleNamespace
        spans = [SimpleNamespace(id="company", evidence_id="a"), SimpleNamespace(id="lookalike", evidence_id="b")]
        selected, audit = scope_extraction(data, spans)
        self.assertEqual([c.statement for c in selected], ["Authored a cloud guide"])
        self.assertEqual(audit["excluded_identity_sources"], ["b"])
        data.source_assessments = []
        with self.assertRaises(ValueError): scope_extraction(data, spans)

    def test_firecrawl_uses_official_authenticated_http_endpoint(self):
        from backend.agent import upstream_registry
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "test-only"}):
            server = upstream_registry()[0]["firecrawl"]
            self.assertEqual(server["type"], "http")
            self.assertEqual(server["url"], "https://mcp.firecrawl.dev/v2/mcp")
            self.assertEqual(server["headers"]["Authorization"], "Bearer test-only")

    def test_firecrawl_proxy_pins_supported_era_and_reports_discovery_errors(self):
        from backend.mcp_servers.cached_proxy import build_proxy
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "FIRECRAWL_API_KEY": "test-only", "ARC_DATABASE_PATH": str(Path(tmp) / "cache.db"),
        }), patch("backend.mcp_servers.cached_proxy.create_proxy") as create, patch("backend.mcp_servers.cached_proxy.SourceCache"):
            build_proxy("firecrawl")
            self.assertEqual(create.call_args.kwargs["mode"], "legacy")
            self.assertEqual(create.call_args.kwargs["provider_error_strategy"], "raise")

    def test_fenced_json_and_trailing_comma_recover_without_changing_strings(self):
        from backend.research.parsing import parse_model_json

        result = parse_model_json(
            '```json\n{"unresolved":["literal comma,} remains",],}\n```', WorkerResult
        )
        self.assertEqual(result.unresolved, ["literal comma,} remains"])
        with self.assertRaises(ValueError):
            parse_model_json('{"findings": [{"statement":"invented"}]}', WorkerResult)
        with self.assertRaises(ValueError):
            parse_model_json('Here is some text {"status":"completed"}', WorkerResult)

    def test_extra_extracted_candidate_is_recorded_without_exceeding_task_bound(self):
        from backend.research.eval_adapters import ExtractedClaims, select_claims
        from backend.research.parsing import parse_model_json

        payload = {
            "claims": [
                {"statement": name, "span_ids": ["source"], "limitation": "Self-report"}
                for name in ("Stela", "CodePilot", "Khatabook", "Selection reason: three projects")
            ],
            "selection_reason": "Distinct projects",
        }
        extracted = parse_model_json(json.dumps(payload), ExtractedClaims)
        selected, audit = select_claims(extracted, {"source"})
        self.assertEqual([c.statement for c in selected], ["Stela", "CodePilot", "Khatabook"])
        self.assertEqual(audit["excluded_claims"], payload["claims"][3:])
        self.assertEqual(audit["selected_claims"], payload["claims"][:3])
        self.assertEqual(len(extracted.claims), 4)
        # A partial result must not require inventing claims to reach exactly three.
        extracted.claims = extracted.claims[:1]
        self.assertEqual(len(select_claims(extracted, {"source"})[0]), 1)
        with self.assertRaises(ValueError):
            select_claims(extracted, {"different-source"})
        with self.assertRaises(ValueError):
            parse_model_json('{"claims":[{"statement":"No evidence"}],"selection_reason":"x"}', ExtractedClaims)

    def test_duplicate_project_coverage_rejected(self):
        from backend.research.eval_adapters import (
            ExtractedClaims,
            validate_extracted_coverage,
        )

        data = ExtractedClaims(
            claims=[
                {
                    "statement": str(i),
                    "span_ids": ["stela" if i < 2 else "flight"],
                    "limitation": "unverified",
                }
                for i in range(3)
            ],
            selection_reason="test",
        )
        with self.assertRaises(ValueError):
            validate_extracted_coverage(
                data,
                [
                    {"span_ids": ["stela"]},
                    {"span_ids": ["flight"]},
                    {"span_ids": ["oan"]},
                ],
            )


class WorkspaceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_generation_reports_known_or_unknown_research_usage(self):
        from types import SimpleNamespace
        from backend.investigations import Investigation
        from backend.scratchpad import InvestigationScratchpad
        from backend.research.pipeline import MODEL, run_workspace_research

        for known in (True, False):
            with (
                self.subTest(known=known),
                tempfile.TemporaryDirectory() as tmp,
                patch.dict(
                    os.environ,
                    {"ARC_RESEARCH_DIR": tmp, "ANTHROPIC_API_KEY": "unit-test-only"},
                ),
                patch("backend.orchestrator.model_for", return_value=MODEL),
                patch(
                    "backend.orchestrator.harvest",
                    new=AsyncMock(return_value=SimpleNamespace(total_cost_usd=0.01, result="Collected evidence")),
                ),
            ):
                run = Investigation("Person", budget=1.5)
                run.scratchpad = InvestigationScratchpad("failed", run.emit)
                run.scratchpad.capture(
                    "mcp__github__get_file_contents",
                    "tool",
                    "I built an editor project",
                    {},
                )

                async def fail(model, *args):
                    ticket = model.ledger.reserve("extract_claims")
                    if known:
                        model.ledger.finish(
                            ticket, {"input_tokens": 100, "output_tokens": 100}
                        )
                    raise ValueError("Malformed response")

                with patch(
                    "backend.research.pipeline.EvaluationModel.generate", new=fail
                ):
                    await run_workspace_research(run)
                usage = [
                    e
                    for e in run.events
                    if e["type"] == "agent.usage"
                    and e.get("phase") == "recursive_research"
                ]
                self.assertEqual(len(usage), 1)
                if known:
                    self.assertAlmostEqual(usage[0]["cost_usd"], 0.00006)
                else:
                    self.assertIsNone(usage[0]["cost_usd"])
                    self.assertTrue(run.chat_blocked)

    async def test_case_scoped_spans_and_history_survive_restart(self):
        from backend import main
        from backend.investigations import Investigation
        from backend.scratchpad import InvestigationScratchpad

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ, {"ARC_DATABASE_PATH": str(Path(tmp) / "history.db")}
            ),
            patch(
                "backend.main.run_workspace_research",
                side_effect=AssertionError("Must not restart research"),
            ),
        ):
            path = Path(tmp) / "case.db"
            with Blackboard(path) as board:
                for run_id in ("one", "two"):
                    board.create_run(run_id, "Person")
                    board.add_source(
                        run_id,
                        "profile",
                        {"description": "Reports a 40% improvement"},
                        observed_at="2026-10-14T12:00:00Z",
                        source_kind="SELF_AUTHORED",
                        url="https://example.com/profile",
                    )
                span = EvidenceReader(board, "one").index_source("profile")[0]
                other = EvidenceReader(board, "two").index_source("profile")[0]
            async with main.lifespan(main.app):
                run = Investigation(
                    "Person",
                    store=main.store,
                    research_case_path=str(path),
                    research_run_id="one",
                    cached_source_session_id=main.CACHED_PROFILE,
                    chat_blocked=True,
                )
                run.scratchpad = InvestigationScratchpad("saved", run.emit)
                run.scratchpad.emit({"type": "investigation.complete"})
                run.finish()
            async with (
                main.lifespan(main.app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app),
                    base_url="http://offline",
                ) as client,
            ):
                response = await client.get(f"/api/investigate/saved/spans/{span.id}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["span"]["text"], "Reports a 40% improvement"
                )
                self.assertEqual(response.json()["span"]["pointer"], "/description")
                self.assertEqual(
                    response.json()["span"]["observed_at"], "2026-10-14T12:00:00Z"
                )
                self.assertEqual(
                    (
                        await client.get(f"/api/investigate/saved/spans/{other.id}")
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    (await client.get("/api/investigate/stream/saved")).status_code, 200
                )
                self.assertTrue(main.get_run("saved").chat_blocked)

    async def test_main_route_uses_recursive_runner_idempotently(self):
        from backend import main

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    "ARC_DATABASE_PATH": str(Path(tmp) / "history.db"),
                    "NEO4J_URI": "unused",
                    "NEO4J_USERNAME": "unused",
                    "NEO4J_PASSWORD": "unused",
                    "CLAUDE_MAX_BUDGET_USD": "1.5",
                },
            ),
            patch(
                "backend.main.run_workspace_research", new_callable=AsyncMock
            ) as runner,
        ):
            async with (
                main.lifespan(main.app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app),
                    base_url="http://offline",
                ) as client,
            ):
                request = {"target": "Person", "request_id": "same"}
                self.assertEqual(
                    (
                        await client.post("/api/investigations", json=request)
                    ).status_code,
                    202,
                )
                await main.get_run("same").task
                self.assertEqual(
                    (
                        await client.post("/api/investigations", json=request)
                    ).status_code,
                    202,
                )
                runner.assert_awaited_once()


class PortraitTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_citation_withholds_only_unsupported_section(self):
        from backend.research.projection import (
            PortraitDraft,
            PortraitEvidencePacket,
            synthesize_portrait,
        )
        from backend.graph_models import PortraitSection

        with (
            tempfile.TemporaryDirectory() as tmp,
            Blackboard(Path(tmp) / "case.db") as board,
        ):
            board.create_run("r", "Person")
            board.add_source("r", "s", "Self-reported project")
            span = EvidenceReader(board, "r").index_source("s")[0]
            adapter = AsyncMock()
            adapter.synthesize.return_value = PortraitDraft(
                sections=[
                    PortraitSection(
                        section="core_obsessions",
                        title="Interests",
                        analysis="An attributed interpretation",
                        evidence_ids=["s"],
                        span_ids=[span.id],
                        limitations="Self-report",
                    ),
                    PortraitSection(
                        section="builder_velocity",
                        title="Ships three products every week",
                        analysis="Unsupported assertion must disappear",
                        evidence_ids=["s"],
                        limitations="",
                    ),
                ]
            )
            result = await synthesize_portrait(
                PortraitEvidencePacket(findings=[], spans=[span]), adapter
            )
            self.assertEqual(
                result.sections[0].analysis, "An attributed interpretation"
            )
            self.assertEqual(result.sections[1].support_status, "INSUFFICIENT_EVIDENCE")
            self.assertEqual(result.sections[1].title, "Builder Velocity")
            self.assertNotIn("Unsupported assertion", result.sections[1].analysis)
            self.assertIn("builder_velocity", result.missing_sections)
            adapter.synthesize.return_value.sections[0].span_ids = ["invented"]
            with self.assertRaises(ValueError):
                await synthesize_portrait(
                    PortraitEvidencePacket(findings=[], spans=[span]), adapter
                )
