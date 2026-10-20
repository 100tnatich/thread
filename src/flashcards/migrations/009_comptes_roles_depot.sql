-- Comptes multiples avec rôles, et dépôt de fichiers par cours.
--
-- `web_user` (un seul compte) n'est plus lue: son compte devient l'admin de
-- `comptes`. La table est laissée en place (jamais d'édition ni de suppression
-- rétroactive d'un schéma).
--   admin    — tous les droits, gère les comptes et les réglages système
--   standard — l'appli de révision telle quelle
--   etendu   — comme standard + la page « Dépôt » (créer des cours, déposer
--              des fichiers à traiter plus tard avec /generer-questions)
CREATE TABLE comptes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    identifiant  TEXT NOT NULL UNIQUE,
    hash_mdp     TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('admin', 'standard', 'etendu')),
    cree_le      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO comptes (identifiant, hash_mdp, role, cree_le)
    SELECT identifiant, hash_mdp, 'admin', cree_le FROM web_user;

-- Fichiers déposés. Le contenu vit sur disque (FLASHCARDS_DEPOT), jamais en base;
-- `chemin` est relatif au dossier de dépôt. `traite_le` rempli = déjà utilisé
-- pour générer des questions.
CREATE TABLE depots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cours        TEXT NOT NULL,
    nom_original TEXT NOT NULL,
    chemin       TEXT NOT NULL UNIQUE,
    taille       INTEGER NOT NULL,
    depose_par   TEXT NOT NULL,
    depose_le    TEXT NOT NULL,
    traite_le    TEXT
);
CREATE INDEX idx_depots_cours ON depots (cours);
