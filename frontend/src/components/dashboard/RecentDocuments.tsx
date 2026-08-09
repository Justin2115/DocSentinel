import StatusBadge from "./StatusBadge";

const documents = [
  {
    name: "Invoice_Q2_2024.pdf",
    date: "Jul 8, 2025",
    status: "Processed",
    confidence: "97%",
  },
  {
    name: "ID_Verification.jpg",
    date: "Jul 8, 2025",
    status: "Needs review",
    confidence: "63%",
  },
  {
    name: "Contract_NDA.pdf",
    date: "Jul 7, 2025",
    status: "Indexed",
    confidence: "--",
  },
];

export default function RecentDocuments() {
  return (
    <div className="recentDocs">

      <div className="tableHeader">

        <h2>Recent Documents</h2>

        <button>View all</button>

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

          {documents.map((doc) => (
            <tr key={doc.name}>

              <td>{doc.name}</td>

              <td>{doc.date}</td>

              <td>
                <StatusBadge
                  status={
                    doc.status as
                      | "Processed"
                      | "Needs review"
                      | "Indexed"
                  }
                />
              </td>

              <td>{doc.confidence}</td>

            </tr>
          ))}

        </tbody>

      </table>

    </div>
  );
}