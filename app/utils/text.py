import re
import unicodedata


def normalize_text(text: str) -> str:
    """
    Normalizes text for processing:
    - Trims whitespace
    - Removes duplicate spaces
    - Normalizes Unicode
    - Normalizes quotation marks and line endings
    Preserves numbers, currencies, tickers, percentages.
    """
    if not text:
        return ""

    # Unicode normalize
    text = unicodedata.normalize("NFKC", text)

    # Standardize quotes
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')

    # Remove duplicate spaces and clean line endings
    text = re.sub(r"\s+", " ", text)

    return text.strip()
