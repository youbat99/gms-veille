# Roadmap Améliorations — Veille Média GMS

> Audit réalisé le 2026-03-21. Ce qui était dans le plan initial (Phases 1-6) est **déjà implémenté**.
> Ce document couvre les améliorations suivantes.

---

## État actuel du projet

### ✅ Déjà en place
- Pipeline complet RSS → extraction → matching → HITL → revue
- Déduplication SimHash (near-duplicate, seuil 5 bits)
- Normalisation arabe (tashkeel, alef, ya, ta marbuta)
- GDELT + NewsData.io workers (6h)
- Claude NLP : résumé, tonalité, thème, entités, mots-clés
- LEFT JOIN SerpAPI (articles sans source_id visibles)
- Bibliothèque 360° articles (filtre "Tous" par défaut)
- Filtres statut + langue dans la bibliothèque
- Santé des sources (badge ✅/⚠️/🔴 dans /medias)
- Filtre is_active sur le fil client

### ⚠️ Bugs connus à ce jour
| Bug | Impact | Complexité |
|-----|--------|-----------|
| `crawl_now` ne passe pas `source_id` → articles orphelins | Moyen | Faible |
| `weak_signal` toujours `False` (hardcodé, jamais détecté par Claude) | Moyen | Faible |
| `matched_keywords` absent de `RssArticle` (présent sur `Article` seulement) | Faible | Faible |

---

## SPRINT 1 — Correctifs critiques (2-3 jours)

### 1.1 Fix crawl_now → source_id manquant
**Fichier :** `backend/app/api/collector.py` + `services/collector_service.py`
**Problème :** Quand on lance un crawl manuel depuis l'interface, les articles créés ont `source_id=NULL`.
```python
# Passer source_id au moment de la création de RssArticle
article = RssArticle(
    source_id=revue.source_id,  # ← manquant actuellement
    url=result["url"],
    ...
)
```
**Vérification :** Lancer crawl_now → tous les articles ont un source_id non-null.

---

### 1.2 Weak signal — détection automatique par Claude
**Fichier :** `backend/app/services/nlp_service.py`
**Problème :** `weak_signal` est toujours `False`. Claude devrait le détecter.
```python
# Dans le prompt Claude, ajouter :
"weak_signal": bool  # True si l'article révèle :
  # - risque réputationnel pour le client
  # - crise naissante (avant couverture massive)
  # - contradiction avec communication officielle
  # - signal précoce (tendance, changement réglementaire)
```
**Vérification :** Article sur une crise → `weak_signal=True` visible avec badge 🚨 dans la revue.

---

### 1.3 matched_keywords sur RssArticle
**Fichier :** `backend/app/models/rss_article.py` + migration Alembic
**Problème :** La bibliothèque ne peut pas afficher quels keywords ont matché un article.
```sql
ALTER TABLE rss_articles ADD COLUMN matched_keywords JSONB;
```
```python
# Dans rss_matching_service.py, stocker sur RssArticle :
rss_article.matched_keywords = [{"term": kw.term, "score": score}]
```
**Vérification :** Bibliothèque des articles → badges keywords verts sur les articles matchés.

---

## SPRINT 2 — Performance & Scalabilité (3-4 jours)

### 2.1 Accélérer le rolling crawl
**Problème :** Avec 209 sources et 1 source toutes les 10s → cycle complet en ~35 minutes.
Pour une source qui publie toutes les heures, les articles arrivent avec un retard acceptable.
Mais si on dépasse 300 sources, ça devient un problème.

**Solution :** Prioriser les sources actives récentes + parallélisme léger.
```python
# Dans rss_service.py — crawl_next_source()
# Priorité : sources qui ont publié récemment (last_crawled_at le plus ancien ET is_featured)
# Batch de 3 sources en parallèle (au lieu de 1)
async with asyncio.TaskGroup() as tg:
    for source in next_3_sources:
        tg.create_task(crawl_source(source))
```
**Gain estimé :** Cycle de 35min → ~12min.

---

### 2.2 Recherche full-text PostgreSQL
**Problème :** La recherche dans la bibliothèque fait un `ILIKE %term%` → lent sur 100k+ articles.
**Solution :** Index GIN full-text PostgreSQL.
```sql
ALTER TABLE rss_articles ADD COLUMN search_vector tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,''))
    ) STORED;
CREATE INDEX idx_rss_articles_search ON rss_articles USING GIN(search_vector);
```
```python
# Dans media_sources.py, remplacer ILIKE par :
.where(RssArticle.search_vector.op('@@')(func.plainto_tsquery('simple', search)))
```
**Gain :** Recherche 10ms au lieu de 500ms+ sur gros volumes.

---

### 2.3 Pagination infinie côté bibliothèque
**Problème :** La bibliothèque charge 50 articles et bloque. Pas de lazy loading.
**Fichier :** `frontend/src/app/(app)/medias/page.tsx`
**Solution :** IntersectionObserver pour charger la page suivante automatiquement quand l'utilisateur arrive en bas.
```tsx
const observer = new IntersectionObserver(entries => {
    if (entries[0].isIntersecting && hasMore) loadMore()
})
```

---

## SPRINT 3 — Monitoring & Alertes (3-4 jours)

### 3.1 Dashboard santé des workers
**Problème :** Si un worker crashe, personne ne le sait. Pas de visibilité sur le pipeline.
**Nouveau endpoint :** `GET /stats/workers`
```python
{
  "rss_crawl": {"last_run": "2026-03-21T01:00:00Z", "articles_last_hour": 142, "errors": 0},
  "enrich": {"pending_queue": 23, "failed_last_24h": 5},
  "match": {"unmatched_queue": 0, "articles_created_today": 87},
  "gdelt": {"last_run": "2026-03-21T00:00:00Z", "articles_fetched": 34},
}
```
**Frontend :** Nouveau widget dans `/dashboard` → indicateurs pipeline en temps réel.

---

### 3.2 Alertes sources silencieuses
**Problème :** Si hespress.com arrête de publier (panne, blocage, changement RSS), personne ne le sait.
**Solution :** Job daily qui vérifie les sources sans articles depuis > 48h.
```python
# Dans main.py — job quotidien 09:00
@scheduler.scheduled_job('cron', hour=9, id='source_health_alert')
async def check_silent_sources():
    # Sources actives sans article depuis 48h → log + notification
    threshold = datetime.now(UTC) - timedelta(hours=48)
    silent = await db.execute(
        select(MediaSource)
        .where(MediaSource.is_active == True, MediaSource.last_crawled_at < threshold)
    )
    # → Envoyer alerte email admin
```
**Frontend :** Badge 🔴 "Silencieuse" sur les sources dans /medias.

---

### 3.3 Logs de collecte détaillés
**Problème :** `collection_logs` existe mais peu d'infos sur pourquoi une source échoue.
**Amélioration :** Stocker le type d'erreur (timeout, 403, paywall, RSS vide, etc.)
```python
# Dans rss_service.py
log.error_type = "paywall"  # "timeout" | "http_403" | "empty_rss" | "parse_error"
log.articles_new = N_inserted
log.articles_duplicate = N_dedup
log.articles_failed = N_failed
```

---

## SPRINT 4 — Intelligence Layer : améliorations Claude (4-5 jours)

### 4.1 Analyse de tendance (clustering thématique)
**Idée :** Détecter quand plusieurs articles différents parlent du même sujet → regrouper en "cluster événement".
```python
# Nouveau service : trend_service.py
# Toutes les 2h : comparer embeddings des articles matchés des 24 dernières heures
# Si 3+ articles similaires → créer un "événement" lié à la revue
# Client voit : "📌 Tendance : 8 articles sur la réforme fiscale"
```
**Modèle :** Utiliser Claude pour identifier si deux articles parlent du même événement.

---

### 4.2 Résumé de revue quotidien (auto-généré)
**Idée :** À 08:00 chaque matin, générer automatiquement un résumé exécutif de la veille.
```python
# Job cron 08:00 — pour chaque revue active :
# Prendre tous les articles approuvés des dernières 24h
# → Claude génère un brief exécutif (5-7 points clés)
# → Stocké dans revue_daily_brief (nouvelle table)
# → Accessible depuis la revue de presse
```
**Valeur :** Le client ouvre l'app le matin et voit directement l'essentiel.

---

### 4.3 Score de pertinence affiné
**Problème :** Le `relevance_score` est calculé par Claude mais sans contexte du profil client.
**Amélioration :** Passer le profil du client dans le prompt Claude.
```python
# Dans nlp_service.py :
client_context = f"""
Client: {client.name}
Secteur: {client.sector}
Sujets sensibles: {client.sensitive_topics}
"""
# → Score plus pertinent (article "eau" = score 0.9 pour MEE, 0.2 pour un opérateur télécom)
```

---

## SPRINT 5 — Expérience client (3-4 jours)

### 5.1 Notifications push (email)
**Problème :** Le client doit ouvrir l'app pour voir les nouvelles. Pas d'alertes.
**Solution :** Email digest quotidien + alerte immédiate pour `weak_signal=True`.
```python
# Nouveau service : notification_service.py
# Trigger 1 : Article approuvé avec weak_signal=True → email immédiat
# Trigger 2 : Digest quotidien 08:00 → résumé articles validés depuis hier
# Config par revue : email_alert_weak_signal, email_digest_enabled
```

---

### 5.2 Export PDF revue de presse
**Problème :** L'export existe mais en Excel seulement. Le client veut un PDF élaboré.
**Solution :** Template PDF avec mise en page professionnelle.
```python
# Utiliser weasyprint ou reportlab
# Format : page de garde (logo client, période, nom revue)
# → Articles par thème, avec résumé, tonalité, source
# → Page de synthèse finale (points clés, tendances)
```

---

### 5.3 Vue mobile optimisée
**Problème :** L'interface est responsive mais pas optimisée pour mobile.
**Pages prioritaires à optimiser :**
- `/press-review` — lecture articles sur téléphone
- `/media-feed` — fil d'actualité mobile
- `/hitl/pending` — validation depuis téléphone (terrain)

---

### 5.4 Recherche globale (⌘K)
**Idée :** Raccourci clavier pour rechercher dans tout le contenu (articles, sources, revues).
```tsx
// Composant GlobalSearch.tsx
// ⌘K → modal de recherche
// Recherche simultanée : articles, sources médias, keywords
// Résultats avec navigation directe
```

---

## SPRINT 6 — Robustesse & Sécurité (2-3 jours)

### 6.1 Rate limiting API
**Problème :** Les endpoints sont exposés sans limite de requêtes.
```python
# Utiliser slowapi (middleware FastAPI)
from slowapi import Limiter
limiter = Limiter(key_func=get_remote_address)

@router.get("/articles/recent")
@limiter.limit("60/minute")
async def get_recent_articles(...):
```

---

### 6.2 Isolation multi-client renforcée
**Problème :** Vérifier que toutes les requêtes filtrent bien par `client_id` ou `revue_id`.
**Action :** Audit de sécurité de tous les endpoints `/articles`, `/revues`, `/keywords`.
- Chaque endpoint doit vérifier que l'utilisateur appartient au client propriétaire de la ressource.
- Ajouter tests d'intégration cross-client.

---

### 6.3 Retry avec backoff exponentiel
**Problème :** Si Trafilatura échoue (timeout réseau), l'article passe à `failed` immédiatement.
**Solution :** 3 tentatives avec délai croissant avant de passer à `failed`.
```python
# Dans rss_service.py — enrich_article()
for attempt in range(3):
    try:
        result = trafilatura.extract(html)
        break
    except Exception:
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s
        else:
            article.status = "failed"
```

---

## SPRINT 7 — Analytics & Reporting (3-4 jours)

### 7.1 Analytics par source média
**Idée :** Pour chaque source, afficher :
- Volume d'articles par jour (graphique 30j)
- Taux de match (% articles matchés vs total)
- Répartition thématique
- Sources les plus citées par les articles matchés (entités communes)

---

### 7.2 Heatmap temporelle
**Idée :** Afficher à quelles heures de la journée les sources publient le plus.
→ Permet d'optimiser les horaires des slots SerpAPI.
```
     00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23
Hes  ░░ ░░ ░░ ░░ ░░ ░░ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ██ ░░
MAP  ░░ ░░ ░░ ░░ ░░ ░░ ░░ ██ ██ ██ ██ ░░ ██ ██ ██ ██ ░░ ░░ ░░ ░░ ░░ ░░ ░░ ░░
```

---

### 7.3 Rapport de couverture client
**Idée :** Pour chaque revue, rapport mensuel automatique :
- Nombre d'articles collectés / matchés / validés
- Sources qui parlent le plus du client
- Évolution tonalité sur le mois
- Top keywords déclencheurs

---

## Priorités recommandées

```
SEMAINE 1   Sprint 1 — Correctifs (source_id, weak_signal, matched_keywords)
SEMAINE 2   Sprint 2 — Performance (crawl x3, full-text search, lazy loading)
SEMAINE 3   Sprint 3 — Monitoring (workers, alertes sources silencieuses)
SEMAINE 4   Sprint 4 — Intelligence (brief quotidien, clustering tendances)
SEMAINE 5   Sprint 5 — Client (notifications email, export PDF)
SEMAINE 6   Sprint 6 — Robustesse (rate limiting, isolation, retry)
SEMAINE 7+  Sprint 7 — Analytics (heatmap, rapports mensuels)
```

---

## Matrice impact / effort

| Amélioration | Impact | Effort | Priorité |
|---|---|---|---|
| Fix crawl_now source_id | Moyen | Faible | 🔴 Immédiat |
| Weak signal auto Claude | Élevé | Faible | 🔴 Immédiat |
| matched_keywords RssArticle | Moyen | Faible | 🔴 Immédiat |
| Crawl parallèle x3 | Élevé | Moyen | 🟠 Court terme |
| Full-text search PG | Élevé | Moyen | 🟠 Court terme |
| Brief quotidien Claude | Élevé | Moyen | 🟠 Court terme |
| Dashboard workers | Moyen | Moyen | 🟠 Court terme |
| Alertes sources silencieuses | Élevé | Faible | 🟠 Court terme |
| Notifications email | Élevé | Élevé | 🟡 Moyen terme |
| Export PDF | Moyen | Élevé | 🟡 Moyen terme |
| Clustering tendances | Élevé | Élevé | 🟡 Moyen terme |
| Rate limiting | Moyen | Faible | 🟡 Moyen terme |
| Analytics heatmap | Moyen | Moyen | 🔵 Long terme |
| Rapports mensuels | Élevé | Élevé | 🔵 Long terme |
| Vue mobile | Moyen | Élevé | 🔵 Long terme |
