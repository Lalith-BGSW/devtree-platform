"""
RAG retrieval over NormalizedRecords.

Per the spec, RAG is used ONLY to search/retrieve workbook data - never to
generate DTS directly. This module's job ends at "here are the k most
relevant records"; app/generators/dts_generator.py (deterministic, template
based) is what turns retrieved records into DTS.

Implementation: works with ZERO third-party dependencies by default -
retrieval is pure-Python lexical (token-overlap) scoring, so nothing here
requires a C compiler or a model download. If numpy AND sentence-transformers
are both importable (optional extras - see requirements.txt), retrieval
upgrades automatically to embedding-based cosine similarity for better
semantic matching. Either way the search() interface is identical.
"""

from __future__ import annotations
import re
from app.models import NormalizedRecord

_MODEL_NAME = "all-MiniLM-L6-v2"
_model = None
_np = None
_embeddings_available = None  # None = not yet checked, True/False once known


def _check_embeddings_available() -> bool:
    """Checks once whether numpy + sentence-transformers are both usable.
    Never raises - any import/load failure just means "use lexical search",
    which is always available since it has no dependencies."""
    global _model, _np, _embeddings_available
    if _embeddings_available is not None:
        return _embeddings_available
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
        _np = np
        _model = SentenceTransformer(_MODEL_NAME)
        _embeddings_available = True
    except Exception:
        _embeddings_available = False
    return _embeddings_available


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _lexical_scores(query: str, texts: list[str]) -> list[float]:
    """Jaccard token-overlap scoring. Pure Python, no dependencies - this is
    the default retrieval method and always available."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return [0.0] * len(texts)
    scores = []
    for t in texts:
        t_tokens = _tokenize(t)
        if not t_tokens:
            scores.append(0.0)
            continue
        overlap = len(q_tokens & t_tokens)
        scores.append(overlap / len(q_tokens | t_tokens))
    return scores


def _record_to_text(rec: NormalizedRecord) -> str:
    """Full text blob for a record - used for fuzzy/semantic ranking, where
    broader context is fine (it only affects sort order, not inclusion)."""
    parts = [f"project: {rec.project}", f"soc: {rec.soc}"]
    for k, v in rec.fields.items():
        parts.append(f"{k.replace('_', ' ')}: {v}")
    return "; ".join(parts)


# Fields deliberately excluded from keyword-filter MATCHING (still fine for
# ranking via _record_to_text, which uses every field). Filtering should
# only consider fields that identify what a pin is ACTUALLY configured
# as - not the SoC's theoretical capability list, and not free-text notes
# fields (Remarks, Netlist review result, Imported *) where an unrelated
# word can incidentally appear as a substring. Confirmed against the real
# workbook: without this restriction, "bluetooth uart" matched records
# from AUDIO_CPU/ETH_SW/VI_MEDIA interface groups too, because the
# generic word "uart" appears in many unrelated instances' free-text
# fields, and every UART instance on the chip (debug UART, GNSS UART...)
# matches the bare word "uart" under OR semantics regardless of Bluetooth
# relevance - restricting to these fields makes filtering reflect what's
# actually assigned to a pin, not incidental text elsewhere on the row.
_FILTER_INCLUDED_SLUGS = {
    "pinmuxed_name", "selected_interface", "interface_group",
    "gen5_signal_name", "direction", "id",
}


def _record_to_filter_text(rec: NormalizedRecord) -> str:
    """Narrow text blob used specifically for keyword_filter substring
    matching - only the fields that identify what a pin is actually
    configured as, so a keyword filter reflects real assignment rather
    than incidental substrings in notes/capability-list fields."""
    parts = [f"project: {rec.project}", f"soc: {rec.soc}"]
    for k, v in rec.fields.items():
        if k not in _FILTER_INCLUDED_SLUGS:
            continue
        parts.append(f"{k.replace('_', ' ')}: {v}")
    return "; ".join(parts)


class ProjectIndex:
    """One RAG index per project (keeps retrieval scoped and fast)."""

    def __init__(self, project: str, records: list[NormalizedRecord]):
        self.project = project
        self.records = records
        self._texts: list[str] = []
        self._embeddings = None  # only populated if embeddings are available

    def build(self) -> None:
        self._texts = [_record_to_text(r) for r in self.records]
        if not self.records:
            return
        if _check_embeddings_available():
            emb = _model.encode(self._texts, normalize_embeddings=True, show_progress_bar=False)
            self._embeddings = _np.asarray(emb, dtype=_np.float32)

    def search(self, query: str, k: int = 10,
               keyword_filter: list[str] | None = None,
               limit: int | None = None) -> list[NormalizedRecord]:
        """Returns matching records ranked by relevance.

        Critically: when keyword_filter is given, filtering scans EVERY
        record in this project (not just the top-k by fuzzy score) - so a
        query like "show all bluetooth pins" finds every matching row
        across all 2,000+ real workbook rows, not just whichever happened
        to rank in the top k by lexical/semantic similarity. k only caps
        results in the no-filter fallback case (open-ended ranking over
        potentially thousands of irrelevant rows). `limit`, if given, caps
        the final result count for display purposes only, applied AFTER
        the full scan - callers that need every match (e.g. DTS
        generation) should leave it None.
        """
        if not self._texts and self.records:
            self.build()
        if len(self.records) == 0:
            return []

        if self._embeddings is not None and _check_embeddings_available():
            q_emb = _model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
            sims = self._embeddings @ q_emb  # cosine sim, since both sides are normalized
            order = _np.argsort(-sims).tolist()
        else:
            sims = _lexical_scores(query, self._texts)
            order = sorted(range(len(sims)), key=lambda i: -sims[i])

        ranked_all = [self.records[i] for i in order]

        if keyword_filter:
            lowered_kw = [kw.lower() for kw in keyword_filter]

            def matches(rec: NormalizedRecord) -> bool:
                blob = _record_to_filter_text(rec).lower()
                return any(kw in blob for kw in lowered_kw)

            # Full scan over every record in the project, not just the
            # fuzzy top-k - this is what makes keyword-filtered queries
            # exhaustive instead of missing rows the ranker happened to
            # score lower.
            filtered = [r for r in ranked_all if matches(r)]
            if filtered:
                return filtered[:limit] if limit is not None else filtered
            # Keyword filtering matched nothing (e.g. the LLM picked odd
            # keywords) - fall through to pure ranking below rather than
            # returning an empty result.

        # No filter, or filter matched nothing: this is genuinely
        # open-ended ranking over the whole project, so k is the right
        # thing to cap it at (returning thousands of low-relevance rows
        # isn't useful here the way an exhaustive keyword scan is).
        capped = k if limit is None else min(k, limit)
        return ranked_all[:capped]


class RagStore:
    """Holds one ProjectIndex per (workbook_id, project)."""

    def __init__(self):
        self._indexes: dict[tuple[str, str], ProjectIndex] = {}

    def index_records(self, workbook_id: str, project: str,
                       records: list[NormalizedRecord]) -> None:
        idx = ProjectIndex(project, records)
        idx.build()
        self._indexes[(workbook_id, project)] = idx

    def search(self, workbook_id: str, project: str, query: str, k: int = 10,
               keyword_filter: list[str] | None = None,
               limit: int | None = None) -> list[NormalizedRecord]:
        idx = self._indexes.get((workbook_id, project))
        if idx is None:
            return []
        return idx.search(query, k=k, keyword_filter=keyword_filter, limit=limit)


rag_store = RagStore()
