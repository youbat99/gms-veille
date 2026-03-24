#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
# migrate.sh — Helper Alembic pour les migrations en production
#
# Usage :
#   ./migrate.sh                → upgrade head (défaut)
#   ./migrate.sh upgrade        → upgrade head
#   ./migrate.sh current        → version actuelle
#   ./migrate.sh history        → historique des migrations
#   ./migrate.sh downgrade      → annule la dernière migration (-1)
#   ./migrate.sh new "message"  → crée une nouvelle migration
#
# Variables d'env requises (déjà présentes dans Coolify) :
#   DATABASE_URL=postgresql+asyncpg://...
# ══════════════════════════════════════════════════════════════════════

set -e  # Arrêt immédiat si une commande échoue

ACTION=${1:-"upgrade"}
MSG=${2:-"auto_migration"}

# Vérification que DATABASE_URL est définie
if [ -z "$DATABASE_URL" ]; then
    echo "❌ ERROR: DATABASE_URL n'est pas défini"
    echo "   Export it: export DATABASE_URL=postgresql+asyncpg://..."
    exit 1
fi

echo "🗄️  Base de données : ${DATABASE_URL%%@*}@***"
echo ""

case "$ACTION" in
    "upgrade"|"head")
        echo "⬆️  Application des migrations en attente..."
        alembic upgrade head
        echo "✅ Migrations appliquées avec succès"
        ;;
    "current")
        echo "📍 Version actuelle :"
        alembic current
        ;;
    "history")
        echo "📋 Historique des migrations :"
        alembic history --verbose
        ;;
    "downgrade")
        echo "⚠️  Annulation de la dernière migration..."
        read -p "Es-tu sûr ? (oui/non) : " confirm
        if [ "$confirm" = "oui" ]; then
            alembic downgrade -1
            echo "✅ Migration annulée"
        else
            echo "Annulé."
        fi
        ;;
    "new")
        echo "🆕 Création d'une nouvelle migration : $MSG"
        alembic revision --autogenerate -m "$MSG"
        echo "✅ Fichier de migration créé dans alembic/versions/"
        echo "⚠️  VÉRIFIE le fichier avant de l'appliquer !"
        ;;
    *)
        echo "Usage: ./migrate.sh [upgrade|current|history|downgrade|new \"message\"]"
        exit 1
        ;;
esac
