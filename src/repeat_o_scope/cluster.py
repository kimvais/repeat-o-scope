"""Groups similar texts locally (static embeddings + HDBSCAN) and ranks the groups by size."""

import collections
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

# Small (about 30 MB), numpy only, runs on a CPU in seconds; nothing leaves the machine.
_MODEL = 'minishlab/potion-base-8M'
_MAX_CHARS = 300
_NORMALISE = [
    (re.compile(r'https?://\S+'), '<url>'),
    (re.compile(r'(?:[a-z]:)?[\\/][\w.\\/~-]+', re.IGNORECASE), '<path>'),
    (re.compile(r'[!#]\d+'), '<ref>'),
    (re.compile(r'\b[0-9a-f]{7,40}\b'), '<hash>'),
    (re.compile(r'\d+'), '<n>'),
    (re.compile(r'\s+'), ' '),
]


@dataclass(frozen=True)
class Cluster:
    total: int
    variants: list[tuple[str, int]]  # most frequent first


def normalise(text: str) -> str:
    """Returns `text` lowercased, with paths, URLs, references, hashes and numbers replaced by placeholders."""
    text = text.lower()[:_MAX_CHARS]
    for pattern, placeholder in _NORMALISE:
        text = pattern.sub(placeholder, text)
    return text.strip()


def rank(texts: Iterable[str], *, min_size: int) -> tuple[list[Cluster], int]:
    """Returns clusters of at least `min_size` occurrences, largest first, and the count of one-offs."""
    counts = collections.Counter(texts)
    total = sum(counts.values())
    unique = list(counts)
    labels = _labels(unique, counts=counts, min_size=min_size) if total >= min_size and len(unique) > 1 else []
    groups = collections.defaultdict(list)
    for text, label in zip(unique, labels or [-1] * len(unique)):
        if label != -1:
            groups[label].append(text)
        elif counts[text] >= min_size:
            # A frequent exact repeat is a cluster even when nothing else is like it.
            groups[('exact', text)].append(text)
    clusters = [
        Cluster(
            total=sum(counts[t] for t in members),
            variants=sorted(((t, counts[t]) for t in members), key=lambda v: (-v[1], v[0])),
        )
        for members in groups.values()
    ]
    clusters.sort(key=lambda c: (-c.total, c.variants[0][0]))
    return clusters, total - sum(c.total for c in clusters)


def _labels(unique: list[str], *, counts: collections.Counter, min_size: int) -> list[int]:
    """Returns an HDBSCAN label per unique text, weighting each text by how often it occurs."""
    import numpy as np
    from model2vec import StaticModel
    from sklearn.cluster import HDBSCAN

    emb = StaticModel.from_pretrained(_MODEL).encode(unique)
    emb = emb / np.linalg.norm(emb, axis=1, keepdims=True).clip(min=1e-9)
    # HDBSCAN has no sample weights, so repeat each text's row by its count.
    repeats = np.array([counts[t] for t in unique])
    labels = HDBSCAN(min_cluster_size=min_size, min_samples=2, copy=True).fit_predict(np.repeat(emb, repeats, axis=0))
    # Identical rows always share a label; take the first of each run.
    starts = np.concatenate(([0], np.cumsum(repeats)[:-1]))
    _LOGGER.info('Clustered `%s` texts (`%s` unique).', int(repeats.sum()), len(unique))
    return [int(labels[s]) for s in starts]
