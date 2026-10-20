from __future__ import annotations

import pytest
from fsrs import Rating

from flashcards.grading import (
    corrige_cloze,
    corrige_exact,
    corrige_qcm,
    rating_depuis_correction,
)


@pytest.mark.parametrize(
    "saisie,attendu,resultat",
    [
        ("1", [1], True),
        ("0,2", [0, 2], True),
        ("2 0", [0, 2], True),
        ("0", [0, 2], False),
        ("0,1,2", [0, 2], False),
        ("", [0], False),
        ("abc", [0], False),
    ],
)
def test_corrige_qcm(saisie, attendu, resultat):
    assert corrige_qcm(saisie, attendu) is resultat


def test_corrige_exact_est_strict():
    assert corrige_exact("Berne", "Berne")
    assert corrige_exact("  Berne  ", "Berne")
    assert not corrige_exact("berne", "Berne")
    assert not corrige_exact("Bern", "Berne")


def test_corrige_cloze_exige_tous_les_trous():
    attendu = {"c1": "rouge", "c2": "bleu"}
    assert corrige_cloze({"c1": "rouge", "c2": "bleu"}, attendu)
    assert not corrige_cloze({"c1": "rouge", "c2": "vert"}, attendu)
    assert not corrige_cloze({"c1": "rouge"}, attendu)
    assert not corrige_cloze({"c1": "rouge", "c2": "bleu", "c3": "x"}, attendu)


def test_mapping_notes_fsrs():
    assert rating_depuis_correction(True) is Rating.Good
    assert rating_depuis_correction(False) is Rating.Again
    assert rating_depuis_correction(True, "hard") is Rating.Hard
    assert rating_depuis_correction(True, "easy") is Rating.Easy


def test_reponse_fausse_reste_again_malgre_nuance():
    # Un échec est un échec: FSRS a besoin d'un Again franc.
    assert rating_depuis_correction(False, "easy") is Rating.Again
    assert rating_depuis_correction(False, "hard") is Rating.Again
