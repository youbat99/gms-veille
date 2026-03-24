# PRD — Pipeline de Collecte : Import en masse, Monitoring & Qualité

**Version :** 1.0  
**Date :** 2026-03-21  
**Auteur :** GMS Product  
**Statut :** Brouillon  

---

## 1. Executive Summary

Nous construisons un **centre de contrôle du pipeline de collecte** pour l'équipe GMS interne, afin de résoudre trois problèmes critiques : l'impossibilité d'importer des sources en masse (une par une uniquement), l'absence totale de visibilité sur les sources cassées ou silencieuses, et l'afflux d'articles parasites (sans titre, hors-sujet, doublons) qui polluent la file HITL. L'impact attendu : réduire le temps d'ajout de sources de plusieurs heures à quelques minutes, détecter toute source cassée en moins de 2h au lieu de jamais, et doubler le volume de sources actives tout en améliorant la crédibilité auprès des clients.

---

## 2. Problème

### Qui a ce problème ?
L'équipe GMS interne : admins et analystes qui configurent et surveillent le pipeline de collecte au quotidien.

### Problème 1 — Import source par source (Critique)
Ajouter une nouvelle source se fait uniquement une par une via le formulaire. Quand l'analyste reçoit une liste de 20 sources RSS pertinentes, il doit :
1. Copier l'URL, remplir le formulaire, attendre le processing
2. Répéter 20 fois

**Cas spécifique signalé :**
- Quand on importe un article directement (via URL), la source n'est pas automatiquement ajoutée à la liste des sources crawlées → l'article est enrichi mais la source reste orpheline, jamais re-crawlée
- Impossible de détecter si un article importé existe déjà en base → risque de doublon silencieux, aucun warning

### Problème 2 — Pipeline invisible (Critique)
Aucune visibilité sur l'état de santé du pipeline :
- Une source RSS peut être silencieuse depuis 3 jours → personne ne le sait
- Un crawl peut échouer (timeout, 403, changement de structure HTML) → aucune alerte
- Un filtre peut bloquer tous les articles d'une source → détection uniquement si le client se plaint

**Conséquence directe :** Des événements importants ne sont pas captés. Le client reçoit une revue de presse incomplète sans savoir pourquoi.

### Problème 3 — Articles parasites en HITL (Modéré)
Des articles arrivent en file HITL alors qu'ils ne devraient pas :
- **Sans titre** : Trafilatura/newspaper4k ont échoué, RSS title pas sauvegardé
- **Hors-sujet** : matching keywords trop permissif
- **Doublons** : SimHash ne détecte pas tous les cas (articles reformulés)

L'analyste perd du temps à rejeter manuellement ces articles au lieu de valider du contenu utile.

### Impact business
1. **Couverture manquante** : événements non captés → client non informé → perte de crédibilité
2. **Crédibilité client** : articles parasites dans la revue → qualité perçue dégradée
3. **Temps perdu** : tâches manuelles répétitives qui devraient être automatisées

---

## 3. Personas

### Persona principal — L'Analyste GMS (Admin)
- **Rôle :** Configure les sources, surveille le pipeline, valide les articles en HITL
- **Routine :** Ouvre le dashboard le matin, vérifie les articles en attente, ajoute les sources signalées par les clients
- **Frustrations actuelles :**
  - "J'ai une liste de 30 sources à ajouter, ça me prend une matinée entière"
  - "Je sais pas pourquoi Hespress n'a rien remonté depuis 2 jours"
  - "Il y a encore des articles sans titre dans la file, je dois tous les rejeter à la main"
- **Besoin clé :** Visibilité complète sur ce qui marche et ce qui ne marche pas, sans avoir à aller chercher l'information

### Persona secondaire — Le Superadmin GMS
- **Rôle :** Supervise l'ensemble du système, accès complet
- **Besoin clé :** Vue macro de la santé du pipeline, alertes critiques uniquement

---

## 4. Contexte stratégique

### Pourquoi maintenant ?
- GMS vise à passer de ~209 sources actives à 400+ pour couvrir de nouveaux secteurs (eau, énergie, transport, finances publiques)
- L'onboarding d'un nouveau client nécessite souvent 15-30 nouvelles sources → le process actuel crée un goulot d'étranglement de plusieurs jours
- Des clients ont commencé à signaler des lacunes de couverture → risque de churn

### Objectif stratégique aligné
> Doubler la couverture de sources actives en Q2 2026 sans augmenter la charge manuelle de l'équipe GMS.

### Ce que les concurrents font
- Meltwater, Mention : import en masse via fichier CSV/OPML, dashboard de santé des sources en temps réel
- lekiosk.ma (référence locale) : collecte batch 2-3x/jour, monitoring simple mais efficace

---

## 5. Solution

### Vue d'ensemble
Trois modules distincts, livrables en sprints indépendants :

---

### Module A — Import en masse de sources

**Description :** Un outil permettant de coller une liste d'URLs (RSS, sitemaps, pages web) et de les importer en une seule opération.

**Flux utilisateur :**
1. L'analyste colle jusqu'à 100 URLs dans une zone de texte (une URL par ligne)
2. Le système détecte automatiquement le type de chaque URL (RSS feed, sitemap XML, page web)
3. Validation en temps réel : URL accessible ? Feed valide ? Source déjà existante ? Article déjà en base ?
4. Tableau de prévisualisation : URL | Type détecté | Statut (Nouveau / Déjà présent / Erreur) | Nom auto-détecté
5. L'analyste sélectionne les sources à importer, déselectionne les doublons
6. Import groupé → les sources sont créées ET ajoutées au pool de crawl actif
7. Résumé : X sources ajoutées, Y erreurs, Z doublons ignorés

**Cas spécifiques à gérer :**
- URL d'article individuel → extraire la source parente + proposer d'ajouter la source (pas juste l'article)
- Article déjà en base → warning visible avant import, non bloquant
- Source déjà crawlée → signaler "déjà présente", ne pas dupliquer
- Support format OPML (import export depuis Feedly, NewsBlur, etc.)

---

### Module B — Dashboard Santé du Pipeline

**Description :** Un tableau de bord en temps réel affichant l'état de chaque source et les anomalies détectées.

**Vue principale — Liste des sources avec indicateurs de santé :**

| Source | Dernier crawl | Articles (24h) | Statut | Action |
|--------|--------------|----------------|--------|--------|
| Hespress | il y a 8 min | 47 | ✅ Actif | — |
| MapExpress | il y a 3 jours | 0 | 🔴 Silencieux | Investiguer |
| LesEco | il y a 2h | 0 | 🟡 Suspect | Vérifier |

**Niveaux d'alerte :**
- 🔴 **Critique** : Aucun article depuis > 24h (source normalement active) → notification push + email
- 🟡 **Attention** : Taux d'erreur d'extraction > 50% sur les derniers articles
- 🟠 **Info** : Articles collectés mais tous sans titre (extraction échoue systématiquement)
- ✅ **OK** : Tout fonctionne normalement

**Vue détail d'une source (au clic) — déjà existante, à enrichir :**
- Graphique d'activité sur 7 jours (nb articles/jour)
- Log des dernières erreurs de crawl (timeout, 403, parse error...)
- % d'articles avec titre / sans titre
- % matched / no_match / failed
- Bouton "Tester le crawl maintenant" (déjà implémenté, à intégrer dans cette vue)

**Alertes front-end :**
- Banner non-bloquant en haut du Kiosque si X sources sont en erreur critique
- Badge rouge sur l'icône "Kiosque" dans la sidebar si alertes actives

---

### Module C — Filtrage Qualité Pré-HITL

**Description :** Des règles de filtrage configurables qui empêchent les articles parasites d'atteindre la file HITL.

**Filtres automatiques (toujours actifs) :**
- ❌ **Sans titre ET sans contenu** : article rejeté avant HITL (statut `failed`)
- ❌ **Doublon exact** : url_hash identique → ignoré (déjà géré)
- ❌ **Doublon near-duplicate** : SimHash distance ≤ 4 → ignoré (déjà géré)

**Filtres configurables par revue (à activer/désactiver) :**
- 🔧 **Longueur minimale** : contenu < N caractères → `no_match` (défaut : 200 chars)
- 🔧 **Score de confiance matching** : seuil de pertinence keyword (1 match vs 3 matches)
- 🔧 **Langue forcée** : rejeter articles dans langue non attendue pour cette revue
- 🔧 **Domaine blacklist** : exclure explicitement certaines URLs/domaines d'une revue

**Interface de configuration :**
- Page "Paramètres qualité" par revue (accessible depuis Keywords Management)
- Toggle par filtre + valeur du seuil
- Preview : "Ces filtres auraient éliminé X articles parasites la semaine dernière"

---

## 6. Métriques de succès

### Métrique primaire
**Temps d'ajout d'un batch de 20 sources**
- Actuel : ~3-4 heures (une par une)
- Cible : < 15 minutes (import en masse)

### Métriques secondaires

| Métrique | Actuel | Cible | Délai mesure |
|----------|--------|-------|--------------|
| Délai de détection source cassée | Jamais / détection client | < 2h | 30 jours post-launch |
| Volume sources actives | ~209 | 350+ | 60 jours post-launch |
| % articles parasites en HITL | ~15-20% estimé | < 5% | 30 jours post-launch |
| Sources orphelines (article importé, source non crawlée) | Inconnu | 0 | 30 jours post-launch |

### Métriques de garde-fou (ne pas dégrader)
- Taux de `matched` articles en HITL : ne pas baisser (les filtres ne doivent pas rejeter de bons articles)
- Stabilité du pipeline existant : 0 régression sur le crawl actuel

---

## 7. User Stories & Critères d'acceptation

### Epic
> Nous croyons que donner à l'équipe GMS un outil d'import en masse, un dashboard de monitoring et des filtres qualité configurables permettra de doubler la couverture de sources tout en réduisant de 80% le temps de configuration, parce que les trois frictions actuelles (import manuel, invisibilité des erreurs, articles parasites) bloquent l'efficacité opérationnelle. Nous mesurerons le succès par le temps d'ajout d'un batch de 20 sources et le délai de détection des sources cassées.

---

### Module A — Import en masse

**Story A1 : Import multi-URLs**
En tant qu'analyste GMS, je veux coller une liste d'URLs et les importer en une seule opération, afin de configurer rapidement la couverture d'un nouveau secteur.

**Critères d'acceptation :**
- [ ] Zone de texte acceptant jusqu'à 100 URLs (une par ligne)
- [ ] Détection automatique du type : RSS feed / Sitemap XML / Page web
- [ ] Validation de chaque URL (accessible, format valide) en temps réel
- [ ] Tableau de prévisualisation avant import : URL, type, statut (Nouveau / Déjà présent / Erreur)
- [ ] Import groupé en un clic sur les URLs sélectionnées
- [ ] Les sources importées sont immédiatement actives dans le pool de crawl
- [ ] Résumé post-import : X créées, Y existantes ignorées, Z en erreur

**Story A2 : Détection doublon article**
En tant qu'analyste GMS, je veux être averti si un article que j'importe existe déjà en base, afin d'éviter les doublons silencieux.

**Critères d'acceptation :**
- [ ] À la saisie d'une URL d'article, vérification en base (url_hash)
- [ ] Si trouvé : warning non-bloquant "Cet article existe déjà — importé le [date], statut [status]"
- [ ] L'utilisateur peut choisir d'ignorer ou de forcer l'import
- [ ] Warning visible AVANT la soumission du formulaire

**Story A3 : Import article → création source**
En tant qu'analyste GMS, je veux que l'import d'un article crée automatiquement sa source parente dans le pool de crawl, afin qu'elle soit re-crawlée régulièrement.

**Critères d'acceptation :**
- [ ] Lors de l'import d'une URL d'article, le système extrait la base URL de la source
- [ ] Si la source n'existe pas : proposition de l'ajouter ("Ajouter lematin.ma aux sources crawlées ?")
- [ ] Si l'utilisateur accepte : source créée avec `is_active=true`, `crawl_method=rss` par défaut
- [ ] La source apparaît immédiatement dans la liste des sources du Kiosque

---

### Module B — Monitoring

**Story B1 : Vue santé sources**
En tant qu'analyste GMS, je veux voir en un coup d'œil quelles sources fonctionnent et lesquelles ont un problème, afin d'investiguer rapidement sans attendre que le client se plaigne.

**Critères d'acceptation :**
- [ ] Colonne "Statut santé" dans la liste des sources (✅ / 🟡 / 🔴)
- [ ] 🔴 si aucun article depuis > 24h pour une source normalement active
- [ ] 🟡 si taux d'extraction échouée > 50% sur les 20 derniers articles
- [ ] ✅ sinon
- [ ] Filtre rapide "Sources en erreur" dans le Kiosque
- [ ] Statut calculé et mis à jour toutes les heures (worker background)

**Story B2 : Alerte front-end**
En tant qu'analyste GMS, je veux être notifié dans l'interface quand des sources critiques sont en erreur, afin de ne pas avoir à vérifier manuellement.

**Critères d'acceptation :**
- [ ] Banner non-bloquant en haut du Kiosque : "⚠️ X sources en erreur critique — Voir le détail"
- [ ] Badge rouge sur l'icône Kiosque dans la sidebar si alertes actives
- [ ] Le banner disparaît si toutes les erreurs sont résolues (ou acquittées manuellement)
- [ ] Les alertes ne spamment pas : une seule notification par source par période de 24h

**Story B3 : Log d'erreurs par source**
En tant qu'analyste GMS, je veux voir les erreurs détaillées d'une source dans son drawer, afin de diagnostiquer rapidement la cause du problème.

**Critères d'acceptation :**
- [ ] Section "Logs" dans le drawer de chaque source
- [ ] Liste des 10 dernières erreurs de crawl : date, type d'erreur (timeout / 403 / parse error / feed vide), URL
- [ ] Indicateur "X% d'articles sans titre sur les 20 derniers"
- [ ] Indicateur "X% matched / no_match / failed"

---

### Module C — Qualité Pré-HITL

**Story C1 : Filtre contenu minimal**
En tant qu'analyste GMS, je veux que les articles sans contenu exploitable soient automatiquement exclus de la file HITL, afin de ne pas perdre de temps à les rejeter manuellement.

**Critères d'acceptation :**
- [ ] Les articles avec contenu < 200 caractères après extraction sont marqués `failed` (non `matched`)
- [ ] Le seuil est configurable par revue (100-500 chars)
- [ ] Les articles filtrés sont loggués avec la raison : "Contenu trop court (X chars)"
- [ ] Le filtre est désactivable par revue

**Story C2 : Blacklist domaine par revue**
En tant qu'analyste GMS, je veux pouvoir exclure certains domaines d'une revue spécifique, afin d'éliminer des sources qui génèrent du bruit pour ce client précis.

**Critères d'acceptation :**
- [ ] Interface de blacklist dans les paramètres de la revue
- [ ] Saisie de domaines à exclure (ex: "maghress.com")
- [ ] Les articles de ces domaines ne sont jamais matchés pour cette revue
- [ ] La blacklist est par revue, pas globale

---

## 8. Hors scope (v1)

- **Automatisation complète** : un humain valide toujours l'activation d'une nouvelle source
- **Interface client** : ce dashboard est 100% interne GMS, les clients ne voient rien de ce module
- **Alertes email/SMS** : les alertes sont uniquement dans l'interface (v2)
- **Import depuis Feedly/NewsBlur API** : l'import OPML suffit pour l'instant
- **Remplacement de SerpAPI** : on améliore la gestion, pas les sources de données
- **ML pour filtrage** : les filtres sont basés sur des règles simples, pas du machine learning

---

## 9. Dépendances & Risques

### Dépendances techniques
- **Backend** : Nouveau endpoint `POST /api/media-sources/bulk` pour l'import en masse
- **Backend** : Nouveau worker de monitoring de santé (calcul toutes les heures)
- **Backend** : Nouvelles colonnes `health_status`, `last_error`, `error_count` sur `media_sources`
- **Migration Alembic** : Ajouter les colonnes de monitoring + table `source_health_logs`
- **Frontend** : Composant "Import en masse" dans le Kiosque
- **Frontend** : Colonne statut + banner alertes dans la liste des sources

### Risques & Mitigations

| Risque | Probabilité | Impact | Mitigation |
|--------|-------------|--------|------------|
| Import en masse plante le pipeline (trop de crawls simultanés) | Élevée | Critique | Throttle l'import : max 5 sources en parallèle, queue le reste |
| Faux positifs du filtre qualité (bons articles rejetés) | Moyenne | Élevé | Seuils conservateurs par défaut + dashboard de monitoring des rejets |
| Régression sur le crawl existant lors de refactoring | Moyenne | Critique | Tests de non-régression avant déploiement, rollback plan |
| Worker monitoring surchargé sur 400+ sources | Faible | Moyen | Calcul incrémental : seules les sources actives récemment modifiées |
| Détection URL type incorrecte (RSS vs page web) | Moyenne | Faible | Fallback manuel : l'analyste peut forcer le type dans le tableau de prévisualisation |

---

## 10. Questions ouvertes

| Question | Décision proposée | Statut |
|----------|------------------|--------|
| Limite d'URLs par import en masse ? | 100 URLs max par batch | À valider |
| Seuil "source silencieuse" : 24h ou 48h ? | 24h pour sources quotidiennes, 72h pour sources hebdomadaires | À valider |
| Les alertes doivent-elles envoyer un email aussi (v1) ? | Non, v1 = interface uniquement | À valider |
| Import OPML (Feedly export) : v1 ou v2 ? | v2 — pas prioritaire | À valider |
| Configurer les filtres qualité par source ou par revue ? | Par revue (plus flexible pour les clients) | À valider |
| Faut-il un log d'audit des imports en masse ? | Oui — "Importé par [user] le [date] : X sources" | À valider |

---

## Annexe — Ordre de livraison suggéré

**Sprint 1 — Quick wins (1 semaine)**
- Story A2 : Warning doublon article (impact immédiat, faible effort)
- Story A3 : Import article → création source automatique (bug actuel signalé)
- Story B1 : Colonne statut santé dans liste sources (visible, haute valeur)

**Sprint 2 — Import en masse (2 semaines)**
- Story A1 : Interface import multi-URLs
- Backend : endpoint bulk import + throttling

**Sprint 3 — Monitoring complet (1 semaine)**
- Story B2 : Alertes front-end + badge sidebar
- Story B3 : Log erreurs dans drawer source
- Backend : worker monitoring + table health_logs

**Sprint 4 — Qualité pré-HITL (1 semaine)**
- Story C1 : Filtre contenu minimal
- Story C2 : Blacklist domaine par revue
- Interface configuration filtres

---

*Document vivant — mettre à jour au fil de la livraison.*
