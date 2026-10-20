-- Retire l'ancienne clé de regroupement des payloads JSON déjà stockés: seul le
-- thème (cours) situe encore une carte.
UPDATE cards SET payload = json_remove(payload, '$.chapitre')
WHERE json_extract(payload, '$.chapitre') IS NOT NULL;
