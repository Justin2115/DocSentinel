from hashlib import sha256

from app.core.config import settings
from app.services.embedding_service import reset_chroma_client, set_encode_override


def dummy_encode(texts: list[str]) -> list[list[float]]:
    dim = 32
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * dim
        lowered = (text or "").lower()
        for token in lowered.replace("\n", " ").split():
            digest = sha256(token.encode("utf-8")).digest()
            for index, byte in enumerate(digest[:dim]):
                vec[index] += byte / 255.0
        for index, char in enumerate(lowered[:dim]):
            vec[index % dim] += (ord(char) % 13) / 13.0
        norm = sum(value * value for value in vec) ** 0.5 or 1.0
        vectors.append([value / norm for value in vec])
    return vectors


def use_test_embeddings(tmp_path) -> None:
    settings.CHROMA_PERSIST_DIR = str(tmp_path / "chroma")
    reset_chroma_client()
    set_encode_override(dummy_encode)
