import { useEffect, useState } from "react";

import ZoomInIcon from "@mui/icons-material/ZoomIn";
import ZoomOutIcon from "@mui/icons-material/ZoomOut";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import EditNoteIcon from "@mui/icons-material/EditNote";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import RefreshIcon from "@mui/icons-material/Refresh";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Snackbar,
  TextField,
  Typography,
} from "@mui/material";

import {
  getReviewQueueDocuments,
  approveDocument,
  rejectDocument,
  requestDocumentRevision,
} from "../api/document";
import type { DocumentDetail, OCRResult } from "../api/document";
import { useAuth } from "../context/AuthContext";
import {
  CONFIDENCE_EXPLANATIONS,
  formatConfidence,
} from "../utils/confidence";
import {
  PageStructuredTables,
  stripTableBlocks,
} from "../components/tables/StructuredTables";

export default function ReviewQueue() {
  const { user: currentUser } = useAuth();
  const [documents, setDocuments] = useState<DocumentDetail[]>([]);
  const [selectedDocumentIndex, setSelectedDocumentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [zoom, setZoom] = useState(100);
  const [threshold, setThreshold] = useState(100);

  // Dialog states for reject and revision
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [revisionDialogOpen, setRevisionDialogOpen] = useState(false);
  const [revisionNotes, setRevisionNotes] = useState("");

  const fetchQueue = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await getReviewQueueDocuments();
      setDocuments(response.data || []);
      if (selectedDocumentIndex >= (response.data || []).length) {
        setSelectedDocumentIndex(0);
      }
    } catch (err: any) {
      console.error("Failed to load review queue:", err);
      setError(
        err.response?.data?.detail || "Failed to load documents for review."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

  // The threshold slider keeps the documents a reviewer most needs to look at.
  // An unmeasured score is unknown quality, so it stays in the queue rather
  // than being counted as a confident document.
  const visibleDocuments = documents.filter((doc) => {
    const conf = doc.overall_confidence ?? 0;
    return conf <= threshold;
  });

  const activeDoc: DocumentDetail | null =
    visibleDocuments[selectedDocumentIndex] || null;

  const ocrPageNumber = (result: OCRResult): number | null =>
    activeDoc?.pages?.find((page) => page.id === result.page_id)?.page_number ??
    null;

  // Why the pipeline queued this document (low measured confidence, failed
  // pages, a stalled run). Read from the review queue rows the server writes.
  const reviewReasons = Array.from(
    new Set(
      (activeDoc?.review_items ?? [])
        .map((item) => (item.reason || "").trim())
        .filter((reason) => reason.length > 0)
    )
  );

  // Separation of duties check
  const isOwnDocument = Boolean(
    currentUser &&
      activeDoc &&
      activeDoc.uploaded_by != null &&
      activeDoc.uploaded_by === currentUser.id
  );

  const handleDocumentChange = (index: number) => {
    setSelectedDocumentIndex(index);
    setZoom(100);
  };

  const zoomIn = () => {
    setZoom((value) => Math.min(value + 10, 200));
  };

  const zoomOut = () => {
    setZoom((value) => Math.max(value - 10, 50));
  };

  const handleApprove = async () => {
    if (!activeDoc) return;
    try {
      setActionLoading(true);
      setError(null);
      await approveDocument(activeDoc.id);
      setSuccessMsg(`Document #${activeDoc.id} approved successfully.`);
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      if (selectedDocumentIndex >= visibleDocuments.length - 1) {
        setSelectedDocumentIndex(Math.max(0, visibleDocuments.length - 2));
      }
    } catch (err: any) {
      console.error("Approve failed:", err);
      setError(err.response?.data?.detail || "Failed to approve document.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRejectConfirm = async () => {
    if (!activeDoc) return;
    try {
      setActionLoading(true);
      setError(null);
      await rejectDocument(
        activeDoc.id,
        rejectReason.trim() || "Rejected during checker review"
      );
      setSuccessMsg(`Document #${activeDoc.id} has been rejected.`);
      setRejectDialogOpen(false);
      setRejectReason("");
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      if (selectedDocumentIndex >= visibleDocuments.length - 1) {
        setSelectedDocumentIndex(Math.max(0, visibleDocuments.length - 2));
      }
    } catch (err: any) {
      console.error("Reject failed:", err);
      setError(err.response?.data?.detail || "Failed to reject document.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRevisionConfirm = async () => {
    if (!activeDoc) return;
    try {
      setActionLoading(true);
      setError(null);
      await requestDocumentRevision(
        activeDoc.id,
        revisionNotes.trim() || "Revision requested by checker"
      );
      setSuccessMsg(`Revision requested for Document #${activeDoc.id}.`);
      setRevisionDialogOpen(false);
      setRevisionNotes("");
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      if (selectedDocumentIndex >= visibleDocuments.length - 1) {
        setSelectedDocumentIndex(Math.max(0, visibleDocuments.length - 2));
      }
    } catch (err: any) {
      console.error("Revision request failed:", err);
      setError(
        err.response?.data?.detail || "Failed to request document revision."
      );
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="reviewPage">

      {/* ================= HEADER ================= */}
      <div className="reviewHeader">
        <div>
          <h1>Review queue</h1>
          <p>
            Verify AI-extracted document metadata, validate compliance, and enforce separation of duties.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <div className="reviewThreshold">
            <span>Filter confidence &le;:</span>
            <input
              type="range"
              min="50"
              max="100"
              value={threshold}
              aria-label="Filter documents by maximum confidence"
              onChange={(event) => setThreshold(Number(event.target.value))}
            />
            <strong>{threshold}%</strong>
          </div>

          <IconButton
            onClick={fetchQueue}
            disabled={loading}
            sx={{
              backgroundColor: "rgba(255, 255, 255, 0.05)",
              borderRadius: "10px",
              "&:hover": { backgroundColor: "rgba(255, 255, 255, 0.1)" },
            }}
            title="Refresh Review Queue"
            aria-label="Refresh review queue"
          >
            <RefreshIcon />
          </IconButton>
        </div>
      </div>

      {error && (
        <Alert
          severity="error"
          sx={{ mb: 2, borderRadius: "10px" }}
          onClose={() => setError(null)}
        >
          {error}
        </Alert>
      )}

      {/* ================= WORKSPACE ================= */}
      <div className="reviewWorkspace">
        {/* ================= LEFT LIST ================= */}
        <aside className="reviewList">
          <div className="reviewListHeader">
            <div>
              <h2>Pending Documents</h2>
              <span>{visibleDocuments.length} needing review</span>
            </div>
          </div>

          <div className="reviewListItems">
            {loading ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
                <CircularProgress size={28} sx={{ color: "var(--accent, #a855f7)" }} />
              </Box>
            ) : visibleDocuments.length === 0 ? (
              <div className="emptyReviewList">
                <p>No documents currently requiring review.</p>
              </div>
            ) : (
              visibleDocuments.map((item, index) => {
                const isSelected = selectedDocumentIndex === index;
                const isItemOwn =
                  currentUser && item.uploaded_by === currentUser.id;

                return (
                  <button
                    key={item.id}
                    type="button"
                    className={
                      isSelected
                        ? "reviewListItem active"
                        : "reviewListItem"
                    }
                    onClick={() => handleDocumentChange(index)}
                  >
                    <div className="reviewListItemTop">
                      <div className="reviewDocumentIcon">
                        <DescriptionOutlinedIcon />
                      </div>

                      <div className="reviewDocumentName">
                        <strong>{item.original_filename}</strong>
                        <span>
                          {item.department || "General"} · {item.document_type || item.file_type}
                        </span>
                      </div>
                    </div>

                    <div className="reviewListItemBottom">
                      <span>ID #{item.id}</span>
                      {isItemOwn && (
                        <Chip
                          label="Your Upload"
                          size="small"
                          sx={{
                            height: 18,
                            fontSize: "0.6rem",
                            bgcolor: "rgba(239, 68, 68, 0.15)",
                            color: "#f87171",
                          }}
                        />
                      )}
                      <span
                        className="reviewConfidence"
                        title={CONFIDENCE_EXPLANATIONS.document}
                      >
                        {formatConfidence(item.overall_confidence)}
                      </span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        {/* ================= CENTER + RIGHT ================= */}
        <section className="reviewDetail">
          {activeDoc ? (
            <div className="reviewWorkspaceInner">
              {/* ================= DOCUMENT VIEWER ================= */}
              <div className="documentViewer">
                <div className="documentViewerHeader">
                  <div className="documentViewerTitle">
                    <strong>{activeDoc.original_filename}</strong>
                    <span>
                      {activeDoc.department || "No Department"} · {activeDoc.document_type || "Document"} (ID #{activeDoc.id})
                    </span>
                  </div>

                  <div className="documentViewerControls">
                    <button type="button" onClick={zoomOut} title="Zoom out">
                      <ZoomOutIcon />
                    </button>
                    <span className="zoomValue">{zoom}%</span>
                    <button type="button" onClick={zoomIn} title="Zoom in">
                      <ZoomInIcon />
                    </button>
                  </div>
                </div>

                {/* ================= DOCUMENT PREVIEW CONTENT ================= */}
                <div className="documentPreview">
                  <div
                    className="documentMock"
                    style={{
                      transform: `scale(${zoom / 100})`,
                      transformOrigin: "top center",
                      width: "100%",
                      maxWidth: "700px",
                      padding: "24px",
                      background: "rgba(255, 255, 255, 0.03)",
                      borderRadius: "12px",
                      border: "1px solid rgba(255, 255, 255, 0.08)",
                      color: "var(--text-primary, #f1f5f9)",
                    }}
                  >
                    <div style={{ marginBottom: "16px", borderBottom: "1px solid rgba(255,255,255,0.1)", paddingBottom: "12px" }}>
                      <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>
                        {activeDoc.original_filename}
                      </Typography>
                      <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mt: 1 }}>
                        <Chip
                          label={`Department: ${activeDoc.department || "Unassigned"}`}
                          size="small"
                          sx={{ bgcolor: "rgba(245, 158, 11, 0.15)", color: "#fbbf24", fontWeight: 600 }}
                        />
                        <Chip
                          label={`Type: ${activeDoc.document_type || "General"}`}
                          size="small"
                          sx={{ bgcolor: "rgba(59, 130, 246, 0.15)", color: "#60a5fa", fontWeight: 600 }}
                        />
                        <Chip
                          label={`Status: ${activeDoc.status || "PENDING_REVIEW"}`}
                          size="small"
                          sx={{ bgcolor: "rgba(168, 85, 247, 0.15)", color: "#c084fc", fontWeight: 600 }}
                        />
                      </Box>
                    </div>

                    <Typography variant="subtitle2" sx={{ color: "var(--text-secondary, #94a3b8)", mb: 1, textTransform: "uppercase", fontSize: "0.75rem", letterSpacing: 1 }}>
                      Extracted Text / OCR Content
                    </Typography>

                    <Box
                      sx={{
                        background: "rgba(0, 0, 0, 0.2)",
                        p: 2,
                        borderRadius: "8px",
                        fontFamily: "monospace",
                        fontSize: "0.85rem",
                        lineHeight: 1.6,
                        maxHeight: "380px",
                        overflowY: "auto",
                        whiteSpace: "pre-wrap",
                      }}
                    >
                      {activeDoc.ocr_results && activeDoc.ocr_results.length > 0 ? (
                        [...activeDoc.ocr_results]
                          .sort(
                            (a, b) =>
                              (ocrPageNumber(a) ?? 0) - (ocrPageNumber(b) ?? 0)
                          )
                          .map((res, i) => {
                            const pageNumber = ocrPageNumber(res);
                            const measured =
                              typeof res.confidence === "number" &&
                              Number.isFinite(res.confidence);
                            const failed = (res.ocr_engine || "").includes("Failed");
                            const pageTables = (activeDoc.extracted_tables || []).filter(
                              (table) =>
                                pageNumber != null && table.page_number === pageNumber
                            );
                            // Rows the reader measured are drawn as a table; the raw
                            // "cell | cell" copy of them is not shown twice. With no
                            // structure for the page, the raw text is the fallback.
                            const rawText =
                              res.extracted_text ||
                              (failed
                                ? "OCR failed on this page: no text was recovered."
                                : "(No text detected on this page)");
                            const prose = pageTables.length
                              ? stripTableBlocks(rawText)
                              : rawText;

                            return (
                              <div key={res.id || i} style={{ marginBottom: "12px" }}>
                                <div
                                  title={CONFIDENCE_EXPLANATIONS.page}
                                  style={{
                                    color: "#94a3b8",
                                    fontSize: "0.75rem",
                                    marginBottom: 4,
                                  }}
                                >
                                  {pageNumber ? `Page ${pageNumber}` : "Whole document"}
                                  {res.ocr_engine ? ` · ${res.ocr_engine}` : ""}
                                  {` · ${
                                    measured
                                      ? `${formatConfidence(res.confidence)} word confidence`
                                      : "word confidence not measured"
                                  }`}
                                </div>
                                {pageTables.length > 0 && (
                                  <PageStructuredTables tables={pageTables} />
                                )}
                                {prose ? prose : null}
                              </div>
                            );
                          })
                      ) : (
                        <div style={{ color: "#94a3b8", fontStyle: "italic" }}>
                          No OCR text extracted for this document.
                        </div>
                      )}
                    </Box>
                  </div>
                </div>

                {/* ================= VIEWER FOOTER ================= */}
                <div className="documentViewerFooter">
                  <span>
                    Uploaded by: <strong>{activeDoc.uploader_name || `User #${activeDoc.uploaded_by || "System"}`}</strong>
                  </span>
                  <span>
                    Date: <strong>{activeDoc.uploaded_at ? new Date(activeDoc.uploaded_at).toLocaleString() : "Recently"}</strong>
                  </span>
                </div>
              </div>

              {/* ================= RIGHT PANEL ================= */}
              <aside className="extractedPanel">
                <div className="extractedPanelHeader">
                  <div>
                    <h2>Review &amp; Verify</h2>
                    <p>Review AI-extracted fields and make authorization decisions.</p>
                  </div>

                  <div
                    className="overallConfidence"
                    title={CONFIDENCE_EXPLANATIONS.document}
                  >
                    <span>Overall quality score</span>
                    <strong>
                      {formatConfidence(activeDoc.overall_confidence)}
                    </strong>
                  </div>
                </div>

                {/* Why this document is in the queue */}
                {reviewReasons.length > 0 && (
                  <Alert
                    severity="warning"
                    icon={<WarningAmberIcon />}
                    sx={{
                      m: 2,
                      mb: 0,
                      borderRadius: "10px",
                      fontSize: "0.8rem",
                      bgcolor: "rgba(56, 189, 248, 0.12)",
                      border: "1px solid rgba(56, 189, 248, 0.3)",
                      color: "#7dd3fc",
                    }}
                  >
                    <div>
                      <strong>Why this needs review</strong>
                      <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                        {reviewReasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    </div>
                  </Alert>
                )}

                {/* Separation of Duties Warning */}
                {isOwnDocument && (
                  <Alert
                    severity="warning"
                    icon={<WarningAmberIcon />}
                    sx={{
                      m: 2,
                      mb: 1,
                      borderRadius: "10px",
                      fontSize: "0.8rem",
                      bgcolor: "rgba(245, 158, 11, 0.12)",
                      border: "1px solid rgba(245, 158, 11, 0.3)",
                      color: "#fbbf24",
                    }}
                  >
                    <strong>Separation of Duties:</strong> You uploaded this document. As a security requirement, you cannot approve, reject, or request revisions on your own uploads.
                  </Alert>
                )}

                <div className="extractedFields">
                  <div style={{ padding: "0 16px 12px 16px" }}>
                    <div className="extractedField">
                      <div className="extractedFieldHeader">
                        <label>Department</label>
                        {/* A chosen department is a fact, not a score; only
                            auto-detected ones carry any uncertainty. */}
                        <span className="fieldConfidence">
                          {activeDoc.department ? "specified" : "auto-detected"}
                        </span>
                      </div>
                      <input type="text" readOnly value={activeDoc.department || "Not specified"} aria-label="Department" />
                    </div>

                    <div className="extractedField">
                      <div className="extractedFieldHeader">
                        <label>Document Type</label>
                        <span
                          className="fieldConfidence"
                          title="Classification confidence is not stored per document, so the overall quality score is shown instead."
                        >
                          {formatConfidence(activeDoc.overall_confidence)}
                        </span>
                      </div>
                      <input type="text" readOnly value={activeDoc.document_type || "General Document"} aria-label="Document type" />
                    </div>

                    {activeDoc.extracted_fields && activeDoc.extracted_fields.length > 0 ? (
                      activeDoc.extracted_fields.map((field) => {
                        const strength =
                          typeof field.confidence === "number" &&
                          Number.isFinite(field.confidence)
                            ? Math.round(field.confidence)
                            : null;
                        return (
                          <div className="extractedField" key={field.id || field.field_name}>
                            <div className="extractedFieldHeader">
                              <label>{field.field_name}</label>
                              <span
                                className={
                                  strength !== null && strength < 70
                                    ? "fieldConfidence low"
                                    : "fieldConfidence"
                                }
                                title={CONFIDENCE_EXPLANATIONS.field}
                              >
                                {formatConfidence(field.confidence)}
                              </span>
                            </div>
                            <input type="text" defaultValue={field.field_value || ""} aria-label={`${field.field_name} extracted value`} />
                            <div
                              className="fieldConfidenceBar"
                              title={CONFIDENCE_EXPLANATIONS.field}
                            >
                              <div
                                className="fieldConfidenceFill"
                                style={{ width: `${strength ?? 0}%` }}
                              />
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <div style={{ padding: "12px 0", color: "#9ca3af", fontSize: "0.85rem" }}>
                        No structured key-value fields detected.
                      </div>
                    )}
                  </div>
                </div>

                {/* ================= REVIEW ACTIONS ================= */}
                <div className="reviewActions">
                  <button
                    type="button"
                    className="rejectButton"
                    onClick={() => setRejectDialogOpen(true)}
                    disabled={actionLoading || isOwnDocument}
                    title={isOwnDocument ? "Separation of duties: Cannot reject your own upload" : "Reject document"}
                  >
                    <CloseIcon />
                    Reject
                  </button>

                  <button
                    type="button"
                    className="revisionButton"
                    onClick={() => setRevisionDialogOpen(true)}
                    disabled={actionLoading || isOwnDocument}
                    title={isOwnDocument ? "Separation of duties: Cannot request revision on your own upload" : "Request revision"}
                  >
                    <EditNoteIcon />
                    Revision
                  </button>

                  <button
                    type="button"
                    className="approveButton"
                    onClick={handleApprove}
                    disabled={actionLoading || isOwnDocument}
                    title={isOwnDocument ? "Separation of duties: Cannot approve your own upload" : "Approve document"}
                  >
                    <CheckIcon />
                    {actionLoading ? "Processing..." : "Approve"}
                  </button>
                </div>
              </aside>
            </div>
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", py: 12 }}>
              <DescriptionOutlinedIcon sx={{ fontSize: 64, color: "var(--text-secondary, #64748b)", mb: 2 }} />
              <Typography variant="h6" sx={{ color: "var(--text-primary, #f1f5f9)", fontWeight: 600 }}>
                No Document Selected
              </Typography>
              <Typography variant="body2" sx={{ color: "var(--text-secondary, #94a3b8)" }}>
                Select a document from the left list to review its content.
              </Typography>
            </Box>
          )}
        </section>
      </div>

      {/* Reject Confirmation Dialog */}
      <Dialog
        open={rejectDialogOpen}
        onClose={() => setRejectDialogOpen(false)}
        slotProps={{
          paper: {
            sx: {
              background: "#1e293b",
              color: "#f8fafc",
              borderRadius: "16px",
              minWidth: "400px",
            },
          },
        }}
      >
        <DialogTitle sx={{ fontWeight: 700 }}>Reject Document</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: "#94a3b8", mb: 2 }}>
            Provide a reason for rejecting document #{activeDoc?.id}.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            multiline
            rows={3}
            placeholder="e.g. Document image is illegible or missing required signatures."
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            sx={{
              backgroundColor: "rgba(255, 255, 255, 0.05)",
              borderRadius: "8px",
              "& .MuiInputBase-input": { color: "#f8fafc" },
            }}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setRejectDialogOpen(false)} sx={{ color: "#94a3b8" }}>
            Cancel
          </Button>
          <Button
            onClick={handleRejectConfirm}
            variant="contained"
            color="error"
            disabled={actionLoading}
            sx={{ borderRadius: "8px", fontWeight: 600 }}
          >
            {actionLoading ? "Rejecting..." : "Confirm Reject"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Request Revision Dialog */}
      <Dialog
        open={revisionDialogOpen}
        onClose={() => setRevisionDialogOpen(false)}
        slotProps={{
          paper: {
            sx: {
              background: "#1e293b",
              color: "#f8fafc",
              borderRadius: "16px",
              minWidth: "400px",
            },
          },
        }}
      >
        <DialogTitle sx={{ fontWeight: 700 }}>Request Revision</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: "#94a3b8", mb: 2 }}>
            Specify the required corrections for the uploader of document #{activeDoc?.id}.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            multiline
            rows={3}
            placeholder="e.g. Please re-upload with page 2 clearly legible and high resolution."
            value={revisionNotes}
            onChange={(e) => setRevisionNotes(e.target.value)}
            sx={{
              backgroundColor: "rgba(255, 255, 255, 0.05)",
              borderRadius: "8px",
              "& .MuiInputBase-input": { color: "#f8fafc" },
            }}
          />
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setRevisionDialogOpen(false)} sx={{ color: "#94a3b8" }}>
            Cancel
          </Button>
          <Button
            onClick={handleRevisionConfirm}
            variant="contained"
            sx={{
              borderRadius: "8px",
              fontWeight: 600,
              backgroundColor: "#d97706",
              "&:hover": { backgroundColor: "#b45309" },
            }}
            disabled={actionLoading}
          >
            {actionLoading ? "Submitting..." : "Submit Revision Request"}
          </Button>
        </DialogActions>
      </Dialog>

      <Snackbar
        open={Boolean(successMsg)}
        autoHideDuration={4000}
        onClose={() => setSuccessMsg(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        <Alert severity="success" onClose={() => setSuccessMsg(null)}>
          {successMsg}
        </Alert>
      </Snackbar>
    </div>
  );
}