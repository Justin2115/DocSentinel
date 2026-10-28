import { useEffect, useState } from "react";

import ZoomInIcon from "@mui/icons-material/ZoomIn";
import ZoomOutIcon from "@mui/icons-material/ZoomOut";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import SaveIcon from "@mui/icons-material/Save";
import EditNoteIcon from "@mui/icons-material/EditNote";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import RefreshIcon from "@mui/icons-material/Refresh";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import SearchIcon from "@mui/icons-material/Search";
import RateReviewIcon from "@mui/icons-material/RateReview";
import TextFieldsIcon from "@mui/icons-material/TextFields";
import TableChartIcon from "@mui/icons-material/TableChart";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
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
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";

import {
  getDocument,
  getReviewQueueDocuments,
  approveDocument,
  rejectDocument,
  requestDocumentRevision,
  updateExtractedField,
  updateDocumentOCRText,
} from "../api/document";
import type { DocumentDetail, OCRResult } from "../api/document";
import {
  applySavedOcrToDocuments,
  canonicalExtractedText,
  ocrSaveFeedback,
  originalOcrText,
} from "../utils/ocrCorrection";
import { useAuth } from "../context/AuthContext";
import {
  formatConfidence,
} from "../utils/confidence";
import {
  PageStructuredTables,
} from "../components/tables/StructuredTables";

export default function ReviewQueue() {
  const { user: currentUser } = useAuth();
  const [documents, setDocuments] = useState<DocumentDetail[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Queue list search filter
  const [queueSearch, setQueueSearch] = useState("");

  // Review detail state
  const [detailTab, setDetailTab] = useState<"ocr" | "fields" | "info">("ocr");
  const [zoom, setZoom] = useState(100);

  // Editable fields state
  const [fieldValues, setFieldValues] = useState<Record<number, string>>({});
  const [savingFieldId, setSavingFieldId] = useState<number | null>(null);

  // Editable OCR text state
  const [editingOcrId, setEditingOcrId] = useState<number | null>(null);
  const [ocrTextValues, setOcrTextValues] = useState<Record<number, string>>({});
  const [savingOcrId, setSavingOcrId] = useState<number | null>(null);

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

  const activeDoc: DocumentDetail | null =
    documents.find((d) => d.id === selectedDocId) || null;

  // Initialize field values when activeDoc changes
  useEffect(() => {
    if (activeDoc) {
      const initialFields: Record<number, string> = {};
      activeDoc.extracted_fields?.forEach((f) => {
        initialFields[f.id] = f.corrected_value ?? f.field_value ?? "";
      });
      setFieldValues(initialFields);

      const initialOcr: Record<number, string> = {};
      activeDoc.ocr_results?.forEach((ocr) => {
        initialOcr[ocr.id] = canonicalExtractedText(ocr);
      });
      setOcrTextValues(initialOcr);
      setEditingOcrId(null);
      setZoom(100);
      setDetailTab("ocr");
    }
  }, [selectedDocId]);

  const ocrPageNumber = (result: OCRResult): number | null =>
    activeDoc?.pages?.find((page) => page.id === result.page_id)?.page_number ??
    null;

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

  // Field update handler
  const handleFieldSave = async (fieldId: number) => {
    if (!activeDoc) return;
    const val = fieldValues[fieldId];
    if (val === undefined) return;
    try {
      setSavingFieldId(fieldId);
      await updateExtractedField(activeDoc.id, fieldId, val);
      setSuccessMsg("Extracted field updated successfully.");
      setDocuments((prev) =>
        prev.map((d) =>
          d.id === activeDoc.id
            ? {
                ...d,
                extracted_fields: d.extracted_fields?.map((f) =>
                  f.id === fieldId ? { ...f, field_value: val, corrected_value: val } : f
                ),
              }
            : d
        )
      );
    } catch (err: any) {
      console.error("Failed to update field:", err);
      setError(err.response?.data?.detail || "Failed to update field value.");
    } finally {
      setSavingFieldId(null);
    }
  };

  // OCR text update handler
  const handleOcrTextSave = async (ocrId: number) => {
    if (!activeDoc) return;
    const newText = ocrTextValues[ocrId];
    if (newText === undefined) return;
    try {
      setSavingOcrId(ocrId);
      setError(null);
      const patchResponse = await updateDocumentOCRText(
        activeDoc.id,
        ocrId,
        newText
      );
      let saved: OCRResult = patchResponse.data;
      setDocuments((prev) =>
        applySavedOcrToDocuments(prev, activeDoc.id, saved)
      );
      setOcrTextValues((prev) => ({
        ...prev,
        [ocrId]: canonicalExtractedText(saved),
      }));
      const feedback = ocrSaveFeedback({ ok: true, ocr: saved });
      setSuccessMsg(feedback.successMessage);
      setEditingOcrId(null);
      try {
        const fresh = await getDocument(activeDoc.id);
        const fromFetch = fresh.data.ocr_results?.find(
          (ocr) => Number(ocr.id) === Number(ocrId)
        );
        if (fromFetch) {
          saved = fromFetch;
          setDocuments((prev) =>
            prev.map((d) =>
              d.id === fresh.data.id ? { ...d, ...fresh.data } : d
            )
          );
          setOcrTextValues((prev) => ({
            ...prev,
            [ocrId]: canonicalExtractedText(fromFetch),
          }));
        }
      } catch {
        // PATCH already persisted; local merge is enough if refetch fails.
      }
    } catch (err: any) {
      console.error("Failed to update OCR text:", err);
      const feedback = ocrSaveFeedback({
        ok: false,
        detail:
          err.response?.data?.detail || "Failed to update OCR text.",
      });
      setSuccessMsg(null);
      setError(feedback.errorMessage);
    } finally {
      setSavingOcrId(null);
    }
  };

  const handleApprove = async () => {
    if (!activeDoc) return;
    try {
      setActionLoading(true);
      setError(null);
      await approveDocument(activeDoc.id);
      setSuccessMsg(`Document #${activeDoc.id} approved successfully.`);
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      setSelectedDocId(null);
    } catch (err: any) {
      console.error("Approval failed:", err);
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
      await rejectDocument(activeDoc.id, rejectReason.trim() || undefined);
      setSuccessMsg(`Document #${activeDoc.id} rejected.`);
      setRejectDialogOpen(false);
      setRejectReason("");
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      setSelectedDocId(null);
    } catch (err: any) {
      console.error("Rejection failed:", err);
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
      setSuccessMsg(`Revision requested for Document #${activeDoc.id}. Returned to maker.`);
      setRevisionDialogOpen(false);
      setRevisionNotes("");
      setDocuments((prev) => prev.filter((d) => d.id !== activeDoc.id));
      setSelectedDocId(null);
    } catch (err: any) {
      console.error("Revision request failed:", err);
      setError(
        err.response?.data?.detail || "Failed to request document revision."
      );
    } finally {
      setActionLoading(false);
    }
  };

  // Filter queue by search
  const filteredQueue = documents.filter((doc) => {
    if (!queueSearch.trim()) return true;
    const q = queueSearch.toLowerCase();
    return (
      doc.original_filename?.toLowerCase().includes(q) ||
      doc.department?.toLowerCase().includes(q) ||
      doc.document_type?.toLowerCase().includes(q) ||
      String(doc.id).includes(q)
    );
  });

  return (
    <div className="reviewPage" style={{ padding: "24px", maxWidth: "1600px", margin: "0 auto" }}>
      {/* ================= HEADER ================= */}
      <div
        className="reviewHeader"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "20px",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div>
          <Typography variant="h5" sx={{ fontWeight: 700, color: "var(--text-primary)" }}>
            Review Queue
          </Typography>
          <Typography variant="body2" sx={{ color: "var(--text-secondary)" }}>
            Review, verify OCR accuracy, edit extracted fields, and make authorization decisions.
          </Typography>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {currentUser?.department ? (
            <Chip
              label={`Department: ${currentUser.department}`}
              sx={{
                bgcolor: "var(--surface-secondary)",
                color: "var(--text-secondary)",
                fontWeight: 600,
                fontSize: "0.8rem",
              }}
            />
          ) : (
            <Chip
              label="Super Admin · All Departments"
              sx={{
                bgcolor: "var(--surface-secondary)",
                color: "var(--text-secondary)",
                fontWeight: 600,
                fontSize: "0.8rem",
              }}
            />
          )}

          <IconButton
            onClick={fetchQueue}
            disabled={loading}
            sx={{
              backgroundColor: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "10px",
              "&:hover": { backgroundColor: "var(--surface-secondary)" },
            }}
            title="Refresh Queue"
            aria-label="Refresh review queue"
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </div>
      </div>

      {error && (
        <Alert
          severity="error"
          sx={{ mb: 3, borderRadius: "10px" }}
          onClose={() => setError(null)}
        >
          {error}
        </Alert>
      )}

      {/* ======================================================== */}
      {/* 1. QUEUE LIST VIEW (When no document is actively opened) */}
      {/* ======================================================== */}
      {selectedDocId === null ? (
        <div className="queueListView">
          {/* Queue Filter Bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "16px",
              flexWrap: "wrap",
              gap: "12px",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "10px",
                padding: "6px 12px",
                minWidth: "300px",
              }}
            >
              <SearchIcon sx={{ color: "var(--text-secondary)", fontSize: 20 }} />
              <input
                type="text"
                placeholder="Filter documents in queue..."
                value={queueSearch}
                onChange={(e) => setQueueSearch(e.target.value)}
                style={{
                  border: "none",
                  outline: "none",
                  background: "transparent",
                  color: "var(--text-primary)",
                  fontSize: "0.85rem",
                  width: "100%",
                }}
              />
            </div>

            <Chip
              label={`${filteredQueue.length} ${filteredQueue.length === 1 ? "Document" : "Documents"} Pending Review`}
              color="primary"
              variant="outlined"
              sx={{ fontWeight: 600, fontSize: "0.85rem" }}
            />
          </div>

          {loading ? (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", py: 10, gap: 2 }}>
              <CircularProgress size={36} sx={{ color: "var(--primary)" }} />
              <Typography variant="body2" sx={{ color: "var(--text-secondary)" }}>
                Loading documents requiring review...
              </Typography>
            </Box>
          ) : filteredQueue.length === 0 ? (
            <Box
              sx={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                py: 10,
                textAlign: "center",
                px: 3,
              }}
            >
              <DescriptionOutlinedIcon sx={{ fontSize: 56, color: "var(--text-secondary)", mb: 2 }} />
              <Typography variant="h6" sx={{ fontWeight: 700, color: "var(--text-primary)", mb: 1 }}>
                {queueSearch ? "No matching documents" : "Review Queue is Empty"}
              </Typography>
              <Typography variant="body2" sx={{ color: "var(--text-secondary)", maxWidth: "480px", mx: "auto" }}>
                {queueSearch
                  ? "No documents match the current filter. Try clearing the search term."
                  : "All submitted documents have been reviewed and verified. When new documents are uploaded or flagged, they will appear here."}
              </Typography>
            </Box>
          ) : (
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                overflow: "hidden",
                boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
              }}
            >
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.85rem" }}>
                <thead>
                  <tr
                    style={{
                      borderBottom: "1px solid var(--border)",
                      background: "var(--surface-secondary)",
                      color: "var(--text-secondary)",
                      fontWeight: 600,
                    }}
                  >
                    <th style={{ padding: "12px 16px" }}>Document</th>
                    <th style={{ padding: "12px 16px" }}>Department</th>
                    <th style={{ padding: "12px 16px" }}>Submitted By</th>
                    <th style={{ padding: "12px 16px" }}>Submission Date</th>
                    <th style={{ padding: "12px 16px" }}>OCR Quality</th>
                    <th style={{ padding: "12px 16px" }}>Status</th>
                    <th style={{ padding: "12px 16px", textAlign: "right" }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredQueue.map((item) => {
                    const isItemOwn = currentUser && item.uploaded_by === currentUser.id;
                    const confidenceNum =
                      typeof item.overall_confidence === "number"
                        ? Math.round(item.overall_confidence)
                        : null;

                    return (
                      <tr
                        key={item.id}
                        style={{
                          borderBottom: "1px solid var(--border)",
                          transition: "background 0.15s ease",
                        }}
                        onMouseEnter={(e) =>
                          (e.currentTarget.style.backgroundColor = "var(--surface-secondary)")
                        }
                        onMouseLeave={(e) =>
                          (e.currentTarget.style.backgroundColor = "transparent")
                        }
                      >
                        <td style={{ padding: "14px 16px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                            <div
                              style={{
                                width: "36px",
                                height: "36px",
                                borderRadius: "8px",
                                background: "var(--surface-secondary)",
                                color: "var(--text-secondary)",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                flexShrink: 0,
                              }}
                            >
                              <DescriptionOutlinedIcon fontSize="small" />
                            </div>
                            <div>
                              <strong
                                style={{
                                  display: "block",
                                  color: "var(--text-primary)",
                                  fontSize: "0.9rem",
                                }}
                              >
                                {item.original_filename}
                              </strong>
                              <span style={{ color: "var(--text-secondary)", fontSize: "0.75rem" }}>
                                ID #{item.id} · {item.document_type || item.file_type || "Document"}
                              </span>
                            </div>
                          </div>
                        </td>

                        <td style={{ padding: "14px 16px" }}>
                          <Chip
                            label={item.department || "General"}
                            size="small"
                            sx={{
                              fontSize: "0.75rem",
                              fontWeight: 600,
                              bgcolor: "var(--surface-secondary)",
                              color: "var(--text-secondary)",
                            }}
                          />
                        </td>

                        <td style={{ padding: "14px 16px" }}>
                          <div>
                            <span style={{ color: "var(--text-primary)" }}>
                              {item.uploader_name || `User #${item.uploaded_by || "System"}`}
                            </span>
                            {isItemOwn && (
                              <Chip
                                label="Your Upload"
                                size="small"
                                sx={{
                                  ml: 1,
                                  height: 18,
                                  fontSize: "0.65rem",
                                  bgcolor: "var(--info-bg)",
                                  color: "var(--info)",
                                  fontWeight: 600,
                                }}
                              />
                            )}
                          </div>
                        </td>

                        <td style={{ padding: "14px 16px", color: "var(--text-secondary)" }}>
                          {item.uploaded_at || item.created_at
                            ? new Date(item.uploaded_at || item.created_at || "").toLocaleDateString(undefined, {
                                year: "numeric",
                                month: "short",
                                day: "numeric",
                              })
                            : "Recent"}
                        </td>

                        <td style={{ padding: "14px 16px" }}>
                          <Chip
                            label={formatConfidence(item.overall_confidence)}
                            size="small"
                            sx={{
                              fontWeight: 600,
                              fontSize: "0.75rem",
                              bgcolor:
                                confidenceNum !== null && confidenceNum >= 80
                                  ? "var(--success-bg)"
                                  : confidenceNum !== null && confidenceNum >= 60
                                  ? "var(--warning-bg)"
                                  : "var(--danger-bg)",
                              color:
                                confidenceNum !== null && confidenceNum >= 80
                                  ? "var(--success)"
                                  : confidenceNum !== null && confidenceNum >= 60
                                  ? "var(--warning)"
                                  : "var(--danger)",
                            }}
                          />
                        </td>

                        <td style={{ padding: "14px 16px" }}>
                          <Chip
                            label="Pending Review"
                            size="small"
                            sx={{
                              bgcolor: "var(--warning-bg)",
                              color: "var(--warning)",
                              fontWeight: 600,
                              fontSize: "0.75rem",
                            }}
                          />
                        </td>

                        <td style={{ padding: "14px 16px", textAlign: "right" }}>
                          <Button
                            variant="contained"
                            size="small"
                            onClick={() => setSelectedDocId(item.id)}
                            startIcon={<RateReviewIcon fontSize="small" />}
                            sx={{
                              textTransform: "none",
                              fontWeight: 600,
                              fontSize: "0.8rem",
                              borderRadius: "8px",
                              bgcolor: "var(--primary)",
                              "&:hover": { bgcolor: "var(--primary-hover)" },
                            }}
                          >
                            Review Document
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : activeDoc ? (
        /* ======================================================== */
        /* 2. DEDICATED DOCUMENT REVIEW VIEW                        */
        /* ======================================================== */
        <div className="dedicatedReviewView">
          {/* Top Review Bar */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "12px",
              padding: "12px 18px",
              marginBottom: "16px",
              flexWrap: "wrap",
              gap: "12px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={<ArrowBackIcon />}
                onClick={() => setSelectedDocId(null)}
                sx={{
                  textTransform: "none",
                  borderRadius: "8px",
                  borderColor: "var(--border)",
                  color: "var(--text-primary)",
                  "&:hover": { borderColor: "var(--primary)" },
                }}
              >
                Back to Queue
              </Button>

              <div>
                <strong style={{ fontSize: "1rem", color: "var(--text-primary)", display: "block" }}>
                  {activeDoc.original_filename}
                </strong>
                <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                  Document ID #{activeDoc.id} · {activeDoc.department || "General"} · {activeDoc.document_type || "General Document"}
                </span>
              </div>
            </div>

            {/* Verification Decision Actions */}
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <Button
                variant="outlined"
                color="error"
                size="small"
                startIcon={<CloseIcon />}
                onClick={() => setRejectDialogOpen(true)}
                disabled={actionLoading || isOwnDocument}
                title={isOwnDocument ? "Separation of duties: Cannot reject your own upload" : "Reject document"}
                sx={{ textTransform: "none", borderRadius: "8px", fontWeight: 600 }}
              >
                Reject
              </Button>

              <Button
                variant="outlined"
                color="warning"
                size="small"
                startIcon={<EditNoteIcon />}
                onClick={() => setRevisionDialogOpen(true)}
                disabled={actionLoading || isOwnDocument}
                title={isOwnDocument ? "Separation of duties: Cannot request revision on your own upload" : "Request revision"}
                sx={{ textTransform: "none", borderRadius: "8px", fontWeight: 600 }}
              >
                Request Revision
              </Button>

              <Button
                variant="contained"
                size="small"
                startIcon={<CheckIcon />}
                onClick={handleApprove}
                disabled={actionLoading || isOwnDocument}
                title={isOwnDocument ? "Separation of duties: Cannot approve your own upload" : "Approve document"}
                sx={{
                  textTransform: "none",
                  borderRadius: "8px",
                  fontWeight: 600,
                  bgcolor: "var(--primary)",
                  "&:hover": { bgcolor: "var(--primary-hover)" },
                }}
              >
                {actionLoading ? "Processing..." : "Approve Document"}
              </Button>
            </div>
          </div>

          {/* Separation of Duties Alert */}
          {isOwnDocument && (
            <Alert
              severity="warning"
              icon={<WarningAmberIcon />}
              sx={{
                mb: 2,
                borderRadius: "10px",
                fontSize: "0.85rem",
              }}
            >
              <strong>Separation of Duties Notice:</strong> You uploaded this document. As a required security safeguard, you cannot approve, reject, or request revisions on documents you submitted. Another authorized reviewer must perform the review.
            </Alert>
          )}

          {/* Workspace: Left (Preview) + Right (Extracted Text & Metadata Workspace) */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "20px",
              minHeight: "680px",
            }}
          >
            {/* ================= LEFT: ORIGINAL PREVIEW ================= */}
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              {/* Preview Bar */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "10px 16px",
                  borderBottom: "1px solid var(--border)",
                  background: "var(--surface-secondary)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <DescriptionOutlinedIcon fontSize="small" sx={{ color: "var(--primary)" }} />
                  <span style={{ fontWeight: 600, fontSize: "0.85rem", color: "var(--text-primary)" }}>
                    Original Document Preview
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                  <IconButton
                    size="small"
                    onClick={() => setZoom((z) => Math.max(50, z - 10))}
                    title="Zoom Out"
                  >
                    <ZoomOutIcon fontSize="small" />
                  </IconButton>
                  <span style={{ fontSize: "0.75rem", minWidth: "40px", textAlign: "center", color: "var(--text-secondary)" }}>
                    {zoom}%
                  </span>
                  <IconButton
                    size="small"
                    onClick={() => setZoom((z) => Math.min(200, z + 10))}
                    title="Zoom In"
                  >
                    <ZoomInIcon fontSize="small" />
                  </IconButton>
                  <IconButton
                    size="small"
                    onClick={() => setZoom(100)}
                    title="Reset Zoom"
                  >
                    <RestartAltIcon fontSize="small" />
                  </IconButton>
                </div>
              </div>

              {/* Preview Container */}
              <div
                style={{
                  flex: 1,
                  padding: "20px",
                  overflow: "auto",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  background: "var(--background)",
                }}
              >
                <div
                  style={{
                    transform: `scale(${zoom / 100})`,
                    transformOrigin: "top center",
                    width: "100%",
                    maxWidth: "650px",
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    borderRadius: "10px",
                    padding: "24px",
                    boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                    color: "var(--text-primary)",
                  }}
                >
                  <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: "12px", marginBottom: "16px" }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, fontSize: "1.1rem" }}>
                      {activeDoc.original_filename}
                    </Typography>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "8px" }}>
                      <Chip label={`Dept: ${activeDoc.department || "General"}`} size="small" />
                      <Chip label={`Format: ${activeDoc.file_type || "File"}`} size="small" variant="outlined" />
                      <Chip label={`Pages: ${activeDoc.pages?.length || 1}`} size="small" variant="outlined" />
                    </div>
                  </div>

                  <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "16px" }}>
                    Uploaded: {activeDoc.uploaded_at ? new Date(activeDoc.uploaded_at).toLocaleString() : "Recently"} by{" "}
                    <strong>{activeDoc.uploader_name || `User #${activeDoc.uploaded_by || "System"}`}</strong>
                  </div>

                  {/* Document preview pages / content rendering */}
                  <Box
                    sx={{
                      p: 2,
                      borderRadius: "8px",
                      background: "var(--surface-secondary)",
                      border: "1px solid var(--border)",
                      fontFamily: "monospace",
                      fontSize: "0.8rem",
                      lineHeight: 1.6,
                      maxHeight: "440px",
                      overflowY: "auto",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {activeDoc.ocr_results && activeDoc.ocr_results.length > 0 ? (
                      activeDoc.ocr_results.map((res, i) => (
                        <div key={res.id || i} style={{ marginBottom: "16px" }}>
                          <div style={{ fontSize: "0.75rem", fontWeight: 700, color: "var(--primary)", marginBottom: 4 }}>
                            --- PAGE {ocrPageNumber(res) || i + 1} ---
                          </div>
                          <div>{canonicalExtractedText(res) || "(Empty page)"}</div>
                        </div>
                      ))
                    ) : (
                      <div style={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
                        Document content preview is being rendered.
                      </div>
                    )}
                  </Box>
                </div>
              </div>
            </div>

            {/* ================= RIGHT: EXTRACTED CONTENT & CORRECTION WORKSPACE ================= */}
            <div
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "14px",
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
              }}
            >
              {/* Workspace Navigation Tabs */}
              <Box sx={{ borderBottom: 1, borderColor: "var(--border)", bgcolor: "var(--surface-secondary)" }}>
                <Tabs
                  value={detailTab}
                  onChange={(_, val) => setDetailTab(val)}
                  textColor="primary"
                  indicatorColor="primary"
                  sx={{ minHeight: "44px" }}
                >
                  <Tab
                    value="ocr"
                    icon={<TextFieldsIcon fontSize="small" />}
                    iconPosition="start"
                    label="OCR Text Editor"
                    sx={{ textTransform: "none", fontWeight: 600, fontSize: "0.85rem", minHeight: "44px" }}
                  />
                  <Tab
                    value="fields"
                    icon={<TableChartIcon fontSize="small" />}
                    iconPosition="start"
                    label={`Extracted Fields (${activeDoc.extracted_fields?.length || 0})`}
                    sx={{ textTransform: "none", fontWeight: 600, fontSize: "0.85rem", minHeight: "44px" }}
                  />
                  <Tab
                    value="info"
                    icon={<InfoOutlinedIcon fontSize="small" />}
                    iconPosition="start"
                    label="Warnings & Details"
                    sx={{ textTransform: "none", fontWeight: 600, fontSize: "0.85rem", minHeight: "44px" }}
                  />
                </Tabs>
              </Box>

              {/* TAB 1: OCR TEXT CORRECTION EDITOR */}
              {detailTab === "ocr" && (
                <div style={{ padding: "18px", flex: 1, overflowY: "auto" }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1, color: "var(--text-primary)" }}>
                    Page-by-Page OCR Text Verification
                  </Typography>
                  <Typography variant="body2" sx={{ color: "var(--text-secondary)", fontSize: "0.8rem", mb: 2 }}>
                    Review recognized OCR text. Authorised checkers can correct distorted characters or erroneous words while preserving the original recognition trail.
                  </Typography>

                  {activeDoc.ocr_results && activeDoc.ocr_results.length > 0 ? (
                    activeDoc.ocr_results.map((res) => {
                      const pageNum = ocrPageNumber(res);
                      const isEditing = Number(editingOcrId) === Number(res.id);
                      const isSaving = Number(savingOcrId) === Number(res.id);
                      const pageTables = (activeDoc.extracted_tables || []).filter(
                        (t) => pageNum != null && t.page_number === pageNum
                      );

                      return (
                        <div
                          key={res.id}
                          style={{
                            background: "var(--background)",
                            border: "1px solid var(--border)",
                            borderRadius: "10px",
                            padding: "14px",
                            marginBottom: "16px",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                            <div>
                              <strong style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>
                                Page {pageNum || 1}
                              </strong>
                              {res.confidence != null && (
                                <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)", marginLeft: "8px" }}>
                                  ({formatConfidence(res.confidence)} confidence)
                                </span>
                              )}
                            </div>

                            {!isEditing ? (
                              <Button
                                size="small"
                                variant="outlined"
                                onClick={() => setEditingOcrId(res.id)}
                                disabled={isOwnDocument}
                                sx={{ textTransform: "none", fontSize: "0.75rem", borderRadius: "6px" }}
                              >
                                Edit OCR Text
                              </Button>
                            ) : (
                              <div style={{ display: "flex", gap: "6px" }}>
                                <Button
                                  size="small"
                                  onClick={() => setEditingOcrId(null)}
                                  disabled={isSaving}
                                  sx={{ textTransform: "none", fontSize: "0.75rem", color: "var(--text-secondary)" }}
                                >
                                  Cancel
                                </Button>
                                <Button
                                  size="small"
                                  variant="contained"
                                  onClick={() => handleOcrTextSave(res.id)}
                                  disabled={isSaving}
                                  sx={{ textTransform: "none", fontSize: "0.75rem", borderRadius: "6px", bgcolor: "var(--primary)" }}
                                >
                                  {isSaving ? "Saving..." : "Save Corrections"}
                                </Button>
                              </div>
                            )}
                          </div>

                          {/* Render structured tables if present on this page */}
                          {pageTables.length > 0 && (
                            <Box sx={{ mb: 2 }}>
                              <PageStructuredTables tables={pageTables} />
                            </Box>
                          )}

                          {isEditing ? (
                            <TextField
                              fullWidth
                              multiline
                              minRows={6}
                              maxRows={14}
                              value={ocrTextValues[res.id] ?? ""}
                              onChange={(e) =>
                                setOcrTextValues((prev) => ({ ...prev, [res.id]: e.target.value }))
                              }
                              sx={{
                                "& .MuiInputBase-input": {
                                  fontFamily: "monospace",
                                  fontSize: "0.8rem",
                                  lineHeight: 1.5,
                                },
                              }}
                            />
                          ) : (
                            <div
                              style={{
                                fontFamily: "monospace",
                                fontSize: "0.8rem",
                                lineHeight: 1.6,
                                whiteSpace: "pre-wrap",
                                color: "var(--text-primary)",
                                background: "var(--surface)",
                                padding: "10px",
                                borderRadius: "6px",
                                border: "1px solid var(--border)",
                                maxHeight: "240px",
                                overflowY: "auto",
                              }}
                            >
                              {canonicalExtractedText(res) ||
                                "(No text recorded on this page)"}
                            </div>
                          )}

                          {originalOcrText(res) &&
                            originalOcrText(res) !== canonicalExtractedText(res) && (
                            <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", marginTop: "6px", fontStyle: "italic" }}>
                              Original OCR: {originalOcrText(res).slice(0, 100)}...
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <Typography variant="body2" sx={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
                      No OCR text extracted for this document.
                    </Typography>
                  )}
                </div>
              )}

              {/* TAB 2: EXTRACTED FIELDS & METADATA EDITOR */}
              {detailTab === "fields" && (
                <div style={{ padding: "18px", flex: 1, overflowY: "auto" }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1, color: "var(--text-primary)" }}>
                    AI-Extracted Metadata &amp; Key-Value Pairs
                  </Typography>
                  <Typography variant="body2" sx={{ color: "var(--text-secondary)", fontSize: "0.8rem", mb: 2 }}>
                    Authorised reviewers can inspect and correct extracted entities before final approval.
                  </Typography>

                  {activeDoc.extracted_fields && activeDoc.extracted_fields.length > 0 ? (
                    activeDoc.extracted_fields.map((field) => {
                      const currentVal =
                        fieldValues[field.id] !== undefined
                          ? fieldValues[field.id]
                          : field.field_value || "";
                      const isSaving = savingFieldId === field.id;
                      const isModified =
                        (field.original_value && field.original_value !== currentVal) ||
                        (field.corrected_value && field.corrected_value !== field.original_value);

                      return (
                        <div
                          key={field.id}
                          style={{
                            background: "var(--background)",
                            border: "1px solid var(--border)",
                            borderRadius: "10px",
                            padding: "12px 14px",
                            marginBottom: "12px",
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                            <strong style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>
                              {field.field_name}
                            </strong>
                            <Chip
                              label={formatConfidence(field.confidence)}
                              size="small"
                              sx={{
                                fontSize: "0.7rem",
                                height: 20,
                                fontWeight: 600,
                                bgcolor:
                                  field.confidence && field.confidence >= 80
                                    ? "var(--success-bg)"
                                    : "var(--warning-bg)",
                                color:
                                  field.confidence && field.confidence >= 80 ? "var(--success)" : "var(--warning)",
                              }}
                            />
                          </div>

                          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                            <TextField
                              size="small"
                              fullWidth
                              value={currentVal}
                              onChange={(e) =>
                                setFieldValues((prev) => ({ ...prev, [field.id]: e.target.value }))
                              }
                              sx={{ "& .MuiInputBase-input": { fontSize: "0.85rem" } }}
                            />
                            <IconButton
                              size="small"
                              onClick={() => handleFieldSave(field.id)}
                              disabled={isSaving || isOwnDocument}
                              title="Save field change"
                              sx={{
                                bgcolor: "var(--primary-light)",
                                color: "var(--primary)",
                                "&:hover": { bgcolor: "var(--primary-light)" },
                              }}
                            >
                              {isSaving ? <CircularProgress size={16} /> : <SaveIcon fontSize="small" />}
                            </IconButton>
                          </div>

                          {isModified && (
                            <div style={{ fontSize: "0.7rem", color: "var(--text-secondary)", marginTop: "4px" }}>
                              Original value: <span style={{ fontStyle: "italic" }}>{field.original_value}</span>
                            </div>
                          )}
                        </div>
                      );
                    })
                  ) : (
                    <Typography variant="body2" sx={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
                      No structured key-value fields detected on this document.
                    </Typography>
                  )}
                </div>
              )}

              {/* TAB 3: WARNINGS & AUDIT INFO */}
              {detailTab === "info" && (
                <div style={{ padding: "18px", flex: 1, overflowY: "auto" }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1, color: "var(--text-primary)" }}>
                    Document Warnings &amp; Quality Audit
                  </Typography>

                  {/* Quality Reasons */}
                  {reviewReasons.length > 0 ? (
                    <Alert severity="warning" sx={{ mb: 2, borderRadius: "10px" }}>
                      <strong>Auto-Flag Reasons:</strong>
                      <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                        {reviewReasons.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    </Alert>
                  ) : (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: "10px" }}>
                      No automated quality warnings flagged. Normal verification required.
                    </Alert>
                  )}

                  <Box sx={{ mt: 2, background: "var(--background)", p: 2, borderRadius: "10px", border: "1px solid var(--border)" }}>
                    <Typography variant="body2" sx={{ mb: 1 }}>
                      <strong>Overall Quality Score:</strong> {formatConfidence(activeDoc.overall_confidence)}
                    </Typography>
                    <Typography variant="body2" sx={{ mb: 1 }}>
                      <strong>Document Type:</strong> {activeDoc.document_type || "General Document"}
                    </Typography>
                    <Typography variant="body2" sx={{ mb: 1 }}>
                      <strong>Department:</strong> {activeDoc.department || "Unassigned"}
                    </Typography>
                    <Typography variant="body2" sx={{ mb: 1 }}>
                      <strong>Uploaded By:</strong> {activeDoc.uploader_name || `User #${activeDoc.uploaded_by || "System"}`}
                    </Typography>
                    <Typography variant="body2">
                      <strong>Submission Timestamp:</strong> {activeDoc.uploaded_at ? new Date(activeDoc.uploaded_at).toLocaleString() : "Recently"}
                    </Typography>
                  </Box>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* Reject Confirmation Dialog */}
      <Dialog
        open={rejectDialogOpen}
        onClose={() => setRejectDialogOpen(false)}
        slotProps={{
          paper: {
            sx: {
              background: "var(--surface)",
              color: "var(--text-primary)",
              borderRadius: "14px",
              minWidth: "400px",
              border: "1px solid var(--border)",
            },
          },
        }}
      >
        <DialogTitle sx={{ fontWeight: 700 }}>Reject Document #{activeDoc?.id}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: "var(--text-secondary)", mb: 2 }}>
            Provide a clear reason for rejecting this document. This decision will be logged to the Activity Log.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="Rejection Reason"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="e.g. Unreadable scan, missing signature, mismatched totals..."
          />
        </DialogContent>
        <DialogActions sx={{ p: 2, pt: 0 }}>
          <Button onClick={() => setRejectDialogOpen(false)} sx={{ color: "var(--text-secondary)" }}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleRejectConfirm}
            disabled={actionLoading}
            sx={{ fontWeight: 600, borderRadius: "8px" }}
          >
            {actionLoading ? "Rejecting..." : "Confirm Rejection"}
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
              background: "var(--surface)",
              color: "var(--text-primary)",
              borderRadius: "14px",
              minWidth: "400px",
              border: "1px solid var(--border)",
            },
          },
        }}
      >
        <DialogTitle sx={{ fontWeight: 700 }}>Request Revision for Document #{activeDoc?.id}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ color: "var(--text-secondary)", mb: 2 }}>
            Specify instructions for the uploader. The document will return to the Maker's workspace for revision.
          </Typography>
          <TextField
            autoFocus
            fullWidth
            multiline
            rows={3}
            size="small"
            label="Revision Instructions"
            value={revisionNotes}
            onChange={(e) => setRevisionNotes(e.target.value)}
            placeholder="e.g. Please re-scan page 2 with higher resolution..."
          />
        </DialogContent>
        <DialogActions sx={{ p: 2, pt: 0 }}>
          <Button onClick={() => setRevisionDialogOpen(false)} sx={{ color: "var(--text-secondary)" }}>
            Cancel
          </Button>
          <Button
            variant="contained"
            color="warning"
            onClick={handleRevisionConfirm}
            disabled={actionLoading}
            sx={{ fontWeight: 600, borderRadius: "8px" }}
          >
            {actionLoading ? "Submitting..." : "Send Revision Request"}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Feedback Snackbar */}
      <Snackbar
        open={Boolean(successMsg)}
        autoHideDuration={4000}
        onClose={() => setSuccessMsg(null)}
        message={successMsg}
      />
    </div>
  );
}
