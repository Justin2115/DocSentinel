import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import StatusBadge from "./StatusBadge";
import { getDashboardStats } from "../../api/document";

const formatDate = (date: string | null) => date ? new Date(date).toLocaleDateString() : "--";
const displayStatus = (status: string | null): "Processed" | "Needs review" | "Indexed" =>
  status === "needs_review" ? "Needs review" : status === "completed" ? "Processed" : "Indexed";

export default function RecentDocuments() {
  const [documents, setDocuments] = useState<Awaited<ReturnType<typeof getDashboardStats>>["data"]["recent_documents"]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    getDashboardStats().then((response) => setDocuments(response.data.recent_documents)).catch(() => setError(true));
  }, []);

  return (
    <div className="recentDocs">

      <div className="tableHeader">

        <h2>Recent Documents</h2>

        <Link to="/library">View all</Link>

      </div>

      <table>

        <thead>

          <tr>

            <th>Name</th>

            <th>Date</th>

            <th>Status</th>

            <th>Confidence</th>

          </tr>

        </thead>

        <tbody>

          {error ? <tr><td colSpan={4}>Unable to load recent documents.</td></tr> : documents.length === 0 ? <tr><td colSpan={4}>No documents uploaded yet.</td></tr> : documents.map((doc) => (
            <tr key={doc.id}>

              <td>{doc.original_filename}</td>

              <td>{formatDate(doc.uploaded_at)}</td>

              <td>
                <StatusBadge
                  status={
                    displayStatus(doc.status)
                  }
                />
              </td>

              <td>{doc.overall_confidence != null ? `${Number(doc.overall_confidence).toFixed(1)}%` : "--"}</td>

            </tr>
          ))}

        </tbody>

      </table>

    </div>
  );
}