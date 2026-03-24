"""
Déduplication near-duplicate via SimHash 64-bit.

Usage :
    fp = compute_fingerprint(title, content)
    is_dup = is_near_duplicate(fp, existing_fp)
"""
import hashlib
import re
import unicodedata


# ── Normalisation ────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Normalise le texte pour le fingerprinting :
    - Supprime tashkeel (diacritiques arabes)
    - Normalise alef / ya / ta marbuta
    - Supprime accents latins
    - Minuscules, sans ponctuation
    """
    if not text:
        return ""
    # Tashkeel (diacritiques arabes)
    text = re.sub(r'[\u0610-\u061A\u064B-\u065F]', '', text)
    # Alef variants → ا
    text = re.sub(r'[إأآ]', 'ا', text)
    text = text.replace('ى', 'ي').replace('ة', 'ه')
    # Accents latins (NFD → enlever combining)
    text = ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if not unicodedata.combining(c)
    )
    text = text.lower()
    # Supprimer ponctuation, garder lettres/chiffres/espaces
    text = re.sub(r'[^\w\s]', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── SimHash 64-bit ───────────────────────────────────────────────────────────

def _simhash_64(text: str) -> int:
    """Calcule un SimHash 64-bit sur les mots du texte normalisé."""
    words = text.split()
    if not words:
        return 0
    v = [0] * 64
    for word in words:
        # MD5 du mot → 128 bits, on prend les 64 premiers
        h = int(hashlib.md5(word.encode('utf-8')).hexdigest()[:16], 16)
        for i in range(64):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1
    fp = 0
    for i in range(64):
        if v[i] > 0:
            fp |= (1 << i)
    return fp


# ── API publique ─────────────────────────────────────────────────────────────

def compute_fingerprint(title: str, content: str | None = None) -> str:
    """
    Calcule un fingerprint SimHash 64-bit pour la déduplication near-duplicate.

    Basé sur le titre normalisé + les 100 premiers mots du contenu (si disponible).
    Retourne une chaîne hexadécimale de 16 caractères (ex: "a3f1b2c4d5e6f708").

    Args:
        title:   Titre de l'article
        content: Texte intégral (optionnel, améliore la précision)

    Returns:
        Fingerprint hex 16 chars
    """
    text = _normalize(title or "")
    if content:
        content_normalized = _normalize(content)
        # Ajouter les 100 premiers mots du contenu
        extra_words = " ".join(content_normalized.split()[:100])
        text = text + " " + extra_words
    return format(_simhash_64(text), '016x')


def hamming_distance(fp1: str, fp2: str) -> int:
    """Distance de Hamming entre deux fingerprints hexadécimaux 16-char."""
    try:
        return bin(int(fp1, 16) ^ int(fp2, 16)).count('1')
    except (ValueError, TypeError):
        return 64  # invalide → considérer comme différents


def is_near_duplicate(fp1: str, fp2: str, threshold: int = 5) -> bool:
    """
    Retourne True si deux articles sont near-duplicates.
    Seuil de 5 bits sur 64 ≈ 92% de mots en commun → même article reformulé.

    Args:
        fp1, fp2:   Fingerprints hex 16-char
        threshold:  Bits différents tolérés (défaut 5 = très strict)

    Returns:
        True si near-duplicate
    """
    return hamming_distance(fp1, fp2) <= threshold
