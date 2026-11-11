"""Ensembles d'intervalles entiers semi-ouverts [début, fin).

Utilisés pour savoir quelles portions d'un clip (en PTS source) sont gardées,
déjà analysées, ou restent à analyser.
"""

from __future__ import annotations

Interval = tuple[int, int]


def normalize(intervals: list[Interval] | list[list[int]]) -> list[Interval]:
    """Trie, supprime les intervalles vides et fusionne ceux qui se touchent."""
    items = sorted((int(a), int(b)) for a, b in intervals if b > a)
    out: list[Interval] = []
    for a, b in items:
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def union(a: list[Interval], b: list[Interval]) -> list[Interval]:
    return normalize(list(a) + list(b))


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Parties de `a` qui ne sont couvertes par aucun intervalle de `b`."""
    out: list[Interval] = []
    cuts = normalize(b)
    for start, end in normalize(a):
        cur = start
        for c0, c1 in cuts:
            if c1 <= cur:
                continue
            if c0 >= end:
                break
            if c0 > cur:
                out.append((cur, c0))
            cur = max(cur, c1)
            if cur >= end:
                break
        if cur < end:
            out.append((cur, end))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    nb = normalize(b)
    for a0, a1 in normalize(a):
        for b0, b1 in nb:
            lo, hi = max(a0, b0), min(a1, b1)
            if hi > lo:
                out.append((lo, hi))
    return normalize(out)


def pad(a: list[Interval], amount: int, lo: int, hi: int) -> list[Interval]:
    """Élargit chaque intervalle de `amount` de chaque côté, borné à [lo, hi)."""
    return normalize([(max(lo, s - amount), min(hi, e + amount)) for s, e in a])


def total(a: list[Interval]) -> int:
    return sum(e - s for s, e in normalize(a))


def contains(a: list[Interval], value: int) -> bool:
    return any(s <= value < e for s, e in a)
