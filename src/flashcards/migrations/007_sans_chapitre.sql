-- Plus de « chapitre »: le thème (cours) est l'unique niveau sous la matière.
-- Les cartes et les tests ne gardent que leur thème; le filtre de chapitres
-- d'un test disparaît (un test couvre ses thèmes entiers).
DROP INDEX IF EXISTS idx_cards_cours_chapitre;
DROP INDEX IF EXISTS idx_cards_branche_chapitre;
ALTER TABLE cards DROP COLUMN chapitre;
CREATE INDEX IF NOT EXISTS idx_cards_cours ON cards(cours);
DROP TABLE IF EXISTS exam_chapitres;
