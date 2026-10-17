"""Immutable source addressing and bounded, auditable evidence selection."""

import re
import json
from collections import deque
from urllib.parse import urlsplit
from .blackboard import Blackboard, digest, encoded
from .models import EvidencePacket, EvidenceSpan, Hypothesis, ResearchTask


def at_pointer(data, pointer):
    value = data
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def text_value(value):
    return value if isinstance(value, str) else encoded(value)


def escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


class EvidenceReader:
    def __init__(self, board: Blackboard, run_id: str):
        self.board, self.run_id = board, run_id

    def add_span(self, source_id, pointer, text, start=None, end=None):
        source = self.board._get(self.run_id, "source", source_id)
        raw = text_value(at_pointer(source["data"], pointer))
        if (start is None) != (end is None):
            raise ValueError("Both offsets required")
        if start is not None and not 0 <= start <= end <= len(raw):
            raise ValueError("Invalid offsets")
        if (raw if start is None else raw[start:end]) != text:
            raise ValueError("Excerpt differs from immutable source")
        sid = digest([self.run_id, source_id, source["hash"], pointer, start, end])[:32]
        span = EvidenceSpan(
            id=sid,
            run_id=self.run_id,
            evidence_id=source_id,
            snapshot_hash=source["hash"],
            pointer=pointer,
            text=text,
            start=start,
            end=end,
            **{
                k: source[k]
                for k in ("url", "observed_at", "source_kind", "origin_group")
                if k in source
            },
        )
        with self.board.transaction():
            self.board._put(self.run_id, "span", sid, span.model_dump())
        return span

    def read_span(self, id):
        span = EvidenceSpan.model_validate(self.board._get(self.run_id, "span", id))
        source = self.board._get(self.run_id, "source", span.evidence_id)
        if (
            source["hash"] != span.snapshot_hash
            or digest(source["data"]) != span.snapshot_hash
        ):
            raise ValueError("Snapshot hash changed")
        raw = text_value(at_pointer(source["data"], span.pointer))
        if (raw if span.start is None else raw[span.start : span.end]) != span.text:
            raise ValueError("Excerpt integrity failed")
        return span

    def index_source(self, id, pointer=""):
        source = self.board._get(self.run_id, "source", id)
        spans = []

        def section(value, pointer):
            if isinstance(value, list):
                for i, item in enumerate(value):
                    section(item, f"{pointer}/{i}")
            elif isinstance(value, dict):
                # Individual semantic records retain their sibling dates/subject fields.
                if pointer and len(encoded(value).encode()) <= 12000:
                    spans.append(self.add_span(id, pointer, encoded(value)))
                else:
                    for key, item in value.items():
                        section(item, f"{pointer}/{escape(key)}")
            elif isinstance(value, str) and len(value) > 1000:
                boundaries = (
                    [0]
                    + [
                        m.start()
                        for m in re.finditer(r"(?m)^#{1,6} ", value)
                        if m.start() > 0
                    ]
                    + [len(value)]
                )
                for start, end in zip(boundaries, boundaries[1:]):
                    if value[start:end].strip():
                        spans.append(
                            self.add_span(id, pointer, value[start:end], start, end)
                        )
            elif value is not None:
                spans.append(self.add_span(id, pointer, text_value(value)))

        section(at_pointer(source["data"], pointer), pointer)
        return spans

    def identity_context(self):
        try:
            return self.board._get(self.run_id, "identity_context", "operator")
        except ValueError:
            return {}

    def search(self, query: str, limit: int = 24):
        words = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))
        stop = {
            "the",
            "a",
            "an",
            "to",
            "of",
            "and",
            "or",
            "is",
            "in",
            "for",
            "with",
            "this",
            "from",
            "claim",
            "cached",
            "evidence",
            "inspect",
            "assess",
            "specific",
            "it",
            "that",
            "by",
            "as",
        }
        words = [w for w in words if w not in stop][:32]
        if not words:
            return []
        quoted = ['"' + w.replace('"', '""') + '"' for w in words]
        excluded = self.excluded_sources()
        placeholders = ",".join("?" for _ in excluded)
        restriction = (
            f"AND json_extract(r.payload,'$.evidence_id') NOT IN ({placeholders})"
            if excluded
            else ""
        )

        def query_fts(operator):
            return self.board.db.execute(
                f"""SELECT span_id FROM evidence_fts JOIN research_records r
                ON r.run_id=evidence_fts.run_id AND r.id=span_id AND r.kind='span'
                WHERE evidence_fts MATCH ? AND evidence_fts.run_id=? {restriction}
                ORDER BY bm25(evidence_fts),span_id LIMIT ?""",
                (operator.join(quoted), self.run_id, *excluded, limit),
            ).fetchall()

        rows = query_fts(" AND ")
        if not rows:
            rows = query_fts(" OR ")
        return [self.read_span(row["span_id"]) for row in rows]

    def excluded_sources(self):
        context = self.identity_context()
        return sorted(set(context.get("excluded_discovery_sources", [])) |
                      set(context.get("excluded_identity_sources", [])))

    def initial_passages(self, query: str, *, limit: int, max_bytes: int):
        """Seed extraction from the current case, without a profession keyword list.

        Rank by the actual request, then round-robin across available sources so
        missing name/keyword matches cannot exclude an entire profession. Retain
        omitted span IDs locally; the model can cite only the returned passages.
        """
        if limit < 1 or max_bytes < 1:
            raise ValueError("Initial passage bounds must be positive")
        all_spans = self.board.snapshot(self.run_id).spans
        excluded_sources = set(self.excluded_sources())
        ranked = self.search(query, limit=limit)
        candidates = {s.id: s for s in ranked + all_spans if s.evidence_id not in excluded_sources}
        sources = {}
        for span in candidates.values():
            sources.setdefault(span.evidence_id, deque()).append(span)
        chosen = []
        queues = deque(sources.values())
        while queues and len(chosen) < limit:
            queue = queues.popleft()
            span = queue.popleft()
            if len(json.dumps([s.model_dump() for s in chosen + [span]]).encode()) <= max_bytes:
                chosen.append(self.read_span(span.id))
            if queue:
                queues.append(queue)
        selected_ids = {s.id for s in chosen}
        with self.board.transaction():
            self.board._put(self.run_id, "selection_manifest", "initial", {
                "query": query, "limit": limit, "max_bytes": max_bytes,
                "selected_span_ids": [s.id for s in chosen],
                "excluded_span_ids": [s.id for s in all_spans if s.id not in selected_ids],
                "excluded_discovery_sources": sorted(excluded_sources),
            })
        return chosen

    def build_packet(self, task: ResearchTask, max_input_tokens: int):
        if task.run_id != self.run_id:
            raise ValueError("Task outside case")
        hypothesis = Hypothesis.model_validate(
            self.board._get(self.run_id, "hypothesis", task.hypothesis_id)
        )
        all_spans = self.board.snapshot(self.run_id).spans
        context = self.identity_context()
        excluded_sources = set(self.excluded_sources())
        scoped_spans = [s for s in all_spans if s.evidence_id not in excluded_sources]
        query = task.goal + " " + hypothesis.statement
        ranked = self.search(query)
        by_id = {s.id: s for s in scoped_spans}
        primary = [by_id[s] for s in task.span_ids if s in by_id]
        words = set(re.findall(r"\w+", query.casefold()))

        def linked_repo(span):
            url = urlsplit(span.url)
            parts = url.path.split("/")
            if url.hostname != "github.com" or len(parts) < 3:
                return False
            anchor = f"https://github.com/{parts[1]}/{parts[2]}"
            name = set(re.findall(r"\w+", parts[2].replace("-", " ").casefold()))
            return bool(name and name <= words) or any(
                anchor in s.text for s in primary
            )

        # Preserve explicitly requested passages, then diversify into linked repo
        # evidence before unrelated profile keyword matches. All retrieval is local.
        linked = sorted(
            [s for s in scoped_spans if linked_repo(s)],
            key=lambda s: (
                -len(words & set(re.findall(r"\w+", s.text.casefold()))),
                len(s.text),
                s.id,
            ),
        )
        candidates = list({s.id: s for s in primary + linked + ranked}.values())
        missing = [s for s in task.span_ids if s not in by_id]
        packet = EvidencePacket(
            task_id=task.id,
            goal=task.goal,
            hypothesis=hypothesis,
            identity_context=context,
            missing_span_ids=missing,
            manifest_id=digest([task.id, task.version, query])[:24],
        )

        def measure(p):
            # Include JSON metadata and count field itself; never pretend this is model usage.
            p.token_upper_bound = 0
            for _ in range(4):
                p.token_upper_bound = len(p.model_dump_json().encode())
            return p.token_upper_bound

        if measure(packet) > max_input_tokens:
            raise ValueError("Packet metadata exceeds input bound")
        for span in candidates:
            trial = packet.model_copy(deep=True)
            trial.spans.append(self.read_span(span.id))
            if any(
                span.text in previous.text or previous.text in span.text
                for previous in packet.spans
            ):
                continue
            if measure(trial) <= max_input_tokens:
                packet = trial
        measure(packet)
        included = {s.id for s in packet.spans}
        excluded = [s.id for s in all_spans if s.id not in included]
        packet.excluded_span_count = len(excluded)
        # Small manifests remain inline for compatibility. Large manifests never
        # compete with source excerpts for model context.
        packet.excluded_span_ids = excluded if len(excluded) <= 16 else []
        if measure(packet) > max_input_tokens:
            packet.excluded_span_ids = []
        while measure(packet) > max_input_tokens and packet.spans:
            packet.spans.pop()
            included = {s.id for s in packet.spans}
            excluded = [s.id for s in all_spans if s.id not in included]
            packet.excluded_span_count = len(excluded)
        if measure(packet) > max_input_tokens:
            raise ValueError("Packet metadata exceeds input bound")
        with self.board.transaction():
            self.board._put(
                self.run_id,
                "packet_manifest",
                packet.manifest_id,
                {
                    "task_id": task.id,
                    "included_span_ids": [s.id for s in packet.spans],
                    "excluded_span_ids": excluded,
                    "missing_span_ids": missing,
                },
            )
        return packet
