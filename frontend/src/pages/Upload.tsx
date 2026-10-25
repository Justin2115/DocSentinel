import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined";
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined";
import ErrorOutlineOutlinedIcon from "@mui/icons-material/ErrorOutlineOutlined";
import CheckCircleOutlineOutlinedIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import CloseOutlinedIcon from "@mui/icons-material/CloseOutlined";
import RefreshOutlinedIcon from "@mui/icons-material/RefreshOutlined";
import { LinearProgress } from "@mui/material";

import { renderAsync } from "docx-preview";

import {
  getDocumentProcessing,
  retryDocumentProcessing,
  uploadDocument as uploadDocumentRequest,
} from "../api/document";
import type {
  DocumentProcessing,
} from "../api/document";
import { requestStatus } from "../api/client";
import { useAuth } from "../context/AuthContext";

interface SelectedFile {
  name: string;
  size: number;
  type: string;
  file: File;
}

export type DepartmentType = "auto" | "HR" | "FINANCE" | "LEGAL" | "OPERATIONS" | "IT";

export interface TrackedUpload {
  id: number;
  filename: string;
  department?: string | null;
  document_type?: string | null;
  uploaded_at: string;
}

const SESSION_STORAGE_KEY = "docsentinel_session_uploads";

const loadSessionUploads = (): TrackedUpload[] => {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
};

const saveSessionUploads = (items: TrackedUpload[]) => {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(items));
  } catch {
    // SessionStorage error handling
  }
};

const POLL_INTERVAL_MS = 2500;

const isCancelled = (error: unknown) =>
  (error as { code?: string } | null)?.code === "ERR_CANCELED";

const DEPARTMENTS: {
  value: DepartmentType;
  label: string;
}[] = [
  {
    value: "auto",
    label: "Auto Detect (AI Classification)",
  },
  {
    value: "HR",
    label: "Human Resources (HR)",
  },
  {
    value: "FINANCE",
    label: "Finance & Accounting",
  },
  {
    value: "LEGAL",
    label: "Legal & Compliance",
  },
  {
    value: "OPERATIONS",
    label: "Operations",
  },
  {
    value: "IT",
    label: "Information Technology (IT)",
  },
];

export default function Upload() {
  const { canReview, isSuperAdmin } = useAuth();

  const [selectedFile, setSelectedFile] =
    useState<SelectedFile | null>(null);

  const [isDragging, setIsDragging] =
    useState(false);

  const [showPreview, setShowPreview] =
    useState(false);

  const [isUploading, setIsUploading] =
    useState(false);

  // Bytes transferred, reported by the browser, never simulated.
  const [uploadPercent, setUploadPercent] =
    useState(0);

  // Tracked uploads persisting across navigation and refresh in the active session
  const [trackedUploads, setTrackedUploads] =
    useState<TrackedUpload[]>(loadSessionUploads);

  // Live pipeline state for each tracked document ID
  const [processingMap, setProcessingMap] =
    useState<Record<number, DocumentProcessing>>({});

  const [statusErrors, setStatusErrors] =
    useState<Record<number, string>>({});

  const [retryingId, setRetryingId] =
    useState<number | null>(null);

  const [uploadError, setUploadError] =
    useState("");

  // Target department selection (optional, default auto-detection)
  const [selectedDepartment, setSelectedDepartment] =
    useState<DepartmentType>("auto");

  const docxContainerRef =
    useRef<HTMLDivElement | null>(null);

  const handleFile = (file: File) => {
    setUploadError("");
    setUploadPercent(0);

    setSelectedFile({
      name: file.name,
      size: file.size,
      type: file.type,
      file,
    });
  };

  const handleFileInput = (
    event: ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];

    if (file) {
      handleFile(file);
    }
  };

  const handleDrop = (
    event: DragEvent<HTMLDivElement>
  ) => {
    event.preventDefault();
    setIsDragging(false);

    const file = event.dataTransfer.files?.[0];

    if (file) {
      handleFile(file);
    }
  };

  const removeFile = () => {
    if (isUploading) {
      return;
    }

    setSelectedFile(null);
    setShowPreview(false);
    setUploadPercent(0);
    setUploadError("");
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }

    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const isImage =
    selectedFile?.type.startsWith("image/") ?? false;

  const isPdf =
    selectedFile?.type === "application/pdf";

  const isDocx =
    selectedFile?.type ===
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    selectedFile?.name
      .toLowerCase()
      .endsWith(".docx");

  const previewUrl = selectedFile
    ? URL.createObjectURL(selectedFile.file)
    : "";

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  useEffect(() => {
    if (
      showPreview &&
      isDocx &&
      selectedFile &&
      docxContainerRef.current
    ) {
      docxContainerRef.current.innerHTML = "";

      renderAsync(
        selectedFile.file,
        docxContainerRef.current
      );
    }
  }, [showPreview, isDocx, selectedFile]);

  const describeFailure = (error: unknown, action: string) => {
    const status = requestStatus(error);

    if (status === undefined) {
      return `${action}: the server could not be reached.`;
    }
    if (status === 401) {
      return `${action}: your session has expired.`;
    }
    if (status === 403) {
      return `${action}: your role is not allowed to do this.`;
    }
    if (status >= 500) {
      return `${action}: the server reported an error (HTTP ${status}).`;
    }
    return `${action}: rejected with HTTP ${status}.`;
  };

  // Follow the pipeline for every tracked upload in the active session.
  useEffect(() => {
    if (trackedUploads.length === 0) {
      return;
    }

    const controller = new AbortController();
    let timer: number | undefined;
    let stopped = false;

    const pollAll = async () => {
      if (stopped) {
        return;
      }

      // Check which uploads are still in progress
      const unsettledUploads = trackedUploads.filter((u) => {
        const proc = processingMap[u.id];
        return !proc || (proc.stage !== "done" && proc.stage !== "failed");
      });

      if (unsettledUploads.length === 0) {
        return;
      }

      await Promise.all(
        unsettledUploads.map(async (u) => {
          try {
            const response = await getDocumentProcessing(
              u.id,
              controller.signal
            );
            if (stopped) return;
            setProcessingMap((prev) => ({
              ...prev,
              [u.id]: response.data,
            }));
            setStatusErrors((prev) => {
              if (prev[u.id]) {
                const next = { ...prev };
                delete next[u.id];
                return next;
              }
              return prev;
            });
          } catch (error) {
            if (stopped || isCancelled(error)) {
              return;
            }
            setStatusErrors((prev) => ({
              ...prev,
              [u.id]: describeFailure(error, "Could not read processing status"),
            }));
          }
        })
      );

      if (!stopped) {
        timer = window.setTimeout(pollAll, POLL_INTERVAL_MS);
      }
    };

    timer = window.setTimeout(pollAll, 600);

    return () => {
      stopped = true;
      controller.abort();
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [trackedUploads, processingMap]);

  const retryProcessing = async (docId: number) => {
    if (retryingId !== null) {
      return;
    }

    setRetryingId(docId);
    setStatusErrors((prev) => {
      const next = { ...prev };
      delete next[docId];
      return next;
    });

    try {
      await retryDocumentProcessing(docId);
      // Clear previous processing state to trigger polling
      setProcessingMap((prev) => {
        const next = { ...prev };
        delete next[docId];
        return next;
      });
    } catch (error) {
      setStatusErrors((prev) => ({
        ...prev,
        [docId]: describeFailure(error, "Retry failed"),
      }));
    } finally {
      setRetryingId(null);
    }
  };

  const dismissUpload = (docId: number) => {
    setTrackedUploads((prev) => {
      const next = prev.filter((item) => item.id !== docId);
      saveSessionUploads(next);
      return next;
    });
    setProcessingMap((prev) => {
      const next = { ...prev };
      delete next[docId];
      return next;
    });
    setStatusErrors((prev) => {
      const next = { ...prev };
      delete next[docId];
      return next;
    });
  };

  const getFileIcon = () => {
    if (!selectedFile) {
      return null;
    }

    if (isPdf) {
      return <PictureAsPdfOutlinedIcon />;
    }

    return <InsertDriveFileOutlinedIcon />;
  };

  /* ============================
   * UPLOAD TO FASTAPI
   * ============================ */

  const uploadDocument = async () => {
    if (!selectedFile || isUploading) {
      return;
    }

    setIsUploading(true);
    setUploadPercent(0);
    setUploadError("");

    try {
      const response = await uploadDocumentRequest(
        selectedFile.file,
        (isSuperAdmin && selectedDepartment !== "auto") ? selectedDepartment : undefined,
        undefined,
        setUploadPercent
      );

      const doc = response.data;
      const newTracked: TrackedUpload = {
        id: doc.id,
        filename: doc.original_filename || selectedFile.name,
        department: doc.department,
        document_type: doc.document_type,
        uploaded_at: new Date().toISOString(),
      };

      setTrackedUploads((prev) => {
        const next = [newTracked, ...prev.filter((p) => p.id !== newTracked.id)];
        saveSessionUploads(next);
        return next;
      });

      setSelectedFile(null);
      setShowPreview(false);
    } catch (error) {
      console.error(
        "Upload error:",
        error
      );

      const message = (
        error as {
          response?: {
            data?: {
              detail?: string;
            };
          };
        }
      ).response?.data?.detail;

      setUploadError(
        message ||
          (error instanceof Error
            ? error.message
            : "Something went wrong while uploading.")
      );
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="uploadPage">

      {/* ================= HEADER ================= */}

      <div className="uploadHeader">
        <div>
          <h1>Upload Documents</h1>

          <p>
            Add documents to your library for
            processing and analysis.
          </p>
        </div>
      </div>

      {/* ================= DROPZONE ================= */}

      <div
        className={`uploadDropzone ${
          isDragging
            ? "uploadDropzoneDragging"
            : ""
        }`}
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() =>
          setIsDragging(false)
        }
        onDrop={handleDrop}
      >
        <div className="uploadIcon">
          <CloudUploadOutlinedIcon />
        </div>

        <h2>
          Drag & drop your files here
        </h2>

        <p>or</p>

        <label className="browseButton">
          Browse files

          <input
            type="file"
            hidden
            accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.xlsx,.xls"
            onChange={handleFileInput}
          />
        </label>

        <span className="uploadHint">
          PDF, DOC, DOCX, XLS, XLSX, JPG or PNG
          <br />
          Maximum file size: 25 MB
        </span>

        {/* ================= DEPARTMENT CATEGORIZATION (SUPER ADMIN ONLY) ================= */}

        {isSuperAdmin && (
          <div
            className="departmentSelector ocrLanguageSelector"
            onClick={(event) =>
              event.stopPropagation()
            }
          >
            <label
              htmlFor="doc-department"
              className="ocrLanguageLabel"
            >
              Target Department (Optional)
            </label>

            <select
              id="doc-department"
              className="ocrLanguageSelect"
              value={selectedDepartment}
              onChange={(event) =>
                setSelectedDepartment(
                  event.target.value as DepartmentType
                )
              }
              disabled={isUploading}
            >
              {DEPARTMENTS.map((dept) => (
                <option
                  key={dept.value}
                  value={dept.value}
                >
                  {dept.label}
                </option>
              ))}
            </select>

            <span className="ocrLanguageHint">
              Select a target department or let AI classify automatically. Language is detected automatically.
            </span>
          </div>
        )}
      </div>

      {/* ================= SELECTED FILE ================= */}

      {selectedFile && (
        <div className="selectedFileCard">

          <div className="selectedFileInfo">

            <div className="fileIcon">
              {getFileIcon()}
            </div>

            <div className="fileDetails">
              <h3>
                {selectedFile.name}
              </h3>

              <span>
                {formatFileSize(
                  selectedFile.size
                )}
              </span>
            </div>

          </div>

          <div className="fileActions">

            {/* STATUS */}

            {isUploading ? (
              <div className="fileStatus uploading">
                <span className="uploadSpinner" />
                <span>
                  {uploadPercent > 0
                    ? `Uploading ${uploadPercent}%`
                    : "Uploading..."}
                </span>
              </div>
            ) : (
              <div className="fileStatus">
                <CheckCircleOutlineOutlinedIcon />
                <span>Ready</span>
              </div>
            )}

            {/* PREVIEW */}

            <button
              type="button"
              className="previewButton"
              onClick={() =>
                setShowPreview(true)
              }
              disabled={isUploading}
            >
              <VisibilityOutlinedIcon />
              Preview
            </button>

            {/* UPLOAD */}

            <button
              type="button"
              className="browseButton uploadActionButton"
              onClick={uploadDocument}
              disabled={isUploading}
            >
              <CloudUploadOutlinedIcon />

              {isUploading
                ? "Uploading..."
                : "Upload document"}
            </button>

            {/* REMOVE */}

            <button
              type="button"
              className="removeFileButton"
              onClick={removeFile}
              disabled={isUploading}
              aria-label="Remove file"
            >
              <DeleteOutlineOutlinedIcon />
            </button>

          </div>
        </div>
      )}

      {/* ================= TRANSFER PROGRESS ================= */}

      {isUploading && (
        <div
          role="progressbar"
          aria-label="File upload progress"
          aria-valuenow={uploadPercent}
          aria-valuemin={0}
          aria-valuemax={100}
          style={{ padding: "10px 0 4px" }}
        >
          <LinearProgress
            variant={uploadPercent > 0 ? "determinate" : "indeterminate"}
            value={uploadPercent}
            sx={{
              height: 8,
              borderRadius: 4,
              bgcolor: "rgba(255,255,255,0.08)",
              "& .MuiLinearProgress-bar": {
                bgcolor: "var(--accent, #a855f7)",
              },
            }}
          />
        </div>
      )}

      {/* ================= ERROR ================= */}

      {uploadError && (
        <div className="uploadError">
          <strong>
            Upload failed
          </strong>

          <span>
            {uploadError}
          </span>
        </div>
      )}

      {/* ================= PROCESSING STATUS ================= */}

      {trackedUploads.length > 0 && (
        <div style={{ display: "grid", gap: 14, margin: "20px 0" }}>
          {trackedUploads.map((upload) => {
            const proc = processingMap[upload.id];
            const statusErr = statusErrors[upload.id];

            let title = "Uploaded — starting processing";
            let message = "The file is stored. Reading its status now.";
            let cardClass = "uploadProcessing";

            if (proc) {
              if (proc.stage === "failed" || proc.status === "failed") {
                title = "Processing failed";
                message = proc.error || proc.message || "Processing failed.";
                cardClass = "uploadError";
              } else if (proc.stage === "done") {
                if (proc.status === "PENDING_REVIEW") {
                  title = "Needs review";
                  message = proc.message || "Finished with warnings and is waiting for review.";
                  cardClass = "uploadWarning";
                } else if (proc.pages_failed && proc.pages_failed > 0) {
                  title = "Partially processed";
                  message = proc.message || "Document was partially processed.";
                  cardClass = "uploadWarning";
                } else {
                  title = proc.searchable ? "Indexed" : "Processed";
                  message = proc.message || "Processing finished successfully.";
                  cardClass = "uploadSuccess";
                }
              } else {
                const stageMap: Record<string, string> = {
                  waiting: "Queued for processing",
                  reading_text: "Reading text (OCR)",
                  extracting_fields: "Extracting fields and classifying",
                  making_searchable: "Building the search index",
                };
                title = stageMap[proc.stage] || "Processing";
                message = proc.message || "Processing document...";
                cardClass = "uploadProcessing";
              }
            }

            return (
              <div
                key={upload.id}
                className={cardClass}
                aria-live="polite"
                aria-atomic="true"
              >
                {cardClass === "uploadError" ? (
                  <ErrorOutlineOutlinedIcon sx={{ mt: 0.25 }} />
                ) : (
                  <CheckCircleOutlineOutlinedIcon sx={{ mt: 0.25 }} />
                )}

                <div style={{ display: "grid", gap: 6, width: "100%" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <strong>{title}</strong>
                    <span style={{ fontSize: "0.85rem", opacity: 0.8, fontFamily: "monospace" }}>
                      ID: {upload.id}
                    </span>
                  </div>

                  <span>{message}</span>

                  <span style={{ fontSize: "0.88rem", opacity: 0.9 }}>
                    {upload.filename}
                    {upload.department ? ` · Department: ${upload.department}` : ""}
                    {upload.document_type ? ` · Type: ${upload.document_type}` : ""}
                    {proc?.overall_confidence !== null && proc?.overall_confidence !== undefined && proc.stage === "done"
                      ? ` · Confidence: ${proc.overall_confidence}%`
                      : ""}
                  </span>

                  {statusErr && (
                    <span style={{ color: "#fca5a5" }}>{statusErr}</span>
                  )}

                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 4 }}>
                    {proc?.stage === "failed" && canReview && (
                      <button
                        type="button"
                        className="previewButton"
                        onClick={() => retryProcessing(upload.id)}
                        disabled={retryingId === upload.id}
                      >
                        <RefreshOutlinedIcon />
                        {retryingId === upload.id ? "Retrying..." : "Retry processing"}
                      </button>
                    )}
                    {proc?.stage === "done" && (
                      <button
                        type="button"
                        className="previewButton"
                        onClick={() => dismissUpload(upload.id)}
                        title="Dismiss status"
                      >
                        <CloseOutlinedIcon sx={{ fontSize: 16 }} />
                        Dismiss
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ================= INFO CARDS ================= */}

      <div className="uploadInfoGrid">



        <div className="uploadInfoCard supportedCard">

          <h3>
            Supported documents
          </h3>

          <div className="supportedItem">
            <PictureAsPdfOutlinedIcon />

            <span>
              PDF documents
            </span>
          </div>

          <div className="supportedItem">
            <DescriptionOutlinedIcon />

            <span>
              Word documents
            </span>
          </div>

          <div className="supportedItem">
            <InsertDriveFileOutlinedIcon />

            <span>
              Images & spreadsheets
            </span>
          </div>

        </div>

      </div>

      {/* ================= PREVIEW MODAL ================= */}

      {showPreview && selectedFile && (
        <div
          className="previewOverlay"
          onClick={() =>
            setShowPreview(false)
          }
        >

          <div
            className="previewModal"
            onClick={(event) =>
              event.stopPropagation()
            }
          >

            <div className="previewHeader">

              <div>
                <h2>
                  Preview
                </h2>

                <span>
                  {selectedFile.name}
                </span>
              </div>

              <button
                type="button"
                className="previewClose"
                onClick={() =>
                  setShowPreview(false)
                }
                aria-label="Close preview"
              >
                <CloseOutlinedIcon />
              </button>

            </div>

            <div className="previewContent">

              {isImage && (
                <img
                  src={previewUrl}
                  alt={selectedFile.name}
                  className="imagePreview"
                />
              )}

              {isPdf && (
                <iframe
                  src={previewUrl}
                  title="PDF preview"
                  className="pdfPreview"
                />
              )}

              {isDocx && (
                <div
                  ref={docxContainerRef}
                  className="docxPreview"
                />
              )}

              {!isImage &&
                !isPdf &&
                !isDocx && (
                  <div className="unsupportedPreview">

                    <InsertDriveFileOutlinedIcon />

                    <h3>
                      Preview unavailable
                    </h3>

                    <p>
                      This file type can be
                      uploaded, but a browser
                      preview is not available yet.
                    </p>

                  </div>
                )}

            </div>

          </div>

        </div>
      )}

    </div>
  );
}