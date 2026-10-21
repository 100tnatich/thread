# Briefing — thread

> Écrit a posteriori (Thread est antérieur au starter-kit). Le détail est dans `README.md`, `docs/CONTEXTE.md` et `CLAUDE.md`.

## Objectif

Appli de flashcards à répétition espacée pour Nate (apprenti informaticien CFC + maturité pro). Il injecte la matière thème par thème,
l'appli génère les questions et les planifie avec FSRS **piloté par ses dates de test**; il s'assoit, répond aux questions du jour, c'est tout.

## Utilisateurs et accès

Connexion requise. Rôles: admin, standard, étendu (voir `CLAUDE.md`). Données partagées entre comptes. PWA installable sur téléphone.

## Contraintes

Coût LLM plafonné (jour/mois/opération), mode simulé par défaut, CSP stricte (KaTeX: `style-src 'unsafe-inline'`), mobile d'abord, français partout.

## Hors périmètre

Pas d'inscription publique; pas de collaboration en temps réel.

## Hypothèses

Briefing reconstitué depuis la documentation existante, sans nouvelle décision.
