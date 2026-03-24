"""
NLP Service — analyse complète d'un article via OpenRouter (API OpenAI-compatible).
Extrait : résumé FR/AR/EN, tonalité, tags, entités, score de pertinence,
auteur et date (si non trouvés par le scraper), impact business.

OpenRouter docs : https://openrouter.ai/docs
Modèle par défaut : anthropic/claude-haiku-4-5 (rapide, économique)
"""
import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime

from openai import AsyncOpenAI
from dateutil.parser import parse as parse_date

from app.core.config import settings
from app.models.article import Tonality

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Regex pour nettoyer les balises <parameter name="..."> qu'Anthropic/OpenRouter
# peut injecter dans les valeurs JSON des tool calls.
_PARAM_TAG_RE = re.compile(r'^<parameter\s+name="[^"]*">\s*|\s*</parameter>$', re.DOTALL)


def _clean_str(value: str | None) -> str | None:
    """
    Nettoie une valeur string retournée par le LLM :
    - Supprime les balises <parameter name="...">...</parameter>
    - Filtre les valeurs vides / 'null' / 'none'
    """
    if not value:
        return None
    # Supprime les éventuelles balises XML Anthropic dans les tool calls
    value = _PARAM_TAG_RE.sub("", value).strip()
    if value.lower() in ("null", "none", "n/a", ""):
        return None
    return value


def _clean_list(value: list | None) -> list:
    """Nettoie chaque item d'une liste en supprimant les balises parasites."""
    if not value:
        return []
    return [_PARAM_TAG_RE.sub("", str(v)).strip() for v in value if v]


# ─── Résultat de l'analyse ──────────────────────────────────────────────────

@dataclass
class NLPResult:
    # Résumés multilingues
    summary: str | None = None         # FR — résumé éditorial
    summary_en: str | None = None      # EN
    summary_ar: str | None = None      # AR

    # Analyse
    tonality: Tonality | None = None
    tonality_justification: str | None = None
    tags: list[str] = field(default_factory=list)
    relevance_score: float | None = None  # 0.0 → 1.0

    # Enrichissement (si scraper a raté)
    author: str | None = None
    published_at: datetime | None = None

    # Analyse approfondie (stockée en tags enrichis)
    entities_persons: list[str] = field(default_factory=list)
    entities_orgs: list[str] = field(default_factory=list)
    entities_places: list[str] = field(default_factory=list)
    key_themes: list[str] = field(default_factory=list)
    market_impact: str | None = None    # court texte sur l'impact business/marché

    # Phase 5
    theme: str | None = None           # politique|économie|société|sport|culture|international
    weak_signal: bool = False          # crise/signal faible détecté

    # Titre généré par Claude si titre original manquant
    generated_title: str | None = None

    error: str | None = None


# ─── Outil (OpenAI function calling format) ──────────────────────────────────

ANALYSIS_TOOL = {
    "type": "function",
    "function": {
        "name": "analyze_article",
        "description": "Analyse complète d'un article de presse marocain",
        "parameters": {
            "type": "object",
            "properties": {
                "summary_fr": {
                    "type": "string",
                    "description": "Résumé éditorial en français, 3-4 phrases percutantes, ton professionnel"
                },
                "summary_en": {
                    "type": "string",
                    "description": "Summary in English, 2-3 sentences"
                },
                "summary_ar": {
                    "type": "string",
                    "description": "ملخص باللغة العربية، 2-3 جمل واضحة"
                },
                "tonality": {
                    "type": "string",
                    "enum": ["positive", "neutral", "negative"],
                    "description": "Tonalité globale de l'article vis-à-vis du sujet principal"
                },
                "tonality_justification": {
                    "type": "string",
                    "description": "1 phrase expliquant pourquoi cette tonalité"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "6 à 10 tags thématiques précis en français (secteur, thème, acteurs clés)"
                },
                "relevance_score": {
                    "type": "number",
                    "description": "Score d'utilité 0.0-1.0 pour le client qui surveille ce sujet. 1.0 = article entièrement centré sur le sujet du client. 0.7 = sujet principal traité en profondeur parmi d'autres. 0.4 = sujet mentionné mais traitement superficiel. 0.2 = mention anecdotique. Ne pas mettre 0.0 si l'article est lié au domaine du client."
                },
                "author": {
                    "type": "string",
                    "description": "Auteur de l'article si mentionné dans le contenu, sinon null"
                },
                "published_date": {
                    "type": "string",
                    "description": "Date de publication ISO 8601 si mentionnée dans le contenu, sinon null"
                },
                "entities_persons": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Noms de personnes citées (ministres, PDG, experts...)"
                },
                "entities_organizations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Organisations citées (entreprises, ministères, partis politiques, associations, institutions publiques…). EXCLURE impérativement : médias, journaux, agences de presse, sites d'information, chaînes TV/radio (ex: Médias24, MAP, Le360, Hespress, 2M, Medi1, L'Économiste, etc.)"
                },
                "entities_places": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lieux géographiques mentionnés"
                },
                "key_themes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 thèmes principaux développés dans l'article"
                },
                "market_impact": {
                    "type": "string",
                    "description": "1-2 phrases sur l'impact potentiel pour les décideurs économiques marocains"
                },
                "theme": {
                    "type": "string",
                    "enum": ["politique", "économie", "société", "sport", "culture", "international"],
                    "description": "Thématique principale de l'article"
                },
                "generated_title": {
                    "type": "string",
                    "description": "Titre généré depuis le contenu si le titre original est absent. Concis, informatif, max 120 caractères. Ne remplir QUE si le titre fourni est vide ou 'Sans titre'."
                },
                "weak_signal": {
                    "type": "boolean",
                    "description": "true si l'article contient des signaux d'alerte potentiels : crise imminente, scandale, incident grave, tension sociale, risque sécuritaire, catastrophe naturelle, faillite, accident industriel, contamination, fraude détectée. false dans tous les autres cas."
                },
            },
            "required": [
                "summary_fr", "summary_en", "summary_ar",
                "tonality", "tonality_justification",
                "tags", "relevance_score",
                "entities_persons", "entities_organizations", "entities_places",
                "key_themes", "market_impact",
                "theme", "weak_signal"
            ]
        }
    }
}


# ─── Service ─────────────────────────────────────────────────────────────────

class NLPService:
    MODEL = "anthropic/claude-haiku-4-5"  # rapide et économique pour l'analyse en masse

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
            )
        return self._client

    async def analyze(
        self,
        title: str,
        content: str,
        url: str,
        keyword: str,
        scraper_author: str | None = None,
        scraper_date: datetime | None = None,
        meta_description: str | None = None,
        client_name: str | None = None,   # nom du client pour contextualiser la pertinence
    ) -> NLPResult:
        """
        Analyse complète d'un article scrapé.
        Retourne un NLPResult avec tous les champs remplis.
        """
        if not settings.OPENROUTER_API_KEY:
            logger.warning("OPENROUTER_API_KEY non configurée — NLP ignoré")
            return NLPResult(error="OPENROUTER_API_KEY manquante")

        # Tronquer le contenu si trop long (éviter dépassement de tokens)
        content_truncated = content[:8000] if content else ""

        prompt = self._build_prompt(
            title, content_truncated, url, keyword,
            scraper_author, scraper_date, meta_description,
            client_name=client_name,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.MODEL,
                max_tokens=2048,
                tools=[ANALYSIS_TOOL],
                tool_choice={"type": "function", "function": {"name": "analyze_article"}},
                messages=[{"role": "user", "content": prompt}],
            )

            # Extraire le résultat de l'outil
            message = response.choices[0].message
            if not message.tool_calls:
                return NLPResult(error="Pas de réponse structurée du LLM")

            raw_args = message.tool_calls[0].function.arguments
            data = json.loads(raw_args)
            return self._parse_result(data, scraper_author, scraper_date)

        except Exception as e:
            logger.error(f"NLP error pour {url}: {e}")
            return NLPResult(error=str(e)[:300])

    def _build_prompt(
        self,
        title: str,
        content: str,
        url: str,
        keyword: str,
        scraper_author: str | None,
        scraper_date: datetime | None,
        meta_description: str | None = None,
        client_name: str | None = None,
    ) -> str:
        titre_display = title if title and title.strip() else "⚠️ TITRE MANQUANT — génère un titre depuis le contenu dans le champ generated_title"
        context_lines = [
            f"**Client :** {client_name}" if client_name else "**Contexte :** Veille médiatique Maroc",
            f"**Sujet surveillé (mot-clé) :** {keyword}",
            f"**URL :** {url}",
            f"**Titre :** {titre_display}",
        ]
        if scraper_author:
            context_lines.append(f"**Auteur :** {scraper_author}")
        if scraper_date:
            context_lines.append(f"**Date de publication :** {scraper_date.isoformat()}")
        if meta_description:
            context_lines.append(f"**Description (meta) :** {meta_description}")

        client_ctx = f"le client **{client_name}** qui" if client_name else "un client qui"

        return f"""Tu es un analyste spécialisé en veille médiatique pour le Maroc.
Analyse cet article de presse collecté pour {client_ctx} surveille le sujet : **{keyword}**.

{chr(10).join(context_lines)}

**Contenu de l'article :**
{content}

Instructions :
- Le résumé FR doit être professionnel, informatif et prêt pour une revue de presse
- Les tags doivent couvrir : secteur économique, thème, acteurs, géographie
- Le score de pertinence mesure l'UTILITÉ de cet article pour ce client (pas juste la présence du mot-clé). Cet article a déjà passé un filtre de collecte donc il est au minimum lié au domaine.
- L'impact marché doit être utile pour un décideur économique marocain
- Pour l'auteur et la date : extrait du contenu si non déjà fourni
- Les entités nommées doivent être précises et complètes
"""

    def _parse_result(
        self,
        data: dict,
        scraper_author: str | None,
        scraper_date: datetime | None,
    ) -> NLPResult:
        # Tonalité
        tonality = None
        try:
            tonality = Tonality(data.get("tonality", "neutral"))
        except ValueError:
            tonality = Tonality.neutral

        # Date : priorité scraper > LLM
        published_at = scraper_date
        if not published_at and data.get("published_date"):
            try:
                published_at = parse_date(data["published_date"])
            except Exception:
                pass

        # Score borné entre 0.3 et 1.0
        # Plancher à 0.3 : si l'article est dans le système, il a déjà passé le filtre de collecte
        # → il est forcément lié au domaine client, donc jamais en-dessous de 30%
        score = data.get("relevance_score")
        if score is not None:
            score = max(0.3, min(1.0, float(score)))

        return NLPResult(
            summary=_clean_str(data.get("summary_fr")),
            summary_en=_clean_str(data.get("summary_en")),
            summary_ar=_clean_str(data.get("summary_ar")),
            tonality=tonality,
            tonality_justification=_clean_str(data.get("tonality_justification")),
            tags=_clean_list(data.get("tags")),
            relevance_score=score,
            author=scraper_author or (_clean_str(data.get("author"))),
            published_at=published_at,
            entities_persons=_clean_list(data.get("entities_persons")),
            entities_orgs=_clean_list(data.get("entities_organizations")),
            entities_places=_clean_list(data.get("entities_places")),
            key_themes=_clean_list(data.get("key_themes")),
            market_impact=_clean_str(data.get("market_impact")),
            theme=_clean_str(data.get("theme")),
            weak_signal=bool(data.get("weak_signal", False)),
            generated_title=_clean_str(data.get("generated_title")),
        )


nlp_service = NLPService()
