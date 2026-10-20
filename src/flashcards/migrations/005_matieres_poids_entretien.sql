-- Matières (parent optionnel des cours), poids des cartes, tests multi-cours,
-- archivage. Les données existantes sont conservées: chaque cours actuel
-- devient un thème sans matière, chaque test reste lié à son cours.

-- 1. Matières. `profil` est le profil de la matière (texte libre, même format
--    que profiles/<cours>.md); vide = on retombe sur profiles/<matière>.md.
CREATE TABLE matieres (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nom     TEXT NOT NULL UNIQUE,
    couleur TEXT NOT NULL DEFAULT '#8B7CFF',
    profil  TEXT NOT NULL DEFAULT '',
    cree_le TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. Un cours est un thème. Matière optionnelle (SET NULL: supprimer une
--    matière ne supprime ni ses thèmes ni leurs cartes). `profil` surcharge
--    celui de la matière. `archive`: réversible, sort le thème du planning
--    (et de l'entretien) sans toucher à ses cartes.
ALTER TABLE cours ADD COLUMN matiere_id INTEGER REFERENCES matieres(id) ON DELETE SET NULL;
ALTER TABLE cours ADD COLUMN profil TEXT NOT NULL DEFAULT '';
ALTER TABLE cours ADD COLUMN archive INTEGER NOT NULL DEFAULT 0;

-- 3. Poids des cartes. `poids` explicite et nullable (NULL = résolu par la
--    catégorie puis par le type, voir config.toml). `categorie` = catégorie du
--    concept d'origine (vocabulaire, formule…), NULL quand elle n'est pas connue.
ALTER TABLE cards ADD COLUMN poids REAL;
ALTER TABLE cards ADD COLUMN categorie TEXT;

-- 4. Tests liés à PLUSIEURS cours, et/ou à toute une matière (les thèmes
--    ajoutés plus tard à la matière sont couverts automatiquement).
CREATE TABLE exam_cours (
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    cours   TEXT    NOT NULL REFERENCES cours(nom) ON DELETE CASCADE,
    PRIMARY KEY (exam_id, cours)
);
CREATE TABLE exam_matieres (
    exam_id    INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    matiere_id INTEGER NOT NULL REFERENCES matieres(id) ON DELETE CASCADE,
    PRIMARY KEY (exam_id, matiere_id)
);

INSERT INTO exam_cours (exam_id, cours) SELECT id, cours FROM exams;

-- `exams.cours` disparaît: on recrée la table (les FK sont coupées pendant
-- les migrations, voir db.migrate).
CREATE TABLE exams_new (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,
    intitule TEXT NOT NULL
);
INSERT INTO exams_new (id, date, intitule) SELECT id, date, intitule FROM exams;
DROP TABLE exams;
ALTER TABLE exams_new RENAME TO exams;

CREATE INDEX idx_cours_matiere ON cours(matiere_id);
CREATE INDEX idx_exam_cours_cours ON exam_cours(cours);
