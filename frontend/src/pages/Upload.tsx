import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import CloudUploadOutlinedIcon from "@mui/icons-material/CloudUploadOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import PictureAsPdfOutlinedIcon from "@mui/icons-material/PictureAsPdfOutlined";
import InsertDriveFileOutlinedIcon from "@mui/icons-material/InsertDriveFileOutlined";
import DeleteOutlineOutlinedIcon from "@mui/icons-material/DeleteOutlineOutlined";
import CheckCircleOutlineOutlinedIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import CloseOutlinedIcon from "@mui/icons-material/CloseOutlined";

import { renderAsync } from "docx-preview";

interface SelectedFile {
  name: string;
  size: number;
  type: string;
  file: File;
}

interface UploadedDocument {
  id: number;
  original_filename: string;
  stored_filename: string;
  file_path: string;
  file_type: string;
  file_size: number | null;
  document_type: string | null;
  status: string | null;
  uploaded_by: number | null;
  overall_confidence: number | null;
  uploaded_at: string | null;
  processed_at: string | null;
}

export default function Upload() {
  const [selectedFile, setSelectedFile] =
    useState<SelectedFile | null>(null);

  const [isDragging, setIsDragging] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const [isUploading, setIsUploading] = useState(false);
  const [uploadedDocument, setUploadedDocument] =
    useState<UploadedDocument | null>(null);

  const [uploadError, setUploadError] = useState("");

  const docxContainerRef =
    useRef<HTMLDivElement | null>(null);

  const handleFile = (file: File) => {
    setUploadError("");
    setUploadedDocument(null);

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
    setUploadedDocument(null);
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

  const getFileIcon = () => {
    if (!selectedFile) {
      return null;
    }

    if (isPdf) {
      return <PictureAsPdfOutlinedIcon />;
    }

    return <InsertDriveFileOutlinedIcon />;
  };

  /*
   * ============================
   * UPLOAD TO FASTAPI
   * ============================
   */

  const uploadDocument = async () => {
    if (!selectedFile || isUploading) {
      return;
    }

    setIsUploading(true);
    setUploadError("");
    setUploadedDocument(null);

    try {
      const formData = new FormData();

      formData.append(
        "file",
        selectedFile.file
      );

      const response = await fetch(
        "http://127.0.0.1:5000/api/documents/upload",
        {
          method: "POST",
          body: formData,
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data?.detail ||
            "Failed to upload document."
        );
      }

      setUploadedDocument(data);

    } catch (error) {
      console.error(
        "Upload error:",
        error
      );

      setUploadError(
        error instanceof Error
          ? error.message
          : "Something went wrong while uploading."
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
                <span>Uploading...</span>
              </div>
            ) : uploadedDocument ? (
              <div className="fileStatus uploaded">
                <CheckCircleOutlineOutlinedIcon />
                <span>Uploaded</span>
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

            {!uploadedDocument && (
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
            )}

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

      {/* ================= SUCCESS ================= */}

      {uploadedDocument && (
        <div className="uploadSuccess">
          <CheckCircleOutlineOutlinedIcon />

          <div>
            <strong>
              Document uploaded successfully
            </strong>

            <span>
              Document ID:{" "}
              {uploadedDocument.id}
            </span>
          </div>
        </div>
      )}

      {/* ================= INFO CARDS ================= */}

      <div className="uploadInfoGrid">

        <div className="uploadInfoCard">

          <h3>
            What happens next?
          </h3>

          <div className="uploadStep">
            <span>1</span>

            <div>
              <strong>Upload</strong>

              <p>
                Your document is securely
                added to the system.
              </p>
            </div>
          </div>

          <div className="uploadStep">
            <span>2</span>

            <div>
              <strong>Process</strong>

              <p>
                OCR and document processing
                prepare your content.
              </p>
            </div>
          </div>

          <div className="uploadStep">
            <span>3</span>

            <div>
              <strong>Analyze</strong>

              <p>
                Your document becomes
                searchable and ready for AI.
              </p>
            </div>
          </div>

        </div>

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