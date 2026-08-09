import { useMemo, useState } from "react";

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

type DocumentItem = {
  id: number;
  name: string;
  type: "PDF" | "DOCX" | "JPG" | "PNG" | "XLSX";
  date: string;
  status: "Processed" | "Needs review" | "Indexed";
  confidence: string;
  size: string;
};

const documents: DocumentItem[] = [
  {
    id: 1,
    name: "Invoice_Q2_2024.pdf",
    type: "PDF",
    date: "Jul 8, 2025",
    status: "Processed",
    confidence: "97%",
    size: "2.4 MB",
  },
  {
    id: 2,
    name: "ID_Verification.jpg",
    type: "JPG",
    date: "Jul 8, 2025",
    status: "Needs review",
    confidence: "63%",
    size: "1.0 MB",
  },
  {
    id: 3,
    name: "Contract_NDA.pdf",
    type: "PDF",
    date: "Jul 7, 2025",
    status: "Indexed",
    confidence: "--",
    size: "1.8 MB",
  },
  {
    id: 4,
    name: "Employee_Records.docx",
    type: "DOCX",
    date: "Jul 6, 2025",
    status: "Processed",
    confidence: "94%",
    size: "845 KB",
  },
  {
    id: 5,
    name: "Financial_Report.xlsx",
    type: "XLSX",
    date: "Jul 5, 2025",
    status: "Processed",
    confidence: "98%",
    size: "3.2 MB",
  },
  {
    id: 6,
    name: "Passport_Copy.png",
    type: "PNG",
    date: "Jul 4, 2025",
    status: "Needs review",
    confidence: "71%",
    size: "1.6 MB",
  },
];

const Library = () => {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("All types");
  const [statusFilter, setStatusFilter] = useState("All status");
  const [selectedDocument, setSelectedDocument] =
    useState<DocumentItem | null>(null);

  const filteredDocuments = useMemo(() => {
    return documents.filter((document) => {
      const matchesSearch = document.name
        .toLowerCase()
        .includes(search.toLowerCase());

      const matchesType =
        typeFilter === "All types" || document.type === typeFilter;

      const matchesStatus =
        statusFilter === "All status" || document.status === statusFilter;

      return matchesSearch && matchesType && matchesStatus;
    });
  }, [search, typeFilter, statusFilter]);

  const getFileIcon = (type: DocumentItem["type"]) => {
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
          <strong>{filteredDocuments.length}</strong>
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
          <span>{filteredDocuments.length} results</span>
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
              {filteredDocuments.length > 0 ? (
                filteredDocuments.map((document) => (
                  <tr key={document.id}>
                    <td>
                      <div className="documentName">
                        <div className="documentIcon">
                          {getFileIcon(document.type)}
                        </div>
                        <div>
                          <strong>{document.name}</strong>
                          <span>{document.size}</span>
                        </div>
                      </div>
                    </td>

                    <td>
                      <span className="fileType">{document.type}</span>
                    </td>

                    <td>{document.date}</td>

                    <td>
                      <span
                        className={`statusBadge ${document.status
                          .toLowerCase()
                          .replace(" ", "-")}`}
                      >
                        {document.status}
                      </span>
                    </td>

                    <td>
                      <span className="confidence">
                        {document.confidence}
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
            Showing {filteredDocuments.length} of {documents.length} documents
          </span>

          <div className="pagination">
            <button disabled>Previous</button>
            <button className="pageActive">1</button>
            <button>2</button>
            <button>3</button>
            <button>Next</button>
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
                <p>{selectedDocument.name}</p>
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
                {getFileIcon(selectedDocument.type)}
              </div>

              <h3>{selectedDocument.name}</h3>

              <p>
                {selectedDocument.type} · {selectedDocument.size}
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