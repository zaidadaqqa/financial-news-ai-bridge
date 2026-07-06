from enum import StrEnum


class NewsStatus(StrEnum):
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    DEDUPLICATED = "DEDUPLICATED"
    STORED = "STORED"
    AI_PENDING = "AI_PENDING"
    AI_SUCCESS = "AI_SUCCESS"
    AI_FAILED = "AI_FAILED"
    TELEGRAM_PENDING = "TELEGRAM_PENDING"
    PUBLISHED = "PUBLISHED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"


class NewsCategory(StrEnum):
    ECONOMIC_DATA = "economic_data"
    CENTRAL_BANK = "central_bank"
    COMPANY = "company"
    EARNINGS = "earnings"
    COMMODITIES = "commodities"
    FOREX = "forex"
    BONDS = "bonds"
    CRYPTO = "crypto"
    GEOPOLITICAL = "geopolitical"
    GOVERNMENT = "government"
    BREAKING = "breaking"
    GENERAL = "general"


class MarketBias(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"
