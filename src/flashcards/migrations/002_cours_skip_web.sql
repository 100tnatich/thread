-- Prototype 3: cours (ex-branches), skip, désactivation, jobs, utilisateur web.

-- 1. Cours: la table `branches` ne portait qu'un nom. Un cours a en plus une
--    couleur d'affichage choisie dans l'interface.
CREATE TABLE cours (
    nom        TEXT PRIMARY KEY,
    couleur    TEXT NOT NULL DEFAULT '#8B7CFF',
    cree_le    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO cours (nom) SELECT nom FROM branches;

-- 2. cards: branche -> course, + désactivation ("question mauvaise").
--    SQLite ne sait pas renommer une colonne référencée par une FK de façon
--    fiable sur les vieilles versions: on recrée proprement la table.
CREATE TABLE cards_new (
    id          TEXT PRIMARY KEY,
    concept_id  TEXT NOT NULL,
    cours       TEXT NOT NULL REFERENCES cours(nom) ON DELETE CASCADE,
    chapitre    TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    source_fichier TEXT,
    source_page    INTEGER,
    imported_at    TEXT NOT NULL,

    -- Exclue des révisions tant qu'elle n'est pas corrigée.
    desactivee     INTEGER NOT NULL DEFAULT 0,
    note_qualite   TEXT,

    fsrs_state      INTEGER NOT NULL DEFAULT 1,
    fsrs_step       INTEGER,
    fsrs_stability  REAL,
    fsrs_difficulty REAL,
    last_review     TEXT
);

INSERT INTO cards_new (
    id, concept_id, cours, chapitre, type, payload, source_fichier,
    source_page, imported_at, fsrs_state, fsrs_step, fsrs_stability,
    fsrs_difficulty, last_review
)
SELECT id, concept_id, branche, chapitre, type, payload, source_fichier,
       source_page, imported_at, fsrs_state, fsrs_step, fsrs_stability,
       fsrs_difficulty, last_review
FROM cards;

DROP TABLE cards;
ALTER TABLE cards_new RENAME TO cards;

CREATE INDEX idx_cards_cours_chapitre ON cards(cours, chapitre);
CREATE INDEX idx_cards_last_review ON cards(last_review);
CREATE INDEX idx_cards_desactivee ON cards(desactivee);

-- 3. exams: branche -> course.
CREATE TABLE exams_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cours       TEXT NOT NULL REFERENCES cours(nom) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    intitule    TEXT NOT NULL
);

INSERT INTO exams_new (id, cours, date, intitule)
SELECT id, branche, date, intitule FROM exams;

DROP TABLE exams;
ALTER TABLE exams_new RENAME TO exams;

DROP TABLE branches;

-- 4. Skips de session. Un skip n'est PAS une réponse: il ne touche jamais
--    l'état FSRS. On compte les passages pour qu'une même question ne puisse
--    pas boucler indéfiniment dans une session.
CREATE TABLE session_skips (
    session_id  TEXT NOT NULL,
    card_id     TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    jour        TEXT NOT NULL,
    nb_skips    INTEGER NOT NULL DEFAULT 1,
    raison      TEXT,
    dernier_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, card_id)
);

CREATE INDEX idx_skips_jour ON session_skips(jour);

-- 5. Tâches longues (extraction / génération) suivies par le front.
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    statut      TEXT NOT NULL,          -- en_cours | termine | echoue
    progression INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    resultat    TEXT,                   -- JSON
    cree_le     TEXT NOT NULL,
    fini_le     TEXT
);

-- 6. Utilisateur unique de l'interface web.
CREATE TABLE web_user (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    identifiant   TEXT NOT NULL,
    hash_mdp      TEXT NOT NULL,
    cree_le       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 7. Réglages modifiables depuis l'interface (surchargent config.toml).
CREATE TABLE settings (
    cle     TEXT PRIMARY KEY,
    valeur  TEXT NOT NULL
);
