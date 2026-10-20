"""Répartition du plafond entre thèmes en mode test (pas un seul thème par jour).

Repères: plafond 40 points, chaque carte vaut 1 point (`make_card`). Cartes
neuves (jamais révisées) = dues tout de suite.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import timedelta

from flashcards import api
from flashcards.heuristic import Exam

from .conftest import TODAY, make_card


def exam(cours: str, jours: int, n: int) -> Exam:
    return Exam(id=n, cours=frozenset({cours}), date=TODAY + timedelta(days=jours),
                intitule=f"TE {cours}")


def par_theme(conn, config) -> Counter:
    items = api.get_today_queue(conn, TODAY, config)
    return Counter(i.card.cours for i in items)


def test_le_plafond_se_partage_entre_quatre_themes(conn, config):
    for t in "ABCD":
        for k in range(30):
            make_card(conn, f"{t}{k}", cours=t)
    exams = [exam("A", 20, 1), exam("B", 30, 2), exam("C", 40, 3), exam("D", 50, 4)]
    cfg = replace(config, jours_focus_test=2)
    plan = api.construire_file(api.load_cards(conn), exams, TODAY, cfg)
    n = Counter(i.card.cours for i in plan.items)
    assert n == {"A": 10, "B": 10, "C": 10, "D": 10}


def test_un_theme_sans_assez_de_cartes_rend_sa_part(conn, config):
    for k in range(3):
        make_card(conn, f"A{k}", cours="A")
    for t in "BCD":
        for k in range(30):
            make_card(conn, f"{t}{k}", cours=t)
    exams = [exam(t, 20 + i, i) for i, t in enumerate("ABCD")]
    plan = api.construire_file(api.load_cards(conn), exams, TODAY, config)
    n = Counter(i.card.cours for i in plan.items)
    assert n["A"] == 3
    assert sum(n.values()) == 40  # rien de perdu: le surplus va aux autres
    assert max(n["B"], n["C"], n["D"]) - min(n["B"], n["C"], n["D"]) <= 1


def test_theme_focus_passe_avant_sans_limite_de_part(conn, config):
    for t in "AB":
        for k in range(60):
            make_card(conn, f"{t}{k}", cours=t)
    exams = [exam("A", 2, 1), exam("B", 20, 2)]  # test de A dans 2 jours
    plan = api.construire_file(api.load_cards(conn), exams, TODAY, config)
    n = Counter(i.card.cours for i in plan.items)
    assert n == {"A": 40}


def test_hors_fenetre_focus_on_repartit(conn, config):
    for t in "AB":
        for k in range(60):
            make_card(conn, f"{t}{k}", cours=t)
    exams = [exam("A", 3, 1), exam("B", 20, 2)]  # 3 jours > focus (2)
    plan = api.construire_file(api.load_cards(conn), exams, TODAY, config)
    n = Counter(i.card.cours for i in plan.items)
    assert n == {"A": 20, "B": 20}


def test_un_seul_theme_garde_tout_le_plafond(conn, config):
    for k in range(60):
        make_card(conn, f"A{k}", cours="A")
    plan = api.construire_file(api.load_cards(conn), [exam("A", 20, 1)], TODAY, config)
    assert len(plan.items) == 40


def test_une_carte_plus_lourde_que_la_part_reste_planifiable(conn, config):
    # 4 thèmes -> part de 10 pts; une rédaction de 25 pts ne tient dans aucune part
    # mais doit être reprise par la redistribution.
    make_card(conn, "gros", cours="A", poids=25.0)
    for t in "BCD":
        for k in range(30):
            make_card(conn, f"{t}{k}", cours=t)
    exams = [exam(t, 20 + i, i) for i, t in enumerate("ABCD")]
    plan = api.construire_file(api.load_cards(conn), exams, TODAY, config)
    assert "gros" in {i.card.id for i in plan.items}
    assert plan.points <= config.plafond_quotidien + 1e-9
