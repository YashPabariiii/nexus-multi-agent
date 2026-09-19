from app.tools.search_tool import DOMAIN_FILTERS


def _is_trusted_source(source_url: str | None, domain: str) -> bool:
    if not source_url:
        return False
    trusted = DOMAIN_FILTERS.get(domain, [])
    return any(td in source_url for td in trusted)


def claim_confidence(
    claim: dict,
    domain: str = "general",
    corroboration_count: int = 0,
    disputed: bool = False,
    retracted: bool = False,
) -> float:
    base = claim.get("confidence", 0.5)

    source_boost = 0.1 if _is_trusted_source(claim.get("source_url"), domain) else 0.0
    corroboration_boost = 0.05 * corroboration_count

    dispute_penalty = 0.0
    if retracted:
        dispute_penalty = 0.5
    elif disputed:
        dispute_penalty = 0.3

    adjusted = base + source_boost + corroboration_boost - dispute_penalty
    return min(1.0, max(0.0, adjusted))


def section_confidence(claims_in_section: list[dict]) -> float:
    if not claims_in_section:
        return 0.0
    weighted_sum = 0.0
    total_weight = 0.0
    for c in claims_in_section:
        weight = c.get("importance", 1.0)
        weighted_sum += c.get("confidence", c.get("final_confidence", 0.0)) * weight
        total_weight += weight
    return round(weighted_sum / total_weight, 3) if total_weight else 0.0


def report_confidence(sections: list[dict]) -> float:
    if not sections:
        return 0.0
    total_words = sum(len(s.get("content", "").split()) or 1 for s in sections)
    if total_words == 0:
        return 0.0
    weighted_sum = sum((len(s.get("content", "").split()) or 1) / total_words * s.get("confidence", 0.0) for s in sections)
    return round(weighted_sum, 3)


def confidence_label(score: float) -> str:
    if score >= 0.8:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    if score >= 0.4:
        return "LOW"
    return "UNVERIFIED"
