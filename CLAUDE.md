# CLAUDE.md — Veille Média Maroc (GMS)

Plateforme de veille médiatique marocaine. Pipeline complet : collecte RSS/SerpAPI/GDELT → extraction texte → matching keywords → validation humaine (HITL) → revue de presse client.

---

## Architecture générale

```
┌─────────────────────────────────────────────────────────┐
│  DATA LAYER                                             │
│  RSS crawl (10s) → Trafilatura/Newspaper4k → rss_articles │
│  SerpAPI (slots horaires) → articles                    │
│  GDELT / NewsData.io (6h) → rss_articles                │
└────────────────────────┬────────────────────────────────┘
                         │ matching keywords
┌────────────────────────▼────────────────────────────────┐
│  INTELLIGENCE LAYER                                     │
│  Claude API → résumé, tonalité, thème, entités, signal  │
│  Uniquement sur articles matchés (contrôle des coûts)   │
└────────────────────────┬────────────────────────────────┘
                         │ HITL validation
┌────────────────────────▼────────────────────────────────┐
│  CLIENT LAYER                                           │
│  Revue de presse → articles validés → export            │
└─────────────────────────────────────────────────────────┘
```

**Deux tables d'articles distinctes :**
- `rss_articles` — tout ce qui entre dans le pipeline (brut)
- `articles` — uniquement les articles validés/en attente HITL (enrichis par Claude)

---

## Stack technique

| Couche | Technologie |
|--------|-------------|
| Backend API | FastAPI + Uvicorn, Python async |
| ORM | SQLAlchemy 2.0 async (asyncpg) |
| Migrations | Alembic (JAMAIS `--autogenerate` en prod) |
| Scheduler | APScheduler (AsyncIOScheduler) |
| Extraction | Trafilatura, Newspaper4k, Playwright, feedparser |
| LLM | Claude (Anthropic) — principal |
| Base de données | PostgreSQL |
| Cache | Redis |
| Auth | JWT (python-jose + bcrypt) |
| Frontend | Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 |
| Charts | Recharts |

---

## Lancer le projet

### Backend
```bash
cd veille-media/backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd veille-media/frontend
npm run dev   # http://localhost:3000
```

**Prérequis :** PostgreSQL + Redis en local.

---

## Structure des dossiers

```
veille-media/
├── backend/
│   ├── app/
│   │   ├── main.py           ← entrée FastAPI + démarrage APScheduler
│   │   ├── core/
│   │   │   ├── config.py     ← variables d'env (Settings)
│   │   │   ├── database.py   ← engine async SQLAlchemy
│   │   │   └── deps.py       ← dépendances FastAPI (get_db, get_current_user)
│   │   ├── api/              ← routes HTTP
│   │   ├── models/           ← modèles SQLAlchemy
│   │   └── services/         ← logique métier
│   ├── alembic/versions/     ← migrations (≈17 fichiers)
│   └── .env                  ← secrets (ne pas committer)
└── frontend/
    └── src/app/
        ├── (app)/            ← pages authentifiées
        ├── hitl/             ← validation humaine
        └── admin/            ← administration
```

**Migrations Alembic (35 fichiers — dernière : `ab6c7d8e9f0a`)**

---

## Variables d'environnement (backend/.env)

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/veille_media
SERPAPI_KEY=...           # Google News — REQUIS
SECRET_KEY=...            # JWT secret — REQUIS
REDIS_URL=redis://localhost:6379/0
ANTHROPIC_API_KEY=...     # Claude API — REQUIS pour NLP
NEWSDATA_API_KEY=...      # optionnel
OPENAI_API_KEY=...        # optionnel (fallback LLM)
OPENROUTER_API_KEY=...    # optionnel (fallback LLM)
SYSTEM_START_DATE=2025-03-01
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

---

## API Routes

| Préfixe | Fichier | Rôle |
|---------|---------|------|
| `/auth` | `api/auth.py` | Login, /me |
| `/users` | `api/users.py` | CRUD comptes, accès revues |
| `/clients` | `api/clients.py` | CRUD clients, assignation revues |
| `/revues` | `api/revues.py` | CRUD revues, keywords, match-recent |
| `/articles` | `api/articles.py` | CRUD articles HITL, validation, export |
| `/collector` | `api/collector.py` | Collecte manuelle SerpAPI |
| `/scheduler` | `api/scheduler.py` | Slots horaires, run-now |
| `/stats` | `api/stats.py` | Dashboard opérationnel |
| `/collection-logs` | `api/collection_logs.py` | Historique collectes |
| `/api/media-sources` | `api/media_sources.py` | Sources médias, articles récents, crawl-now, GDELT discover |
| `/media-feed` | `api/media_feed.py` | Fil client, assignation sources |

---

## Modèles (tables clés)

### `rss_articles` — pipeline de collecte brut
```python
id, source_id (FK → media_sources, NULLABLE pour SerpAPI)
url, url_hash (unique)
status: "pending" | "extracted" | "matched" | "no_match" | "failed"
collection_method: "rss" | "sitemap" | "serpapi" | "playwright"
title, content (texte intégral), summary, author, published_at, image_url
detected_language, content_fingerprint (SimHash 16 chars)
```

### `articles` — HITL + revue de presse (34 colonnes)
```python
id, revue_id (FK), keyword_id (FK)
url, title, content, summary (EN/AR/FR)
tonality: "positive" | "neutral" | "negative"
theme: "politique" | "économie" | "société" | "sport" | "culture" | "international"
weak_signal: bool        # crise/signal faible — renseigné par Claude (actif depuis sprint 2)
tags: JSON               # 6-10 tags thématiques
entities_persons: JSON   # personnalités citées
entities_orgs: JSON      # organisations citées
entities_places: JSON    # lieux géographiques
key_themes: JSON         # 3-5 thèmes principaux développés (ajouté sprint 2)
market_impact: Text      # impact décideurs économiques 1-2 phrases (ajouté sprint 2)
relevance_score: Float   # 0.3 → 1.0 (plancher 0.3 = déjà passé filtre collecte)
matched_keywords: JSON   # tous les keywords qui ont matché
status: "pending" | "in_review" | "approved" | "modified" | "rejected" | "error"
collection_method: "serpapi" | "rss" | "sitemap" | "manual"
```

### `media_sources`
```python
id, name, base_url (unique), rss_url, rss_type
crawl_method: "rss" | "playwright" | "sitemap"
language: "ar" | "fr" | "en"
is_featured, is_active, last_crawled_at
```

### `revues` — revues de presse
```python
id, client_id (FK), name, description, is_active
→ keywords (via revue_keywords)
→ articles
→ scheduler_slots
```

### `accounts` — utilisateurs
```python
id, email (unique), full_name, hashed_password
role: "superadmin" | "admin" | "analyst" | "viewer"
client_id (FK, nullable)
```

---

## Services

| Service | Fichier | Rôle |
|---------|---------|------|
| `rss_service` | `services/rss_service.py` | Crawl RSS, extraction Trafilatura+Newspaper4k, enrichissement, purge |
| `rss_matching_service` | `services/rss_matching_service.py` | Match `rss_articles` → keywords → crée `articles` pending. Normalisation arabe/français |
| `nlp_service` | `services/nlp_service.py` | Appels Claude : résumé, tonalité, thème, entités, weak_signal |
| `collector_service` | `services/collector_service.py` | Collecte manuelle SerpAPI pour une revue |
| `serpapi_service` | `services/serpapi_service.py` | Wrapper SerpAPI |
| `gdelt_service` | `services/gdelt_service.py` | GDELT DOC API v2 — gratuit, 15min refresh |
| `newsdata_service` | `services/newsdata_service.py` | NewsData.io — 200 req/jour gratuit |
| `dedup_service` | `services/dedup_service.py` | SimHash fingerprint, distance Hamming (seuil=4) |
| `scraper_service` | `services/scraper_service.py` | Extraction Playwright (sites JS) |
| `auth_service` | `services/auth_service.py` | JWT, hash password |

---

## Workers APScheduler (main.py)

| Job | Intervalle | Rôle |
|-----|-----------|------|
| `rss_rolling_crawl` | 10s | **3 sources en parallèle** (asyncio.gather) — cycle ~12 min pour 209 sources |
| `rss_enrich_worker` | 20s | Extrait contenu des articles `pending` (batch 8) |
| `rss_match_worker` | 30s | Match `extracted` → keywords → `articles` (batch 50) |
| `retry_failed_worker` | 6h | Retry articles `failed` (batch 10) |
| `purge_worker` | 03:00 daily | Supprime `no_match`+`failed` > 30 jours |
| `gdelt_worker` | 6h | Collecte GDELT par keywords actifs |
| `newsdata_worker` | 6h | Collecte NewsData.io (si clé configurée) |
| `slot_*` (dynamique) | Heure:minute | Slots SerpAPI planifiés par revue |

**Crawl parallèle** : `_RSS_PARALLEL = 3` dans `main.py`. Sélection atomique des 3 sources les plus anciennes + update préventif `last_crawled_at` pour éviter double-sélection. Chaque source tourne dans sa propre session DB via `crawl_source_by_id()`.

---

## Pages Frontend

| Route | Fichier | Rôle |
|-------|---------|------|
| `/dashboard` | `(app)/dashboard/page.tsx` | Tableau de bord principal |
| `/press-review` | `(app)/press-review/page.tsx` | Revue de presse client — badge Signal Faible, key_themes, market_impact |
| `/articles` | `(app)/articles/page.tsx` | Articles en attente HITL |
| `/medias` | `(app)/medias/page.tsx` | Admin sources médias + **bibliothèque 360° articles** |
| `/media-feed` | `(app)/media-feed/page.tsx` | Fil d'actualité client |
| `/keywords` | `(app)/keywords/page.tsx` | Gestion keywords |
| `/collecte` | `(app)/collecte/page.tsx` | Slots de collecte planifiés (wizard 3 étapes, par-slot run-now) |
| `/crawl-history` | `(app)/crawl-history/page.tsx` | Historique crawls |
| `/analytics` | `(app)/analytics/page.tsx` | Analytics revue (tags, entités, key_themes, market_impact) |
| `/clients` | `(app)/clients/page.tsx` | Gestion clients |
| `/hitl/pending` | `hitl/pending/page.tsx` | File validation HITL |
| `/hitl/validated` | `hitl/validated/page.tsx` | Articles validés |
| `/admin/*` | `admin/` | Superadmin : users, revues, clients |

### Contextes React clés
- `AuthContext` — token JWT, utilisateur courant
- `RevueContext` — revue sélectionnée (filtre global de l'app)

### Config API frontend
```typescript
// src/lib/api.ts
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
```

---

## Tables supplémentaires (migrations récentes)

| Table | Migration | Rôle |
|-------|-----------|------|
| `scheduler_slot_keywords` | `x3y4z5a6b7c8` | Junction slot ↔ keyword (vide = tous les keywords) |
| `newsletter_configs` | `y4z5a6b7c8d9` | Config newsletter par revue (schedule, destinataires) |
| `email_logs` | `y4z5a6b7c8d9` | Historique envois newsletter |

## Indexes DB (ajoutés sprint 2)

```sql
-- articles
ix_articles_revue_id, ix_articles_status, ix_articles_keyword_id
ix_articles_revue_status  (composite — requête la plus fréquente)
-- rss_articles
ix_rss_articles_source_id, ix_rss_articles_status
ix_rss_articles_source_fingerprint  (composite — dédup)
ix_rss_articles_search_fts  (GIN tsvector — recherche full-text)
```

## NLP — champs générés et stockés

Tous générés par **Claude Haiku via OpenRouter** (`anthropic/claude-haiku-4-5`).
Fonction calling format (`analyze_article`), tous champs `required`.

| Champ | Stocké | Notes |
|-------|--------|-------|
| `summary` / `summary_en` / `summary_ar` | ✅ | 3 langues |
| `tonality` | ✅ | positive/neutral/negative |
| `tags` | ✅ | 6-10 tags |
| `relevance_score` | ✅ | plancher 0.3 |
| `theme` | ✅ | 6 catégories |
| `weak_signal` | ✅ | **actif depuis sprint 2** (était hardcodé False) |
| `entities_persons/orgs/places` | ✅ | JSON arrays |
| `key_themes` | ✅ | **ajouté sprint 2** |
| `market_impact` | ✅ | **ajouté sprint 2** |
| `tonality_justification` | ❌ | généré mais non stocké |

## Recherche full-text (rss_articles)

Endpoint `/api/media-sources/articles/recent?search=terme` utilise :
```sql
to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,''))
@@ plainto_tsquery('simple', 'terme')
```
Dictionnaire `'simple'` = tokenisation sans stemming → supporte AR/FR/EN.
Fallback sur `ILIKE` si erreur.

## Scheduler slots (collecte planifiée)

- Table `scheduler_slots` + `scheduler_slot_keywords` (junction)
- `keyword_ids` vide = run tous les keywords actifs (backward compat)
- Endpoint `POST /scheduler/slots/{slot_id}/run-now` → 202 Accepted, fire-and-forget
- Endpoint `POST /scheduler/run-now?keyword_ids=uuid1,uuid2` → global
- Wizard 3 étapes : ① Heure → ② Mots-clés → ③ Paramètres

## Newsletter (Resend)

- Service : **Resend** (resend.com) — free tier 3k emails/mois
- Clé API dans `.env` : `RESEND_API_KEY=re_3pSRf27d_...`
- Tables : `newsletter_configs` (1 par revue) + `email_logs`
- **À implémenter** : `services/email_service.py`, `api/newsletter.py`, page `/newsletter`
- Template prévu : par mot-clé → par tonalité, synthèse Claude, CTA plateforme

---

## Points d'attention critiques

### Pipeline état `rss_articles`
```
pending → extracted → matched   (→ article créé en HITL)
                   → no_match   (archivé, purgé après 30j)
       → failed                 (retry toutes 6h)
```

### SerpAPI articles (source_id = NULL)
Les articles collectés via SerpAPI n'ont pas de `source_id`. Toutes les requêtes sur `rss_articles` qui joignent `media_sources` **doivent utiliser `outerjoin`** (LEFT JOIN), sinon ces articles sont invisibles.

### Claude — règle des coûts
Claude n'est appelé **que sur les articles matchés** (`status="matched"`), jamais sur le flux RSS brut. Respecter absolument cette règle.
Modèle utilisé : `anthropic/claude-haiku-4-5` via OpenRouter (`OPENROUTER_API_KEY`).

### Migrations Alembic
- Exécuter **manuellement** en prod : `alembic upgrade head`
- **Ne jamais** utiliser `--autogenerate` en production
- Vérifier les migrations avant d'appliquer

### Déduplication
`dedup_service.py` utilise SimHash (Trafilatura `content_fingerprint`). Distance de Hamming ≤ 4 = doublon. Index sur `(source_id, content_fingerprint)`.

### Normalisation texte arabe
`rss_matching_service.py` normalise tashkeel + variantes alef/ya/ta marbuta avant le matching keywords. Idem pour les accents français.

---

## Flux de données complet

```
1. rss_rolling_crawl (10s)
   → feedparser → liste d'URLs
   → url_hash → dédup → insert rss_articles (status=pending)

2. rss_enrich_worker (20s)
   → Trafilatura extract(url) → title, content, author, date
   → fingerprint SimHash → dédup near-duplicate
   → status=extracted

3. rss_match_worker (30s)
   → compare title+content avec keywords actifs de toutes revues
   → si match → crée Article(status=pending) dans table articles
   → status=matched (ou no_match)

4. nlp_service (déclenché sur Article pending)
   → Claude API → summary, tonality, theme, entities, weak_signal
   → Article enrichi → status=in_review

5. HITL (utilisateur)
   → approve / reject / modify
   → Article(status=approved) visible dans revue de presse client

6. Slots SerpAPI (heure planifiée)
   → SerpAPI Google News → articles directs dans `articles` (bypass rss_articles)
```

---

## Commandes utiles

```bash
# Migrations
cd backend && alembic upgrade head
alembic revision -m "description"  # créer migration vide

# Tester un endpoint backend
curl http://localhost:8000/api/media-sources/articles/recent?status=all&limit=5

# Logs backend (si uvicorn en cours)
# Visible dans le terminal uvicorn

# Rebuild frontend
cd frontend && npm run build
```

---

## Inspiration — lekiosk.ma (analysé le 2026-03-21)

**Site :** https://www.lekiosk.ma
**Concept :** Agrégateur d'actualités marocaines FR (économie + politique), sélection éditoriale de 11 sources, gratuit, monétisé AdSense.

### Architecture technique lekiosk
- Backend Python + Gunicorn sur Render.com, derrière Cloudflare
- SPA vanilla JS (zéro framework), un seul fichier `app.js`
- **Une seule route API** : `GET /api/articles` → retourne ~460 articles JSON sans auth
- Structure article : `{id, title, url, source, category, published_at}` — 6 champs seulement
- Scraping 2-3x/jour (sessions batch, ~20 sessions/7 jours)
- RSS pour Hespress, LesEco, LaVieEco + scraping HTML pour les autres
- **Catégorie extraite du tag RSS `<category>` ou du chemin URL** (pas de NLP)
- Fenêtre 7 jours glissants, filtre côté client uniquement
- Newsletter via Google Forms

### Sources lekiosk (11 sources FR uniquement)
`Hespress · TelQuel · Le360 · LeMatin · LaVieEco · LesEco · BourseNews · H24info · SNRTNews · LaQuotidienne · fnh.ma`

### Catégories lekiosk (5)
`/Economie · /Enreprises · /Finance · /Politique · /Société`

### Mapping catégorie par URL (leur technique)
```
h24info.ma/economie/...     →  /Economie
lematin.ma/nation/...       →  /Politique
lavieeco.com/affaires/...   →  /Enreprises
le360.ma/politique/...      →  /Politique
boursenews.ma/article/...   →  /Finance  (toute la source = Finance)
```

### Ce qu'on peut s'en inspirer pour GMS

**1. Catégorisation gratuite par URL (sans Claude)**
Avant d'appeler Claude, pré-remplir `theme` depuis le chemin URL :
```python
URL_THEME_MAP = {
    "economie": "économie", "finance": "économie", "business": "économie",
    "politique": "politique", "nation": "politique",
    "sport": "sport", "societe": "société", "culture": "culture",
}
def guess_theme_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    for keyword, theme in URL_THEME_MAP.items():
        if f"/{keyword}/" in path:
            return theme
    return None
```
→ Réduit les coûts Claude, thème rempli même sur articles non-matchés.

**2. Intégrer l'API lekiosk comme source de collecte**
L'API est ouverte et retourne des articles déjà catégorisés :
```python
# backend/app/services/lekiosk_service.py
async def fetch_lekiosk_articles() -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://www.lekiosk.ma/api/articles")
        return r.json()
# → Worker 6h dans main.py, collection_method="lekiosk"
```

**3. Vue "Kiosk" par jour dans /media-feed**
lekiosk affiche tout par jour (pas de filtre keyword). GMS peut faire pareil
dans la bibliothèque des articles : regrouper par date comme lekiosk.
C'est la vision 360° déjà activée, mais avec UX "day-card" chronologique.

**4. Newsletter digest quotidien**
lekiosk prouve que les lecteurs veulent recevoir l'essentiel par email.
→ Déjà dans le roadmap Sprint 5 (digest 08h + alerte weak_signal).

**5. Fils thématiques transversaux (nouveau modèle commercial)**
lekiosk filtre par thème indépendamment des clients.
GMS pourrait proposer des abonnements verticaux :
- Pack Eau & Énergie, Pack Finances Publiques, Pack Transport...
- URL : `/themes/eau-et-energie` → tous articles matchant ce secteur

### Ce qu'on ne doit PAS copier de lekiosk
- Pas d'arabe → GMS est marocain, l'arabe est essentiel
- Pas de NLP/résumé → la valeur de GMS = l'intelligence sur les articles
- Pas d'auth → GMS est B2B, données clients confidentielles
- 11 sources FR seulement → GMS = 209 sources AR+FR+EN

---

## Roadmap sprints

### ✅ Sprint 1 — Collecte planifiée & UX
- Scheduler slots avec sélection par mot-clé (wizard 3 étapes)
- Per-slot run-now (`POST /slots/{id}/run-now`)
- Auto-reload logs après run, badge "Tous (N)", next exec time
- Global launch avec filtre keyword_ids

### ✅ Sprint 2 — Performance & Intelligence (2026-03-23)
- `weak_signal` : plus hardcodé à False, Claude le détecte maintenant
- Crawl RSS parallèle : 3 sources/tick → cycle ~12 min (était 35 min)
- Index GIN tsvector sur `rss_articles` (recherche full-text)
- 7 indexes DB sur `articles` + `rss_articles`
- Stockage `key_themes` + `market_impact` (migration + model + API + frontend)
- Affichage dans press-review : Thèmes clés (teal) + Impact marché (amber)
- Endpoint `/trending` enrichi : `key_themes` top 20 + `market_impacts`

### 🔜 Sprint 3 — Newsletter (priorité haute)
- `services/email_service.py` : template HTML par mot-clé → tonalité
- Synthèse exécutive Claude avant les articles
- `api/newsletter.py` : send-now, test, logs, config
- Page `/newsletter` dans la nav gauche
- APScheduler job par revue

### 🔜 Sprint 4 — Reporting & Analytics
- Page `/analytics` : word cloud interactif des tags
- Top personnes/orgs/lieux cliquables → filtrer les articles
- Graphiques `key_themes` + `market_impact`
- Fréquence articles par tonalité dans le temps

### 🔜 Sprint 5 — Scaling & Robustesse
- Rate limiting (slowapi) : `/auth/login` 5/min, autres 60/min
- Afficher les clusters d'articles similaires (calculés, jamais affichés)
- Pagination curseur sur tous les endpoints manquants
- États d'erreur HITL (scraping failed → afficher raison + retry)
- Worker health dashboard `/stats/workers`
