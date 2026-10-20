-- Prototype 2: schéma initial du moteur de planification.

CREATE TABLE branches (
    nom TEXT PRIMARY KEY
);

CREATE TABLE exams (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    branche     TEXT NOT NULL REFERENCES branches(nom) ON DELETE CASCADE,
    date        TEXT NOT NULL,          -- ISO date (YYYY-MM-DD)
    intitule    TEXT NOT NULL
);

-- Chapitres couverts par un examen. Le lien carte <-> examen se fait par
-- (branche, chapitre) et non par id de carte: ainsi une carte importée APRÈS
-- la création de l'examen est automatiquement couverte.
CREATE TABLE exam_chapitres (
    exam_id  INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    chapitre TEXT NOT NULL,
    PRIMARY KEY (exam_id, chapitre)
);

CREATE TABLE cards (
    -- id de la question du prototype 1 (uuid), ancrage stable.
    id          TEXT PRIMARY KEY,
    concept_id  TEXT NOT NULL,
    branche     TEXT NOT NULL REFERENCES branches(nom) ON DELETE CASCADE,
    chapitre    TEXT NOT NULL,
    type        TEXT NOT NULL,
    -- Payload Pydantic complet de la question (modèles du prototype 1,
    -- réutilisés tels quels). Stocké en JSON pour ne pas dupliquer la
    -- taxonomie dans le schéma SQL.
    payload     TEXT NOT NULL,
    source_fichier TEXT,
    source_page    INTEGER,
    imported_at    TEXT NOT NULL,

    -- État FSRS brut. `due` n'est PAS stocké: il est recalculé à la demande
    -- depuis (stability, last_review) + les examens courants, pour que
    -- déplacer un examen replanifie tout sans migration de données.
    fsrs_state      INTEGER NOT NULL DEFAULT 1,   -- fsrs.State: 1=Learning
    fsrs_step       INTEGER,
    fsrs_stability  REAL,
    fsrs_difficulty REAL,
    last_review     TEXT                           -- ISO datetime UTC
);

CREATE INDEX idx_cards_branche_chapitre ON cards(branche, chapitre);
CREATE INDEX idx_cards_last_review ON cards(last_review);

CREATE TABLE reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id      TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reviewed_at  TEXT NOT NULL,     -- ISO datetime UTC
    rating       INTEGER NOT NULL,  -- fsrs.Rating 1..4
    duration_ms  INTEGER,
    -- Trace de l'origine de la note, utile pour juger la qualité du mapping.
    correct      INTEGER,           -- 1/0/NULL (NULL = pas de correction auto
    source       TEXT NOT NULL      -- 'auto' | 'manuel' | 'juge'
);

CREATE INDEX idx_reviews_card ON reviews(card_id);
CREATE INDEX idx_reviews_date ON reviews(reviewed_at);
