import pytest

from app.services.embedding_service import reset_chroma_client, set_encode_override
from tests.helpers import use_test_embeddings


@pytest.fixture(autouse=True)
def fake_embeddings(tmp_path):
    use_test_embeddings(tmp_path)
    yield
    set_encode_override(None)
    reset_chroma_client()
