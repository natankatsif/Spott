"""Answering a question from the corpus (docs/API.md): retrieve → LLM over numbered lines → checked by code.

The model never writes quotes. For every sentence it returns ids of source lines ("S2.L4"); the quote is
then taken from the index. A sentence without a valid line id is dropped, and a sentence whose numbers
don't appear in its quotes is marked unverified, so the answer can't carry a claim no source line backs.

Speed (docs/history/tasks/10): the question is searched while a small model rewrites it into Romanian and Russian
queries and keywords; the freshness queries (later acts, newer acts on the same sites) run before the answer,
not after it; the prompt carries only the matched lines with their context; the answer is streamed, each
sentence checked as soon as the model closes it.

The modules, in the order a question goes through them: routing (does it need the documents), search (what to answer
from), sources (the prompt's numbered lines), prompts, response (the model's JSON checked, claims, and turned into an
AskResponse), streaming (the SSE events), translation, contacts; pipeline runs them. texts holds what is said by
code, chunks and corpus what the others know about a chunk and read from the index.
"""

from .corpus import Store
from .pipeline import answer_events, answer_question, replay_events

__all__ = ["Store", "answer_events", "answer_question", "replay_events"]
