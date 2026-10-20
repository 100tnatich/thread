-- Protection du crédit API: registre de consommation, cache d'idempotence,
-- et verrou d'unicité sur les tâches de fond.

-- 1. Registre: une ligne par appel LLM TENTÉ, y compris les refus.
--    C'est la source de vérité des plafonds et la seule façon de savoir
--    combien a été dépensé.
CREATE TABLE llm_appels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage      TEXT NOT NULL,          -- ISO UTC
    operation       TEXT NOT NULL,          -- extraction | generation
    modele          TEXT NOT NULL,
    tokens_entree   INTEGER NOT NULL DEFAULT 0,
    tokens_sortie   INTEGER NOT NULL DEFAULT 0,
    cout_usd        REAL NOT NULL DEFAULT 0.0,
    cout_estime_usd REAL NOT NULL DEFAULT 0.0,
    statut          TEXT NOT NULL,          -- succes | echec | refuse
    detail          TEXT,                   -- raison du refus / de l'échec
    empreinte       TEXT,                   -- hash de l'entrée (anti-doublon)
    duree_ms        INTEGER
);

CREATE INDEX idx_llm_appels_horodatage ON llm_appels(horodatage);
CREATE INDEX idx_llm_appels_statut ON llm_appels(statut);

-- 2. Cache d'idempotence: même entrée + même opération = on réutilise le
--    résultat au lieu de repayer un appel.
CREATE TABLE llm_cache (
    empreinte   TEXT PRIMARY KEY,
    operation   TEXT NOT NULL,
    modele      TEXT NOT NULL,
    resultat    TEXT NOT NULL,              -- JSON de la réponse validée
    cree_le     TEXT NOT NULL,
    reutilise   INTEGER NOT NULL DEFAULT 0  -- compteur d'économies
);

-- 3. Verrou sur les tâches de fond.
--    Une empreinte identifie le travail demandé: deux soumissions du même
--    contenu ne créent qu'un job. L'index partiel garantit en plus qu'un
--    seul job tourne à la fois, au niveau de la base — un double clic ne
--    peut donc pas passer entre deux vérifications applicatives.
ALTER TABLE jobs ADD COLUMN empreinte TEXT;

CREATE UNIQUE INDEX idx_jobs_un_seul_en_cours
    ON jobs ((1)) WHERE statut = 'en_cours';
