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


MULTILINGUAL_CONCEPTS = {
    "invoice_payment": {
        # English
        "invoice", "payment", "unpaid", "bill", "billing", "settle", "due", "overdue", "pay", "receipt", "pending",
        # Hindi
        "चालान", "भुगतान", "बकाया", "बिल", "पावती", "रकम",
        # Marathi
        "बील", "बिलाचे", "पैसे", "भरणा", "पावती", "रक्कम", "अद्याप", "भरलेले", "नाहीत",
    },
    "employment_salary": {
        # English
        "employee", "salary", "payroll", "wage", "compensation", "staff",
        # Hindi
        "कर्मचारी", "वेतन", "तनख्वाह",
        # Marathi
        "कर्मचारी", "पगार", "वेतन",
    },
    "legal_contract": {
        # English
        "contract", "agreement", "legal", "clause", "terms",
        # Hindi
        "अनुबंध", "समझौता", "कानूनी",
        # Marathi
        "करार", "करारनामा", "कायदेशीर",
    },
}


def mock_multilingual_encode(texts: list[str]) -> list[list[float]]:
    """Deterministic multilingual concept embedding mock for unit tests.

    Maps semantic equivalents across English, Hindi, and Marathi to nearby vectors.
    """
    dim = 32
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.05] * dim
        lowered = (text or "").lower()
        tokens = set(lowered.replace("\n", " ").replace(".", " ").replace("।", " ").split())

        # Check concept matches
        for c_idx, (c_name, cluster_words) in enumerate(MULTILINGUAL_CONCEPTS.items()):
            overlap = tokens.intersection(cluster_words)
            if overlap:
                # Strong signal in dedicated concept dimensions
                base_slot = c_idx * 8
                weight = 1.0 + len(overlap) * 0.5
                for offset in range(6):
                    vec[base_slot + offset] += weight

        # Small hash perturbation for unique textual variance
        digest = sha256(lowered.encode("utf-8")).digest()
        for i in range(4):
            vec[i] += (digest[i] / 255.0) * 0.1

        norm = sum(val * val for val in vec) ** 0.5 or 1.0
        vectors.append([val / norm for val in vec])
    return vectors


def use_test_embeddings(tmp_path, multilingual: bool = False) -> None:
    settings.CHROMA_PERSIST_DIR = str(tmp_path / "chroma")
    reset_chroma_client()
    if multilingual:
        set_encode_override(mock_multilingual_encode)
    else:
        set_encode_override(dummy_encode)


def get_test_auth_headers(db, email: str = "admin@docsentinel.local", role: str = "ADMIN", department: str | None = None) -> dict[str, str]:
    from app.core.security import create_access_token
    from app.models.user import User

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name=email.split("@")[0],
            role=role,
            department=department,
            is_active=True,
            password_hash="test_password_hash",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif user.role != role or user.department != department:
        user.role = role
        user.department = department
        db.commit()
        db.refresh(user)

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"Authorization": f"Bearer {token}"}
