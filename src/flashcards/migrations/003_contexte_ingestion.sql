-- La génération a désormais besoin de la matière source (le nouveau prompt la
-- rejoint pour reprendre les valeurs et la notation exactes du document).
-- On mémorise donc, sur le job d'extraction, de quoi la reconstituer: la voie,
-- les fichiers stockés et le texte collé.
--
-- Stocké côté serveur et jamais renvoyé au client: la génération référence le
-- job d'extraction par son id plutôt que de faire transiter des chemins
-- disque par le navigateur.
ALTER TABLE jobs ADD COLUMN contexte TEXT;
