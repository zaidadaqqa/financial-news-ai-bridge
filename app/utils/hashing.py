import hashlib
import re


def generate_news_hash(normalized_headline: str, source_url: str | None = None) -> str:
    """
    Generates a deterministic SHA-256 hash for deduplication.
    Combines the normalized headline and source URL.
    """
    # Remove non-alphanumeric characters for semantic deduplication.
    clean_headline = re.sub(r"[^\w\s]", "", normalized_headline.lower())
    # Remove extra spaces left after removing punctuation
    clean_headline = re.sub(r"\s+", " ", clean_headline).strip()

    content = clean_headline
    if source_url:
        content += f"|{source_url}"

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
