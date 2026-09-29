import { useEffect, useState } from "react";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import CheckCircleOutlineOutlinedIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import ErrorOutlineOutlinedIcon from "@mui/icons-material/ErrorOutlineOutlined";
import TrendingUpOutlinedIcon from "@mui/icons-material/TrendingUpOutlined";

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
        change="From PostgreSQL"
        icon={<DescriptionOutlinedIcon />}
      />

      <StatsCard
        title="PROCESSED TODAY"
        value={stats ? stats.processed_today.toLocaleString() : "--"}
        change="Processed today"
        icon={<CheckCircleOutlineOutlinedIcon />}
      />

      <StatsCard
        title="PENDING REVIEW"
        value={stats ? stats.pending_review.toLocaleString() : "--"}
        change="Needs review"
        icon={<ErrorOutlineOutlinedIcon />}
      />

      <StatsCard
        title="AVG. CONFIDENCE"
        value={stats?.average_confidence != null ? `${stats.average_confidence.toFixed(1)}%` : "--"}
        change="Available OCR confidence"
        icon={<TrendingUpOutlinedIcon />}
      />

    </div>
  );
}