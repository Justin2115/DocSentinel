import type { DashboardStatusLabel } from "../../utils/documentStatus";

interface StatusBadgeProps {
	status: DashboardStatusLabel;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
	const stateClass = status.toLowerCase().replaceAll(" ", "-");

  return (
    <span
			className={`dashboardStatusBadge status-${stateClass}`}
    >
      {status}
    </span>
  );
}
