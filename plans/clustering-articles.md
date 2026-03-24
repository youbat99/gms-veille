# Plan — Clustering d'articles similaires + Catégorisation NLP

**Date de réflexion :** 22 mars 2026
**Statut :** 📋 Planifié — pas encore démarré
**Priorité :** Moyenne (après onboarding test + signalements)

---

## Contexte & Problème

Les médias marocains reprennent souvent le même article (souvent de la MAP) avec des reformulations.
Résultat : le client reçoit 5-6 articles qui parlent du même événement, ce qui noie l'information.

### Exemple concret
```
MAP publie : "Pluies torrentielles au Maroc prévues ce week-end"
→ hespress.com  "زخات مطرية ودرجات حرارة تحت الصفر..."
→ goud.ma       "Fortes pluies attendues dans ces régions..."
→ hibapress.com "Journée météorologique mondiale..."
→ aswatnews.ma  "توقعات طقس اليوم بالمغرب..."
→ akhbarona.com "أمطار قوية متوقعة..."
```
→ 5 articles dans la revue pour 1 seul événement réel.

### Cas d'usage validés (double cible)
- **Admin** : valider 1 événement au lieu de 5 articles séparément → gain de temps
- **Client** : voir "5 médias ont couvert ce sujet" au lieu de 5 articles dupliqués → clarté

---

## Architecture cible

### Concept : l'Événement
Au lieu de penser "articles", on pense "événements". Un événement = un sujet couvert par N sources.

```
ÉVÉNEMENT : "Pluies torrentielles au Maroc — 22 mars"
├── hespress.com     similarity: 91%   (source probable)
├── aswatnews.ma     similarity: 88%
├── goud.ma          similarity: 85%
├── hibapress.com    similarity: 83%
└── akhbarona.com    similarity: 80%
```

---

## Phase 1 — Base de données (Jour 1)

### Nouvelles tables SQL

```sql
-- Table principale des clusters
CREATE TABLE article_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    revue_id UUID REFERENCES revues(id) ON DELETE CASCADE,
    title TEXT NOT NULL,           -- titre auto = article le + pertinent
    event_date DATE NOT NULL,
    article_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Membres du cluster
CREATE TABLE article_cluster_members (
    cluster_id UUID REFERENCES article_clusters(id) ON DELETE CASCADE,
    article_id UUID REFERENCES press_review_articles(id) ON DELETE CASCADE,
    similarity_score FLOAT,
    is_source BOOLEAN DEFAULT false,  -- article "origine" du cluster
    PRIMARY KEY (cluster_id, article_id)
);

-- Index
CREATE INDEX idx_clusters_revue_date ON article_clusters(revue_id, event_date DESC);
CREATE INDEX idx_cluster_members_article ON article_cluster_members(article_id);
```

### Modèles SQLAlchemy
- `backend/app/models/article_cluster.py`
- Classes : `ArticleCluster`, `ArticleClusterMember`
- Relations : vers `PressReviewArticle` et `Revue`

---

## Phase 2 — Moteur de similarité (Jour 1-2)

### Version 1 : TF-IDF / Jaccard sur titres (sans ML)

Fichier : `backend/app/services/clustering_service.py`

```python
STOPWORDS_FR = {"le", "la", "les", "de", "du", "au", "en", "et", "un", "une", ...}
STOPWORDS_AR = {"في", "من", "إلى", "على", "و", "أن", "هذا", "التي", "الذي", ...}
STOPWORDS_EN = {"the", "a", "an", "of", "in", "to", "and", "is", "that", ...}

def normalize_title(title: str) -> set[str]:
    """Tokenize + lowercase + remove stopwords (AR/FR/EN auto-detect)"""
    ...

def compute_similarity(title_a: str, title_b: str) -> float:
    """Jaccard sur tokens normalisés"""
    tokens_a = normalize_title(title_a)
    tokens_b = normalize_title(title_b)
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union) if union else 0.0

async def cluster_articles_for_revue(
    db: AsyncSession,
    revue_id: str,
    date: date,
    threshold: float = 0.35   # seuil Jaccard titre
) -> list[ArticleCluster]:
    """
    1. Récupère tous les articles approved/pending de la revue pour cette date
    2. Calcule similarité pairwise sur les titres
    3. Union-Find pour former les clusters
    4. Crée/met à jour article_clusters + article_cluster_members
    5. Retourne les clusters formés
    """
```

### Algorithme Union-Find
```
Articles : A B C D E

sim(A,B) = 0.82 ✅ → même cluster
sim(A,C) = 0.41 ✅ → même cluster
sim(A,D) = 0.12 ❌
sim(D,E) = 0.67 ✅ → même cluster

Résultat :
  Cluster 1 : [A, B, C]   → événement
  Cluster 2 : [D, E]      → événement
  Isolés    : [autres]    → articles normaux
```

### Seuil recommandé
- Jaccard titre : **0.35** (assez permissif pour AR avec morphologie variable)
- Si trop de faux positifs → monter à 0.45
- Si trop de manqués → descendre à 0.28

---

## Phase 3 — API Backend (Jour 2)

### Nouveaux endpoints

```
GET  /api/revues/{id}/clusters?date=2026-03-22
     → Liste des clusters du jour avec articles membres

POST /api/revues/{id}/clusters/compute
     → Déclenche le clustering manuellement (bouton admin)

GET  /api/clusters/{cluster_id}
     → Détail cluster + tous les articles membres

PATCH /api/clusters/{cluster_id}/validate
      → body: { action: "approve"|"reject", apply_to: "all"|"source_only" }
      → Valide tous les articles du cluster en une seule action
```

### Schemas Pydantic

```python
class ArticleInCluster(BaseModel):
    id: str
    title: str
    source_domain: str
    similarity_score: float
    is_source: bool       # source probable de l'info
    status: str
    published_at: datetime | None

class ClusterOut(BaseModel):
    id: str
    title: str
    event_date: date
    article_count: int
    pending_count: int
    articles: list[ArticleInCluster]
```

### Fichier : `backend/app/api/clusters.py`

### Worker automatique (main.py)
```python
async def _run_clustering_worker():
    """Toutes les 30min — re-cluster les articles des dernières 48h"""
    async with get_db() as db:
        active_revues = await get_active_revue_ids(db)
        for revue_id in active_revues:
            for d in [today, yesterday]:
                await clustering_service.cluster_articles_for_revue(db, revue_id, d)

_scheduler.add_job(
    _run_clustering_worker,
    IntervalTrigger(minutes=30),
    id="clustering_worker",
    replace_existing=True,
    max_instances=1,
)
```

---

## Phase 4 — Interface Admin (Jour 3)

### Article Monitoring — Toggle Articles / Événements

Nouveau toggle dans le header du panel gauche :
```
[📄 Articles]  [🔗 Événements]
```

### Vue Événements (groupée par date → mot-clé → cluster)

```
📅 Aujourd'hui  (8 événements · 5 en attente)

  🏷 Mèteo AR
    ┌─────────────────────────────────────────────┐
    │ 🔗 5 sources  "Pluies torrentielles..."     │ En attente
    │    hespress · goud · hibapress +2           │
    │    [✅ Approuver tout]  [❌ Rejeter tout]   │
    └─────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────┐
    │ 📄 1 source   "Météo du week-end..."        │ En attente
    │    aswatnews.ma                             │
    └─────────────────────────────────────────────┘
```

### Panel de validation groupée (droite)

```
Événement : "Pluies torrentielles au Maroc"
5 sources ont couvert ce sujet

┌── hespress.com ──────────── Source originale ───┐
│   [titre complet]                               │
│   Pertinence: 90%  ·  Tonalité: Neutre         │
└─────────────────────────────────────────────────┘
┌── goud.ma ──────────────── Similarité: 88% ────┐
│   [titre complet]                               │
└─────────────────────────────────────────────────┘
... (3 autres)

[✅ Approuver les 5]   [❌ Rejeter les 5]
[⚙ Valider un par un]
```

---

## Phase 5 — Interface Client (Jour 4)

### Fichier : `frontend/src/app/(client)/press-review/page.tsx`

### Vue condensée (défaut)

```
📰 Pluies torrentielles au Maroc              Aujourd'hui
   🗞 5 médias ont couvert ce sujet
   hespress · goud · hibapress · +2
   [Voir les 5 versions ↓]

📰 Budget 2026 : nouvelles mesures            Aujourd'hui
   🗞 3 médias
   leseco · telquel · leconomiste
```

### Vue dépliée

```
📰 Pluies torrentielles au Maroc
  ↓ hespress.com   "زخات مطرية ودرجات حرارة..."   [Lire ↗]
  ↓ goud.ma        "Fortes pluies attendues..."    [Lire ↗]
  ↓ hibapress.com  "Journée météorologique..."     [Lire ↗]
```

### Règles affichage client
- Texte intégral masqué (titre + extrait 2 lignes + lien source)
- Toggle : Vue condensée / Vue détaillée
- Articles isolés (1 source) affichés normalement

---

## Phase 6 — Upgrade ML (Semaine 2, optionnel)

À faire si les résultats TF-IDF sont insuffisants.

### Modèle : paraphrase-multilingual-MiniLM-L12-v2
- Taille : ~120MB
- CPU only (pas de GPU nécessaire)
- Langues : AR / FR / EN nativement
- Gratuit / open-source

### Installation
```bash
pip install sentence-transformers
# Ajouter dans requirements.txt
```

### Code
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

def compute_embedding(text: str) -> list[float]:
    return model.encode(text, normalize_embeddings=True).tolist()

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    import numpy as np
    a, b = np.array(vec_a), np.array(vec_b)
    return float(np.dot(a, b))  # normalisés → dot = cosine
```

### Stockage : pgvector
```sql
-- Activer l'extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Ajouter colonne embedding
ALTER TABLE press_review_articles
ADD COLUMN embedding vector(384);   -- 384 dims pour MiniLM

-- Index pour recherche rapide
CREATE INDEX idx_articles_embedding
ON press_review_articles
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

### Seuil cosine recommandé : **0.83**

---

## Récap fichiers à créer/modifier

| Fichier | Action | Phase |
|---------|--------|-------|
| `backend/app/models/article_cluster.py` | Créer | 1 |
| `backend/alembic/versions/xxx_clusters.py` | Créer migration | 1 |
| `backend/app/services/clustering_service.py` | Créer | 2 |
| `backend/app/api/clusters.py` | Créer | 3 |
| `backend/app/main.py` | Ajouter worker 30min | 3 |
| `backend/app/api/__init__.py` | Enregistrer router | 3 |
| `frontend/src/app/(app)/articles/page.tsx` | Toggle Events/Articles | 4 |
| `frontend/src/app/(client)/press-review/page.tsx` | Vue condensée client | 5 |
| `frontend/src/lib/api.ts` | Nouveaux appels clusters | 3 |
| `frontend/src/lib/types.ts` | Types Cluster + Member | 3 |

---

## Ordre d'exécution

```
Jour 1 matin  → DB migration + modèles SQLAlchemy
Jour 1 aprem  → clustering_service.py (TF-IDF + Union-Find)
Jour 2        → API endpoints + worker scheduler
Jour 3        → Interface admin (toggle + vue événements + validation groupée)
Jour 4        → Interface client (vue condensée + déplié)
Semaine 2     → Upgrade MiniLM si résultats insuffisants
```

---

## Points d'attention

- **Seuil à calibrer** : commencer à 0.35 Jaccard, ajuster après tests sur vraies données
- **Fréquence clustering** : toutes les 30min suffit, le clustering est idempotent (on peut recalculer)
- **Articles isolés** : ne pas les forcer dans un cluster — les afficher normalement
- **Langue arabe** : la morphologie est complexe (racines variables) → MiniLM sera meilleur que Jaccard pour l'AR
- **Performance** : pour N articles/jour par revue, pairwise = N² comparaisons. OK jusqu'à ~500 articles/jour. Au-delà → LSH (Locality Sensitive Hashing) ou pgvector ANN.

---

## Catégorisation thématique (NLP Kiosque) — Sujet connexe

### Objectif
Tagger automatiquement chaque article par thème : Politique / Économie / Sport / Culture / Société / International

### Option recommandée (court terme) : règles + mots-clés

```python
THEMES = {
    "politique": ["élection", "parlement", "ministre", "gouvernement", "حكومة", "وزير", "برلمان"],
    "économie":  ["budget", "PIB", "inflation", "investissement", "اقتصاد", "ميزانية", "استثمار"],
    "sport":     ["match", "équipe", "championnat", "فريق", "بطولة", "مباراة"],
    "culture":   ["festival", "cinéma", "musique", "théâtre", "مهرجان", "فن"],
    "société":   ["éducation", "santé", "logement", "تعليم", "صحة", "سكن"],
    "international": ["ONU", "Europe", "USA", "دولي", "أممي"],
}
```

### Option future : Zero-shot XLM-RoBERTa

```python
from transformers import pipeline

classifier = pipeline(
    "zero-shot-classification",
    model="joeddav/xlm-roberta-large-xnli"
)

result = classifier(
    article_title,
    candidate_labels=["politique", "économie", "sport", "culture", "société", "international"],
    hypothesis_template="Ce texte parle de {}."
)
# → {"labels": ["économie", "politique", ...], "scores": [0.87, 0.09, ...]}
```

### Plan catégorisation (séparé, ~2 jours)

```
Jour 1 → Règles multilingues (AR/FR/EN) dans un service dédié
          Intégration dans pipeline enrichissement (après trafilatura)
          Affichage dans Kiosque (filtre par thème)
Jour 2 → Tests + calibrage des règles sur vrais articles
Futur  → Upgrade zero-shot si précision insuffisante
```

---

*Plan rédigé le 22 mars 2026 — à lancer sur signal "go"*
