from __future__ import annotations

from .prompt_library import PromptLibrary
from .schemas import PromptTemplate
from .vectorizer import cosine_similarity, vectorize_text


class PromptRetriever:
    def __init__(self, library: PromptLibrary) -> None:
        self.library = library

    def retrieve(self, context_text: str, top_k: int = 3) -> list[tuple[PromptTemplate, float]]:
        context_vec = vectorize_text(context_text)
        scored: list[tuple[PromptTemplate, float]] = []
        for prompt in self.library.load():
            prompt_vec = vectorize_text(f"{prompt.title}\n{prompt.body}\n{' '.join(prompt.tags)}")
            scored.append((prompt, cosine_similarity(context_vec, prompt_vec)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
