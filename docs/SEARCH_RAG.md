# Search and Citation-Based Q&A

## Current flow

Successful OCR output is stored in the existing `OCRResult` and `DocumentPage` rows. Upload processing schedules embedding indexing in a background thread; application startup backfills documents with OCR rows that do not yet have `EmbeddingIndex` rows. ChromaDB stores page-local chunks. Stable IDs use document, page, and chunk indexes, and reindexing upserts current chunks before deleting stale chunk IDs. Deleting a document removes its Chroma entries through the existing delete route.

Chunking preserves paragraphs and sentence boundaries where practical, limits chunks by configurable character count, and keeps page boundaries by indexing each OCR result separately. The current implementation does not have reliable section, table-cell, or form-field structure in the OCR schema, so it does not synthesize table rows or claim those relationships are preserved. Original OCR text is left intact.

The default embedding model is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`; it is used for both indexing and query embeddings. The model is multilingual, but DocSentinel has not yet measured retrieval quality separately for English, Hindi, Marathi, or cross-language queries. Do not treat model language tags as accuracy evidence.

The `/api/search` hybrid mode combines keyword and vector rankings with reciprocal rank fusion (RRF). The RRF score expresses rank contribution, not a probability. This path does not currently use a learned reranker. Keyword mode retains exact substring matches for names, numbers, and identifiers. Semantic scores are vector similarities and should not be interpreted as answer correctness.

Q&A semantic candidates must meet the configurable `RAG_MIN_SIMILARITY` cosine-similarity cutoff (default `0.2`) before they can become evidence. Exact lexical hits remain available through the hybrid path.

## Authorization and citations

Search and `/api/search/ask` require the existing authenticated-user dependency. Before retrieval, the API derives the readable document IDs from existing RBAC and configured folder permissions. SQL search clauses and Chroma metadata filters are restricted to this set; returned vector metadata is checked against database documents before it becomes a hit or citation. The document viewer checks access again when the citation link opens.

`POST /api/search/ask` accepts `question`, `include_citations` (default `true`), and optional `top_k`. It returns `answer`, `citations`, `evidence_status`, and the original `query`. Citation records are built by the backend from retrieved excerpts and database document metadata; model output cannot supply document IDs, filenames, or URLs. Links use the existing library preview route and its OCR page panel. The original PDF page viewer is not currently deep-linked.

When all of `RAG_LLM_BASE_URL`, `RAG_LLM_MODEL`, and `RAG_LLM_API_KEY` are set, the backend calls an OpenAI-compatible `/chat/completions` endpoint with a bounded request timeout and evidence-only instructions. Retrieved text is marked as untrusted input. If generation is unconfigured or fails, the endpoint returns retrieved passages with `retrieved_only`; when no passages are available it abstains with `insufficient_evidence`. Hiding visible citations does not remove evidence from retrieval or model context.

Marker IDs in a generated answer are checked against the available source list. This validates provenance and navigation, but not claim entailment: no independent claim-to-excerpt verifier is implemented. Generated answers should be checked against the source excerpt for high-impact decisions. Multi-source material is provided to the model, but comparisons and calculations have not been accuracy validated.

## Configuration and commands

Set these values in `backend/.env` when enabling generated answers. Leave them blank for retrieval-only operation:

```dotenv
RAG_LLM_BASE_URL=https://your-openai-compatible-provider/v1
RAG_LLM_MODEL=your-model-name
RAG_LLM_API_KEY=your-secret-key
RAG_LLM_TIMEOUT_SECONDS=30
RAG_RETRIEVAL_TOP_K=8
RAG_MIN_SIMILARITY=0.2
```

The base URL should be the provider's API root; the backend appends `/chat/completions`. Never put the key in frontend configuration. Install backend dependencies from the project's existing `backend/requirements.txt`, then run from the repository root:

```powershell
python backend/scripts/reindex_embeddings.py
python backend/scripts/reindex_embeddings.py --document-id 123
python backend/scripts/search_benchmark.py --manifest backend/tests/data/search_queries.jsonl --k 10
```

Reindexing reads existing OCR rows and does not rerun OCR. It may take time because embeddings are recomputed. The benchmark command expects a manually judged UTF-8 JSONL file with `query`, `language`, `query_type`, and `relevant_document_ids`; no ground-truth corpus is included, so the command cannot produce meaningful project accuracy metrics until that file is supplied. Optional `expected_no_answer`, `expected_citation_document_ids`, and `expected_answer_terms` fields exercise abstention/citation retrieval and provide string-anchor diagnostics. Anchor coverage is not a correctness metric.

Run the search and OCR regressions with:

```powershell
python -m pytest backend/tests/test_search_rag.py backend/tests/test_semantic_search_api.py backend/tests/test_ocr_pipeline.py -q
```

The default development setup uses the configured SQL database and persistent Chroma directory. Tests requiring real semantic indexing need both `chromadb` and the test embedding helper; normal unit tests mock provider calls and do not require paid LLM access.

## Current limits

- No labeled evaluation dataset is present, so Recall@k, Precision@k, MRR, nDCG, language-level accuracy, latency targets, and Q&A accuracy have not been measured.
- The OCR data contract exposes page text, not dependable table/heading/form structures. Table-specific row serialization is not implemented.
- The background indexer is a daemon thread. Failures are logged, but there is no durable job state, retry queue, or user-visible retry action.
- Startup backfill finds documents with no `EmbeddingIndex` rows; it does not detect a changed model version. Run the explicit reindex command after changing the embedding model or chunking configuration.
- The application uses the existing Chroma collection. A model/dimension migration needs a planned rebuild and backup of the vector directory.
- Search computes the readable document ID set from loaded database records. This is permission-safe but may need SQL-side optimization for very large repositories.
- The current chat state is local to the page and is not sent as conversation history. Each question is grounded independently.
