"""The one tool the agent can call: full-text search over the crawled site."""

import db

SEARCH_WEBSITE_SCHEMA = {
    "name": "search_website",
    "description": (
        "Search the crawled content of the target website for information relevant "
        "to the user's question. Returns up to 5 matching text chunks with their "
        "source URL. Always call this before answering questions about the company, "
        "its products, services, pricing, or policies - do not rely on general knowledge."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Key terms from the user's question."}
        },
        "required": ["query"],
    },
}


def search_website(query: str, top_n: int = 5) -> str:
    results = db.search_kb(query, top_n)
    if not results:
        return "No matching content found on the website for this query."
    return "\n\n".join(f"Source: {r['url']}\n{r['content']}" for r in results)
