#!/usr/bin/env python3
"""
Base des routes (playbooks) — §7.1.
===================================
Cinq configurations de setup coexistent (A..E). Elles sont en OU entre elles,
en ET à l'intérieur de chacune. Un candidat qualifie s'il satisfait UNE route
entièrement. La superposition (n>=2) n'est pas une condition d'entrée : c'est
le signal de conviction maximale, exprimé dans la taille (§7.2).

Chaque route déclare ses `active_components` : les termes de CONV_base dont
elle se sert. Les autres sont neutralisés à 0 et exclus de la renormalisation
(§7.2). C'est un point d'implémentation facile à rater et qui fausse tout le
classement s'il est manqué.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from ...models import Route

Check = Tuple[str, bool, str]  # (nom, ok, détail)


@dataclass
class RouteResult:
    route: Route
    passed: bool
    checks: List[Check]
    active_components: Tuple[str, ...] = field(default_factory=tuple)

    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c[1]]


def all_ok(checks: List[Check]) -> bool:
    return all(ok for _, ok, _ in checks)
