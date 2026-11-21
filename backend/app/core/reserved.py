"""Aliases that must never be handed out because they collide with routes or UI paths."""

RESERVED_ALIASES: frozenset[str] = frozenset(
    {
        # API / infrastructure routes
        "api", "v1", "health", "healthz", "live", "ready", "readyz", "metrics", "docs", "redoc",
        "openapi", "swagger", "static", "assets", "favicon", "robots", "sitemap", "well-known",
        # Frontend routes
        "login", "logout", "register", "signup", "signin", "dashboard", "settings", "analytics",
        "urls", "url", "links", "link", "shorten", "account", "profile", "auth", "me",
        # Generic system / marketing words
        "admin", "root", "system", "config", "internal", "null", "undefined", "none", "true",
        "false", "www", "app", "status", "about", "help", "terms", "privacy", "contact",
        "pricing", "features", "support", "security", "blog",
    }
)


def is_reserved(alias: str) -> bool:
    return alias.lower() in RESERVED_ALIASES
