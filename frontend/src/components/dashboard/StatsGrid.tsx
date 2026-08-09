import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import CheckCircleOutlineOutlinedIcon from "@mui/icons-material/CheckCircleOutlineOutlined";
import ErrorOutlineOutlinedIcon from "@mui/icons-material/ErrorOutlineOutlined";
import TrendingUpOutlinedIcon from "@mui/icons-material/TrendingUpOutlined";

import StatsCard from "./StatsCard";

export default function StatsGrid() {
  return (
    <div className="statsGrid">

      <StatsCard
        title="TOTAL DOCUMENTS"
        value="2,847"
        change="+12% this week"
        icon={<DescriptionOutlinedIcon />}
      />

      <StatsCard
        title="PROCESSED TODAY"
        value="143"
        change="+8 this week"
        icon={<CheckCircleOutlineOutlinedIcon />}
      />

      <StatsCard
        title="PENDING REVIEW"
        value="29"
        change="-3 this week"
        icon={<ErrorOutlineOutlinedIcon />}
      />

      <StatsCard
        title="AVG. CONFIDENCE"
        value="91.4%"
        change="+0.6% this week"
        icon={<TrendingUpOutlinedIcon />}
      />

    </div>
  );
}