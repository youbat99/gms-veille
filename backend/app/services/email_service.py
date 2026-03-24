"""
Service d'envoi de la revue de presse par email via Resend.
Structure : synthèse exécutive (Claude) → articles par mot-clé → par tonalité.
"""
import uuid
import logging
from collections import defaultdict
from datetime import datetime, timezone, date, time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.article import Article, ArticleStatus
from app.models.newsletter import NewsletterConfig, EmailLog
from app.models.revue import Revue, Keyword
from app.models.client import Client

logger = logging.getLogger(__name__)

# ── Tonalité config (sans emojis) ──────────────────────────────────────
_TON = {
    "positive": {"color": "#16a34a", "label": "POSITIFS"},
    "neutral":  {"color": "#64748b", "label": "NEUTRES"},
    "negative": {"color": "#dc2626", "label": "NEGATIFS"},
}
_TON_ORDER = ["positive", "negative", "neutral"]


# ── Synthèse exécutive via Claude Haiku ──────────────────────────────────
async def _generate_executive_summary(
    articles: list[Article],
    revue_name: str,
    date_str: str,
    keyword_names: dict,
) -> Optional[str]:
    """
    Génère une synthèse exécutive en 3-5 bullet points via Claude Haiku (OpenRouter).
    Retourne le texte brut avec bullets, ou None si non disponible.
    """
    if not settings.OPENROUTER_API_KEY:
        return None
    if not articles:
        return None

    # Top 20 articles par pertinence pour limiter les tokens
    top = sorted(
        [a for a in articles if a.title],
        key=lambda a: (a.relevance_score or 0),
        reverse=True,
    )[:20]

    lines = []
    for a in top:
        kw = keyword_names.get(a.keyword_id, "") if a.keyword_id else ""
        ton = a.tonality.value if a.tonality else "neutre"
        summary = (a.summary or a.summary_en or "")[:120]
        lines.append(f"[{ton}][{kw}] {a.title}: {summary}")

    article_block = "\n".join(lines)

    prompt = (
        f"Tu es un analyste de veille médiatique senior.\n"
        f"Voici {len(articles)} articles de la revue \"{revue_name}\" du {date_str}.\n\n"
        f"Génère une synthèse exécutive en 3 à 5 points clés en français.\n"
        f"Format : bullet points courts (max 2 lignes chacun), percutants et factuels.\n"
        f"Ton : professionnel, neutre, sans emojis. Ne cite pas les sources.\n"
        f"Réponds uniquement avec les bullet points, un par ligne, chaque ligne commençant par '• '.\n\n"
        f"Articles :\n{article_block}"
    )

    try:
        import httpx
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "anthropic/claude-haiku-4-5",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return text
        logger.warning(f"[newsletter:synthèse] OpenRouter HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[newsletter:synthèse] erreur: {e}")

    return None


# ── Rendu HTML ────────────────────────────────────────────────────────────
def _build_html(
    revue_name: str,
    articles: list[Article],
    keyword_names: dict,        # {uuid: str}
    period_from: Optional[datetime],
    period_to: Optional[datetime],
    executive_summary: Optional[str] = None,
) -> str:
    from urllib.parse import urlparse

    date_str = (period_to or datetime.now(timezone.utc)).strftime("%d %B %Y")
    total = len(articles)
    counts = {
        t: sum(1 for a in articles if (a.tonality.value if a.tonality else "neutral") == t)
        for t in _TON
    }

    # ── Grouper : keyword → tonality → articles ──
    by_kw: dict = defaultdict(lambda: defaultdict(list))
    for art in articles:
        kw_id = art.keyword_id
        ton = art.tonality.value if art.tonality else "neutral"
        by_kw[kw_id][ton].append(art)

    # Trier les groupes par nombre d'articles desc
    sorted_kw = sorted(by_kw.items(), key=lambda x: sum(len(v) for v in x[1].values()), reverse=True)

    # ── Section synthèse ──
    summary_section = ""
    if executive_summary:
        bullets_html = ""
        for line in executive_summary.split("\n"):
            line = line.strip()
            if not line:
                continue
            text = line.lstrip("•-– ").strip()
            if text:
                bullets_html += (
                    f'<tr><td style="padding:5px 0;vertical-align:top;width:16px;">'
                    f'<span style="color:#1e40af;font-weight:700;font-size:14px;">•</span></td>'
                    f'<td style="padding:5px 0 5px 8px;color:#1e293b;font-size:13px;'
                    f'line-height:1.6;text-align:justify;">{text}</td></tr>'
                )
        if bullets_html:
            summary_section = f"""
        <tr>
          <td style="background-color:#ffffff;padding:28px 40px 0;">
            <p style="margin:0 0 14px;font-size:10px;font-weight:700;color:#1e40af;
               text-transform:uppercase;letter-spacing:2px;border-bottom:2px solid #1e40af;
               padding-bottom:8px;">Synthese Executive</p>
            <table cellpadding="0" cellspacing="0" border="0" width="100%">
              {bullets_html}
            </table>
          </td>
        </tr>
        <tr><td style="background-color:#fff;padding:16px 40px 0;">
          <hr style="border:none;border-top:1px solid #e2e8f0;margin:0;" />
        </td></tr>"""

    # ── Sections articles par mot-clé ──
    articles_html = ""
    for kw_id, ton_groups in sorted_kw:
        kw_name = keyword_names.get(kw_id, "Autres") if kw_id else "Autres"
        kw_total = sum(len(v) for v in ton_groups.values())

        articles_html += f"""
        <tr>
          <td style="background-color:#f8fafc;padding:22px 40px 10px;border-top:3px solid #0f172a;">
            <p style="margin:0;font-size:13px;font-weight:700;color:#0f172a;
               text-transform:uppercase;letter-spacing:1px;">{kw_name}</p>
            <p style="margin:3px 0 0;color:#94a3b8;font-size:11px;">{kw_total} article{'s' if kw_total > 1 else ''}</p>
          </td>
        </tr>"""

        for ton in _TON_ORDER:
            arts = ton_groups.get(ton, [])
            if not arts:
                continue
            cfg = _TON[ton]
            articles_html += f"""
        <tr>
          <td style="background-color:#fff;padding:16px 40px 4px;">
            <p style="margin:0;font-size:10px;font-weight:700;color:{cfg['color']};
               text-transform:uppercase;letter-spacing:1.5px;">{cfg['label']} ({len(arts)})</p>
          </td>
        </tr>"""
            for art in arts:
                domain = ""
                try:
                    domain = urlparse(art.url or "").netloc.replace("www.", "")
                except Exception:
                    pass
                pub_date = art.published_at.strftime("%d/%m/%Y") if art.published_at else ""
                meta = " · ".join(filter(None, [domain, art.author, pub_date]))
                summary = (art.summary or art.summary_en or "")[:300]
                if summary and len(summary) == 300:
                    summary += "…"

                tags_html = ""
                if art.tags and isinstance(art.tags, list):
                    tag_list = art.tags[:4]
                    if tag_list:
                        tags_html = (
                            '<p style="margin:7px 0 0;">'
                            + " ".join(
                                f'<span style="display:inline-block;background:#f1f5f9;color:#475569;'
                                f'font-size:10px;padding:2px 7px;border-radius:3px;margin-right:3px;">'
                                f'{t}</span>'
                                for t in tag_list
                            )
                            + "</p>"
                        )

                articles_html += f"""
        <tr>
          <td style="background-color:#fff;padding:0 40px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="border-bottom:1px solid #f1f5f9;padding:14px 0;">
              <tr><td>
                <a href="{art.url or '#'}"
                   style="color:#0f172a;font-size:14px;font-weight:600;text-decoration:none;
                          display:block;line-height:1.4;">{art.title or '(sans titre)'}</a>
                {f'<p style="margin:7px 0 0;color:#475569;font-size:13px;line-height:1.65;text-align:justify;">{summary}</p>' if summary else ''}
                {f'<p style="margin:6px 0 0;color:#94a3b8;font-size:11px;">{meta}</p>' if meta else ''}
                {tags_html}
              </td></tr>
            </table>
          </td>
        </tr>"""

    # ── Barre de stats ──
    period_html = ""
    if period_from and period_to:
        period_html = (
            f'<p style="margin:10px 0 0;color:#475569;font-size:11px;text-align:center;">'
            f'Periode : {period_from.strftime("%d/%m")} → {period_to.strftime("%d/%m/%Y")}</p>'
        )

    stat_cells = ""
    for color, val, label in [
        ("#f8fafc", str(total), "articles"),
        ("#4ade80", str(counts.get("positive", 0)), "positifs"),
        ("#94a3b8", str(counts.get("neutral", 0)), "neutres"),
        ("#f87171", str(counts.get("negative", 0)), "negatifs"),
    ]:
        stat_cells += (
            f'<td align="center" style="padding:0 16px;">'
            f'<p style="margin:0;color:{color};font-size:22px;font-weight:700;">{val}</p>'
            f'<p style="margin:4px 0 0;color:#64748b;font-size:10px;text-transform:uppercase;'
            f'letter-spacing:1px;">{label}</p></td>'
        )

    app_url = settings.APP_URL

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Revue de presse — {revue_name}</title>
</head>
<body style="margin:0;padding:0;background-color:#e2e8f0;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background-color:#e2e8f0;padding:32px 16px;">
    <tr><td align="center">
      <table width="620" cellpadding="0" cellspacing="0" border="0"
             style="max-width:620px;width:100%;border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.12);">

        <!-- HEADER -->
        <tr>
          <td style="background-color:#0f172a;padding:30px 40px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
              <td>
                <p style="margin:0;color:#f8fafc;font-size:18px;font-weight:700;
                   letter-spacing:-0.5px;">GMS Veille</p>
                <p style="margin:3px 0 0;color:#475569;font-size:11px;
                   text-transform:uppercase;letter-spacing:1.5px;">Media Intelligence</p>
              </td>
              <td align="right">
                <p style="margin:0;color:#e2e8f0;font-size:14px;font-weight:600;">{revue_name}</p>
                <p style="margin:4px 0 0;color:#64748b;font-size:12px;">{date_str}</p>
              </td>
            </tr></table>
          </td>
        </tr>

        <!-- STATS BAR -->
        <tr>
          <td style="background-color:#1e293b;padding:20px 40px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>{stat_cells}</tr>
            </table>
            {period_html}
          </td>
        </tr>

        {summary_section}

        <!-- ARTICLES PAR MOT-CLE -->
        {articles_html if articles_html else
          '<tr><td style="background:#fff;padding:40px;text-align:center;color:#94a3b8;font-size:14px;">Aucun article pour cette periode.</td></tr>'}

        <!-- CTA -->
        <tr>
          <td style="background-color:#ffffff;padding:28px 40px;text-align:center;
                     border-top:1px solid #f1f5f9;">
            <a href="{app_url}"
               style="display:inline-block;background-color:#1e40af;color:#ffffff;
                      font-size:13px;font-weight:600;text-decoration:none;
                      padding:12px 28px;border-radius:8px;letter-spacing:0.3px;">
              Acceder a la plateforme
            </a>
            <p style="margin:10px 0 0;color:#94a3b8;font-size:11px;">
              Consultez l'integralite de votre revue de presse en ligne
            </p>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background-color:#f8fafc;padding:18px 40px;text-align:center;
                     border-top:1px solid #e2e8f0;">
            <p style="margin:0;color:#94a3b8;font-size:11px;">GMS Veille · {date_str}</p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ── Récupérer articles + noms de keywords ────────────────────────────────
async def _get_newsletter_data(
    db: AsyncSession,
    revue_id: uuid.UUID,
    since: Optional[datetime],
) -> tuple[list[Article], dict, datetime, datetime]:
    """Retourne (articles, keyword_names, period_from, period_to)."""
    now = datetime.now(timezone.utc)
    if since is None:
        since = datetime.combine(date.today(), time.min).replace(tzinfo=timezone.utc)

    stmt = (
        select(Article)
        .where(
            Article.revue_id == revue_id,
            Article.status == ArticleStatus.approved,
            Article.validated_at >= since,
        )
        .order_by(Article.validated_at.desc())
    )
    result = await db.execute(stmt)
    articles = list(result.scalars().all())

    # Charger les noms des keywords
    kw_ids = list({a.keyword_id for a in articles if a.keyword_id})
    keyword_names: dict = {}
    if kw_ids:
        kw_result = await db.execute(select(Keyword).where(Keyword.id.in_(kw_ids)))
        keyword_names = {kw.id: kw.term for kw in kw_result.scalars().all()}

    return articles, keyword_names, since, now


# ── Récupérer les destinataires ──────────────────────────────────────────
async def _get_recipients(
    db: AsyncSession,
    config: NewsletterConfig,
    revue: Revue,
) -> list[str]:
    recipients: list[str] = []
    if config.include_client_email and revue.client_id:
        client = await db.get(Client, revue.client_id)
        if client and client.email:
            recipients.append(client.email)
    for email in (config.extra_recipients or []):
        if email and email not in recipients:
            recipients.append(email)
    return recipients


# ── Envoi principal ──────────────────────────────────────────────────────
async def send_newsletter(
    db: AsyncSession,
    revue_id: uuid.UUID,
    triggered_by: str = "manual",
    test_email: Optional[str] = None,
) -> dict:
    if not settings.RESEND_API_KEY:
        return {"status": "error", "error": "RESEND_API_KEY non configurée", "recipients": [], "article_count": 0, "subject": ""}

    config_result = await db.execute(
        select(NewsletterConfig).where(NewsletterConfig.revue_id == revue_id)
    )
    config = config_result.scalar_one_or_none()
    revue = await db.get(Revue, revue_id)
    if not revue:
        return {"status": "error", "error": "Revue introuvable", "recipients": [], "article_count": 0, "subject": ""}

    if config is None:
        config = NewsletterConfig(
            id=uuid.uuid4(),
            revue_id=revue_id,
            enabled=False,
            schedule_hour=8,
            schedule_minute=0,
            extra_recipients=[],
            include_client_email=True,
        )

    recipients = [test_email] if test_email else await _get_recipients(db, config, revue)
    if not recipients:
        return {"status": "error", "error": "Aucun destinataire configuré", "recipients": [], "article_count": 0, "subject": ""}

    since = config.last_sent_at if not test_email else None
    articles, keyword_names, period_from, period_to = await _get_newsletter_data(db, revue_id, since)

    date_str = period_to.strftime("%d/%m/%Y")
    subject = (config.subject_template or "Revue de presse · {revue} · {date}").format(
        revue=revue.name, date=date_str
    )

    # Synthèse exécutive (async, non bloquante si échec)
    executive_summary = await _generate_executive_summary(articles, revue.name, date_str, keyword_names)

    html_content = _build_html(revue.name, articles, keyword_names, period_from, period_to, executive_summary)

    status = "sent"
    error_msg = None
    try:
        import resend
        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.EMAIL_FROM,
            "to": recipients,
            "subject": subject,
            "html": html_content,
        })
        logger.info(f"[newsletter] envoyé → {recipients} ({len(articles)} articles)")
    except Exception as e:
        status = "error"
        error_msg = str(e)
        logger.error(f"[newsletter] erreur envoi: {e}")

    log = EmailLog(
        id=uuid.uuid4(),
        revue_id=revue_id,
        sent_at=period_to,
        recipients=recipients,
        article_count=len(articles),
        period_from=period_from,
        period_to=period_to,
        subject=subject,
        status=status,
        error_message=error_msg,
        triggered_by=triggered_by,
        html_snapshot=html_content,
        article_ids=[str(a.id) for a in articles],
        is_critical=False,
    )
    db.add(log)

    if status == "sent" and not test_email and config.id is not None:
        config_obj = await db.get(NewsletterConfig, config.id)
        if config_obj:
            config_obj.last_sent_at = period_to

    await db.commit()
    return {
        "status": status,
        "recipients": recipients,
        "article_count": len(articles),
        "subject": subject,
        "error": error_msg,
    }
