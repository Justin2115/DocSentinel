import { useEffect, useState } from "react";

import SearchIcon from "@mui/icons-material/Search";
import FilterListIcon from "@mui/icons-material/FilterList";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import ImageIcon from "@mui/icons-material/Image";
import DescriptionIcon from "@mui/icons-material/Description";
import TableChartIcon from "@mui/icons-material/TableChart";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import CloseIcon from "@mui/icons-material/Close";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";
import { deleteDocument, getDocuments } from "../api/document";
import type { DocumentRecord } from "../api/document";

type DocumentItem = DocumentRecord;
type DisplayType = "PDF" | "DOCX" | "JPG" | "PNG" | "XLSX" | "DOC" | "XLS";

const displayType = (document: DocumentItem): DisplayType => {
  const extension = document.original_filename.split(".").pop()?.toUpperCase() || "DOC";
  return ["PDF", "DOCX", "JPG", "PNG", "XLSX", "DOC", "XLS"].includes(extension)
    ? extension as DisplayType
    : "DOC";
};
const displayStatus = (status: string | null): "Processed" | "Needs review" | "Indexed" =>
  status === "needs_review" ? "Needs review" : status === "completed" ? "Processed" : "Indexed";
const formatSize = (bytes: number | null) => bytes == null ? "--" : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
const formatDate = (date: string | null) => date ? new Date(date).toLocaleDateString() : "--";

const Library = () => {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("All types");
  const [statusFilter, setStatusFilter] = useState("All status");
  const [selectedDocument, setSelectedDocument] =
    useState<DocumentItem | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const pageSize = 10;

  useEffect(() => {
    setLoading(true);
    setError("");
    const fileType = typeFilter === "All types" ? undefined : `.${typeFilter.toLowerCase()}`;
    const status = statusFilter === "All status" ? undefined : statusFilter === "Processed" ? "completed" : statusFilter === "Needs review" ? "needs_review" : "uploaded";
    getDocuments({ search: search || undefined, file_type: fileType, status, skip: (page - 1) * pageSize, limit: pageSize })
      .then((response) => { setDocuments(response.data.items); setTotal(response.data.total); })
      .catch(() => setError("Unable to load documents from the server."))
      .finally(() => setLoading(false));
  }, [search, typeFilter, statusFilter, page]);

  const removeDocument = async (id: number) => {
    setDeletingId(id);
    try { await deleteDocument(id); setDocuments((current) => current.filter((document) => document.id !== id)); setTotal((current) => current - 1); setSelectedDocument(null); }
    catch { setError("Unable to delete this document."); }
    finally { setDeletingId(null); }
  };

  const getFileIcon = (type: DisplayType) => {
    switch (type) {
      case "PDF":
        return <PictureAsPdfIcon />;
      case "JPG":
      case "PNG":
        return <ImageIcon />;
      case "XLSX":
        return <TableChartIcon />;
      default:
        return <DescriptionIcon />;
    }
  };

  return (
    <div className="libraryPage">
      <div className="libraryHeader">
        <div>
          <h1>Document Library</h1>
          <p>Manage, search and review all your documents.</p>
        </div>

        <div className="libraryCount">
          <strong>{total}</strong>
          <span>documents</span>
        </div>
      </div>

      <div className="libraryToolbar">
        <div className="librarySearch">
          <SearchIcon />
          <input
            type="text"
            placeholder="Search documents..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <div className="filterGroup">
          <div className="filterSelect">
            <FilterListIcon />
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option>All types</option>
              <option>PDF</option>
              <option>DOCX</option>
              <option>JPG</option>
              <option>PNG</option>
              <option>XLSX</option>
            </select>
            <KeyboardArrowDownIcon />
          </div>

          <div className="filterSelect">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option>All status</option>
              <option>Processed</option>
              <option>Needs review</option>
              <option>Indexed</option>
            </select>
            <KeyboardArrowDownIcon />
          </div>
        </div>
      </div>

      <div className="libraryTableCard">
        <div className="libraryTableHeader">
          <h2>All Documents</h2>
          <span>{total} results</span>
        </div>

        <div className="libraryTableWrapper">
          <table className="libraryTable">
            <thead>
              <tr>
                <th>NAME</th>
                <th>TYPE</th>
                <th>DATE</th>
                <th>STATUS</th>
                <th>CONFIDENCE</th>
                <th>ACTIONS</th>
              </tr>
            </thead>

            <tbody>
              {loading ? (
                <tr><td colSpan={6}>Loading documents...</td></tr>
              ) : error ? (
                <tr><td colSpan={6}>{error}</td></tr>
              ) : documents.length > 0 ? (
                documents.map((document) => (
                  <tr key={document.id}>
                    <td>
                      <div className="documentName">
                        <div className="documentIcon">
                          {getFileIcon(displayType(document))}
                        </div>
                        <div>
                          <strong>{document.original_filename}</strong>
                          <span>{formatSize(document.file_size)}</span>
                        </div>
                      </div>
                    </td>

                    <td>
                      <span className="fileType">{displayType(document)}</span>
                    </td>

                    <td>{formatDate(document.uploaded_at)}</td>

                    <td>
                      <span
                        className={`statusBadge ${displayStatus(document.status)
                          .toLowerCase()
                          .replace(" ", "-")}`}
                      >
                        {displayStatus(document.status)}
                      </span>
                    </td>

                    <td>
                      <span className="confidence">
                        {document.overall_confidence != null ? `${Number(document.overall_confidence).toFixed(1)}%` : "--"}
                      </span>
                    </td>

                    <td>
                      <div className="documentActions">
                        <button
                          className="iconAction"
                          title="Preview"
                          onClick={() => setSelectedDocument(document)}
                        >
                          <VisibilityOutlinedIcon />
                        </button>

                        <button
                          className="iconAction deleteAction"
                          title="Delete"
                          disabled={deletingId === document.id}
                          onClick={() => removeDocument(document.id)}
                        >
                         <DeleteIcon />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6}>
                    <div className="emptyLibrary">
                      <SearchIcon />
                      <h3>No documents found</h3>
                      <p>Try changing your search or filters.</p>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="libraryFooter">
            <span>
            Showing {documents.length} of {total} documents
          </span>

          <div className="pagination">
            <button disabled={page === 1} onClick={() => setPage((current) => current - 1)}>Previous</button>
            <button className="pageActive">{page}</button>
            <button disabled={page * pageSize >= total} onClick={() => setPage((current) => current + 1)}>Next</button>
          </div>
        </div>
      </div>

      {selectedDocument && (
        <div
          className="previewOverlay"
          onClick={() => setSelectedDocument(null)}
        >
          <div
            className="previewModal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="previewHeader">
              <div>
                <h2>Document Preview</h2>
                <p>{selectedDocument.original_filename}</p>
              </div>

              <button
                className="previewClose"
                onClick={() => setSelectedDocument(null)}
              >
                <CloseIcon />
              </button>
            </div>

            <div className="previewContent">
              <div className="previewFileIcon">
                {getFileIcon(displayType(selectedDocument))}
              </div>

              <h3>{selectedDocument.original_filename}</h3>

              <p>
                {displayType(selectedDocument)} · {formatSize(selectedDocument.file_size)}
              </p>

              <div className="previewPlaceholder">
                <DescriptionIcon />
                <span>Document preview will appear here</span>
              </div>
            </div>

            <div className="previewFooter">
              <button
                className="previewCancel"
                onClick={() => setSelectedDocument(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Library;