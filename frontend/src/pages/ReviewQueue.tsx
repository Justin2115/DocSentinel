import { useState } from "react";

import ZoomInIcon from "@mui/icons-material/ZoomIn";
import ZoomOutIcon from "@mui/icons-material/ZoomOut";
import DownloadOutlinedIcon from "@mui/icons-material/DownloadOutlined";
import FullscreenOutlinedIcon from "@mui/icons-material/FullscreenOutlined";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";

type DocumentType = "PDF" | "JPG" | "DOCX";

interface ReviewDocument {
  name: string;
  type: DocumentType;
  confidence: number;
  pages: number;
}

interface ExtractedField {
  label: string;
  value: string;
  confidence: number;
}

const reviewDocuments: ReviewDocument[] = [
  {
    name: "ID_Verification_Ramirez.jpg",
    type: "JPG",
    confidence: 63,
    pages: 1,
  },
  {
    name: "Medical_Claim_07072025.pdf",
    type: "PDF",
    confidence: 71,
    pages: 18,
  },
  {
    name: "Contract_NDA_ACME.pdf",
    type: "PDF",
    confidence: 68,
    pages: 12,
  },
  {
    name: "Employee_Record.docx",
    type: "DOCX",
    confidence: 74,
    pages: 6,
  },
];

const extractedFields: ExtractedField[] = [
  {
    label: "Full name",
    value: "Carlos Ramírez",
    confidence: 82,
  },
  {
    label: "Date of birth",
    value: "1987-03-14",
    confidence: 91,
  },
  {
    label: "ID number",
    value: "A7-3291-04",
    confidence: 76,
  },
  {
    label: "Expiry date",
    value: "2029-08-31",
    confidence: 48,
  },
];

export default function ReviewQueue() {
  const [selectedDocument, setSelectedDocument] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [threshold, setThreshold] = useState(85);

  const document = reviewDocuments[selectedDocument];

  const visibleDocuments = reviewDocuments.filter(
    (item) => item.confidence < threshold
  );

  const handleDocumentChange = (index: number) => {
    setSelectedDocument(index);
    setCurrentPage(1);
    setZoom(100);
  };

  const nextPage = () => {
    if (currentPage < document.pages) {
      setCurrentPage((page) => page + 1);
    }
  };

  const previousPage = () => {
    if (currentPage > 1) {
      setCurrentPage((page) => page - 1);
    }
  };

  const zoomIn = () => {
    setZoom((value) => Math.min(value + 10, 200));
  };

  const zoomOut = () => {
    setZoom((value) => Math.max(value - 10, 50));
  };

  return (
    <div className="reviewPage">

      {/* ================= HEADER ================= */}

      <div className="reviewHeader">

        <div>
          <h1>Review queue</h1>

          <p>
            Documents below the confidence threshold require
            manual verification.
          </p>
        </div>

        <div className="reviewThreshold">

          <span>Show items below:</span>

          <input
            type="range"
            min="50"
            max="100"
            value={threshold}
            onChange={(event) =>
              setThreshold(Number(event.target.value))
            }
          />

          <strong>{threshold}%</strong>

        </div>

      </div>


      {/* ================= WORKSPACE ================= */}

      <div className="reviewWorkspace">

        {/* ================= LEFT ================= */}

        <aside className="reviewList">

          <div className="reviewListHeader">

            <div>
              <h2>Documents</h2>
              <span>
                {visibleDocuments.length} needing review
              </span>
            </div>

          </div>

          <div className="reviewListItems">

            {visibleDocuments.length === 0 && (
              <div className="emptyReviewList">
                <p>No documents require review.</p>
              </div>
            )}

            {visibleDocuments.map((item) => {

              const originalIndex =
                reviewDocuments.findIndex(
                  (documentItem) =>
                    documentItem.name === item.name
                );

              return (
                <button
                  key={item.name}
                  type="button"
                  className={
                    selectedDocument === originalIndex
                      ? "reviewListItem active"
                      : "reviewListItem"
                  }
                  onClick={() =>
                    handleDocumentChange(originalIndex)
                  }
                >

                  <div className="reviewListItemTop">

                    <div className="reviewDocumentIcon">
                      <DescriptionOutlinedIcon />
                    </div>

                    <div className="reviewDocumentName">
                      <strong>{item.name}</strong>
                      <span>{item.type} document</span>
                    </div>

                  </div>

                  <div className="reviewListItemBottom">

                    <span>
                      {item.pages}{" "}
                      {item.pages === 1 ? "page" : "pages"}
                    </span>

                    <span className="reviewConfidence">
                      {item.confidence}%
                    </span>

                  </div>

                </button>
              );
            })}

          </div>

        </aside>


        {/* ================= CENTER + RIGHT ================= */}

        <section className="reviewDetail">

          <div className="reviewWorkspaceInner">

            {/* ================= DOCUMENT VIEWER ================= */}

            <div className="documentViewer">

              <div className="documentViewerHeader">

                <div className="documentViewerTitle">

                  <strong>{document.name}</strong>

                  <span>
                    {document.type} document
                  </span>

                </div>

                <div className="documentViewerControls">

                  <button
                    type="button"
                    onClick={zoomOut}
                    title="Zoom out"
                  >
                    <ZoomOutIcon />
                  </button>

                  <span className="zoomValue">
                    {zoom}%
                  </span>

                  <button
                    type="button"
                    onClick={zoomIn}
                    title="Zoom in"
                  >
                    <ZoomInIcon />
                  </button>

                  <button
                    type="button"
                    title="Fullscreen"
                  >
                    <FullscreenOutlinedIcon />
                  </button>

                  <button
                    type="button"
                    title="Download"
                  >
                    <DownloadOutlinedIcon />
                  </button>

                </div>

              </div>


              {/* ================= DOCUMENT ================= */}

              <div className="documentPreview">

                <div
                  className={`documentMock documentMock${document.type}`}
                  style={{
                    transform: `scale(${zoom / 100})`,
                  }}
                >

                  {document.type === "JPG" && (
                    <>
                      <div className="mockDocumentHeader">
                        INDIAN GOVERNMENT
                      </div>

                      <div className="mockDocumentBody">

                        <div className="mockPhoto">
                          PHOTO
                        </div>

                        <div className="mockDocumentDetails">

                          <span>NAME</span>
                          <strong>
                            Carlos Ramírez
                          </strong>

                          <span>DATE OF BIRTH</span>
                          <strong>
                            14/03/1987
                          </strong>

                          <span>ID NUMBER</span>
                          <strong>
                            A7-3291-04
                          </strong>

                          <span>EXPIRY DATE</span>
                          <strong>
                            31/08/2029
                          </strong>

                        </div>

                      </div>
                    </>
                  )}

                  {document.type === "PDF" && (
                    <>
                      <div className="mockPdfHeader">
                        DOCUMENT
                      </div>

                      <div className="mockPdfContent">

                        <h3>
                          Medical Claim Document
                        </h3>

                        <div className="mockTextLine long" />
                        <div className="mockTextLine" />
                        <div className="mockTextLine medium" />

                        <div className="mockPdfTable">

                          <div>
                            <span>Patient Name</span>
                            <strong>
                              Carlos Ramírez
                            </strong>
                          </div>

                          <div>
                            <span>Claim Amount</span>
                            <strong>
                              ₹1,25,000
                            </strong>
                          </div>

                          <div>
                            <span>Hospital</span>
                            <strong>
                              Apollo Hospitals
                            </strong>
                          </div>

                          <div>
                            <span>Discharge Date</span>
                            <strong>
                              14/07/2026
                            </strong>
                          </div>

                        </div>

                        <div className="mockTextLine long" />
                        <div className="mockTextLine long" />
                        <div className="mockTextLine medium" />

                      </div>
                    </>
                  )}

                  {document.type === "DOCX" && (
                    <>
                      <div className="mockDocxHeader">
                        Employee Record
                      </div>

                      <div className="mockDocxContent">

                        <h3>
                          Employee Information
                        </h3>

                        <p>
                          This document contains
                          employee information and
                          employment records.
                        </p>

                        <div className="mockTextLine long" />
                        <div className="mockTextLine" />
                        <div className="mockTextLine medium" />

                        <div className="mockDocxSection">
                          <strong>
                            Employee Name
                          </strong>

                          <span>
                            Carlos Ramírez
                          </span>
                        </div>

                        <div className="mockDocxSection">
                          <strong>
                            Employee ID
                          </strong>

                          <span>
                            EMP-20391
                          </span>
                        </div>

                      </div>
                    </>
                  )}

                </div>

              </div>


              {/* ================= PAGE NAVIGATION ================= */}

              <div className="documentViewerFooter">

                <button
                  type="button"
                  onClick={previousPage}
                  disabled={currentPage === 1}
                >
                  <ChevronLeftIcon />
                </button>

                <span>
                  Page{" "}
                  <strong>{currentPage}</strong>
                  {" "}of{" "}
                  <strong>{document.pages}</strong>
                </span>

                <button
                  type="button"
                  onClick={nextPage}
                  disabled={
                    currentPage === document.pages
                  }
                >
                  <ChevronRightIcon />
                </button>

              </div>

            </div>


            {/* ================= RIGHT PANEL ================= */}

            <aside className="extractedPanel">

              <div className="extractedPanelHeader">

                <div>
                  <h2>Extracted fields</h2>

                  <p>
                    AI-extracted information from
                    this document.
                  </p>
                </div>

                <div className="overallConfidence">

                  <span>
                    Overall confidence
                  </span>

                  <strong>
                    {document.confidence}%
                  </strong>

                </div>

              </div>


              <div className="extractedFields">

                {extractedFields.map((field) => (

                  <div
                    className="extractedField"
                    key={field.label}
                  >

                    <div className="extractedFieldHeader">

                      <label>
                        {field.label}
                      </label>

                      <span
                        className={
                          field.confidence < 70
                            ? "fieldConfidence low"
                            : "fieldConfidence"
                        }
                      >
                        {field.confidence}%
                      </span>

                    </div>

                    <input
                      type="text"
                      defaultValue={field.value}
                    />

                    <div className="fieldConfidenceBar">

                      <div
                        className="fieldConfidenceFill"
                        style={{
                          width: `${field.confidence}%`,
                        }}
                      />

                    </div>

                  </div>

                ))}

              </div>


              {/* ================= REVIEW ACTIONS ================= */}

              <div className="reviewActions">

                <button
                  type="button"
                  className="rejectButton"
                >
                  <CloseIcon />
                  Reject
                </button>

                <button
                  type="button"
                  className="approveButton"
                >
                  <CheckIcon />
                  Approve
                </button>

              </div>

            </aside>

          </div>


          {/* ================= BOTTOM ACTIONS ================= */}

          <div className="reviewBottomActions">

            <button
              type="button"
              className="saveCorrectionsButton"
            >
              Save corrections
            </button>

            <button
              type="button"
              className="approveAllButton"
            >
              Approve all
            </button>

          </div>

        </section>

      </div>

    </div>
  );
}