"""Poids des cartes, en points.

Le plafond quotidien et la dette se comptent en points, pas en nombre de
questions: une carte de vocabulaire (0,1) ne pèse pas autant qu'une
rédaction (4). Résolution, par priorité:

1. `poids` explicite de la carte (nullable);
2. catégorie du concept (`vocabulaire` => 0,1), si elle est connue;
3. poids du type de question.
"""
from __future__ import annotations

from .config import SchedulerConfig


def poids_carte(
    qtype: str,
    categorie: str | None,
    explicite: float | None,
    config: SchedulerConfig,
) -> float:
    if explicite is not None:
        return float(explicite)
    if categorie is not None and categorie in config.poids_categorie:
        return float(config.poids_categorie[categorie])
    if qtype not in config.poids_type:
        raise RuntimeError(f"Aucun poids configuré pour le type « {qtype} ».")
    return float(config.poids_type[qtype])


def poids_plafonne(poids: float, plafond: float) -> float:
    """Garde-fou: une carte ne compte jamais plus que le plafond du jour.

    Sans lui, une carte plus lourde que le plafond ne tiendrait dans aucune
    journée et resterait due à jamais. Plafonnée, elle remplit la journée à
    elle seule mais passe.
    """
    return min(poids, plafond) if plafond > 0 else poids
