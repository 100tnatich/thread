-- Récap théorique d'un thème: page Markdown lue avant de commencer une session
-- (rappel des formules, règles et pièges). Vide = pas de récap pour ce thème.
ALTER TABLE cours ADD COLUMN recap TEXT NOT NULL DEFAULT '';
