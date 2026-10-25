import { useEffect, useState } from "react";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import CheckCircleOutlineOutlinedIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import ErrorOutlineOutlinedIcon from "@mui/icons-material/ErrorOutlineOutlined";

import StatsCard from "./StatsCard";
import { getDashboardStats } from "../../api/document";

export default function StatsGrid() {
  const [stats, setStats] = useState<Awaited<ReturnType<typeof getDashboardStats>>["data"] | null>(null);

  useEffect(() => {
    getDashboardStats().then((response) => setStats(response.data)).catch(() => setStats(null));
  }, []);

  return (
    <div className="statsGrid">

      <StatsCard
        title="TOTAL DOCUMENTS"
        value={stats ? stats.total_documents.toLocaleString() : "--"}
        change="Role-scoped library count"
        icon={<DescriptionOutlinedIcon />}
      />

      <StatsCard
        title="PROCESSED TODAY"
        value={stats ? stats.processed_today.toLocaleString() : "--"}
        change="Completed processing"
        icon={<CheckCircleOutlineOutlinedIcon />}
      />

      <StatsCard
        title="PENDING REVIEW"
        value={stats ? stats.pending_review.toLocaleString() : "--"}
        change="Awaiting checker verification"
        icon={<ErrorOutlineOutlinedIcon />}
      />

    </div>
  );
}
