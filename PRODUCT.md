# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

DocSentinel is for internal teams in organizations that handle controlled documents. The application supports three roles: Admin, Upload Maker, and Upload Checker. Makers upload documents; checkers review assigned documents; admins manage users, access rules, and workflows. Users need to find and act on information while respecting department, document, and role boundaries.

## Product Purpose

DocSentinel transforms digital and scanned documents into searchable, actionable, and verifiable information. It supports document ingestion, multilingual OCR for English, Hindi, and Marathi, semantic and keyword search, evidence-grounded question answering, and structured data extraction. Success means people can retrieve useful information and verify answers against their source documents, while documents move through secure, accountable review workflows.

## Positioning

DocSentinel combines multilingual document processing and semantic retrieval with answers linked to source documents, so users can verify information instead of relying on unsupported AI responses. Its document lifecycle connects ingestion, extraction, access control, and maker-checker review.

## Operating Context

The product is used by internal teams managing documents across departments. Makers upload documents; the system extracts and classifies content and can route items for review; checkers review assigned items; admins manage people, permissions, and workflow settings. Users search by keywords or meaning and can ask questions whose answers should be grounded in retrieved document evidence. Access and review eligibility depend on role, department, assignment, and document permissions. A maker must not approve their own upload.

## Capabilities and Constraints

- The application supports document upload and management, OCR and extraction, document classification, keyword and semantic search, source-linked question answering, a document library, review queues, and administrative configuration.
- OCR supports English, Hindi, and Marathi, including Devanagari text, according to project documentation.
- Authentication and role-based authorization are enforced by the backend. Document access also depends on department, assignment, and configured permissions.
- Maker-checker separation is a product constraint: users cannot review their own uploads.
- Extracted information requires human validation through the review workflow. Do not present extraction as authoritative without review or confidence context.
- OCR engines, LLM providers, embedding models, and retrieval methods should remain replaceable as technology evolves.
- Accuracy, traceability, reliability, usability, and measurable performance are enduring product commitments.
- Describe individual capabilities according to their verified implementation status. AI-assisted fraud detection is planned and must remain described as future work until implemented and tested.
- The project documentation describes local development and Docker-based deployment; production storage and scale remain deployment-specific rather than established product guarantees.

## Brand Commitments

The product name is DocSentinel. No other binding voice, visual identity, or brand asset requirements were established.

## Evidence on Hand

- Repository documentation covering product features, architecture, authentication, database schema, ingestion contracts, and chat conversations is available under `docs/`.
- Backend tests cover authentication, access control, document processing, extraction, search, and conversations. These are implementation evidence, not proof of production readiness or specific accuracy levels.
- No customer testimonials, external case studies, or verified performance benchmarks were identified. Do not invent them.

## Product Principles

- Ground answers in retrievable source evidence so people can check them.
- Enforce confidentiality and permissions throughout every workflow.
- Keep human review and maker-checker accountability in consequential document decisions.
- Make the processing pipeline measurable and communicate uncertainty honestly.
- Keep AI and retrieval components replaceable as capabilities and requirements change.
