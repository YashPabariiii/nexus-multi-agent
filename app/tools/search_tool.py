from langchain_core.tools import tool
from tavily import TavilyClient

from app.config.settings import get_settings
from app.core.tracing import traced_tool

settings = get_settings()

DOMAIN_FILTERS = {
    "finance": ["sec.gov", "bloomberg.com", "reuters.com", "wsj.com"],
    "tech": ["techcrunch.com", "arxiv.org", "github.com", "wired.com"],
    "regulatory": ["federalregister.gov", "eur-lex.europa.eu"],
    "general": [],
}


def _client() -> TavilyClient:
    return TavilyClient(api_key=settings.TAVILY_API_KEY)


@tool
@traced_tool
def web_search(query: str, max_results: int = 5, domain: str = "general", depth: str = "standard") -> list[dict]:
    """Search the web for factual information relevant to a research query."""
    search_depth = "advanced" if depth == "deep" else "basic"
    include_domains = DOMAIN_FILTERS.get(domain, [])

    response = _client().search(
        query=query,
        max_results=max_results,
        search_depth=search_depth,
        include_domains=include_domains or None,
    )

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
            "score": r.get("score", 0.0),
        }
        for r in response.get("results", [])
    ]
