"""The admin panel's API (docs/API.md → Admin), a module per section of the panel: auth (login), sources (sources and
their crawl jobs; probe tells what a pasted link is, store keeps them, views turns them into API models), questions
(ratings, quick questions, gaps), model_settings (admin → Models), spending (admin → Spending)."""

from . import auth, model_settings, questions, sources, spending

ROUTERS = (auth.router, sources.router, questions.router, model_settings.router, spending.router)
