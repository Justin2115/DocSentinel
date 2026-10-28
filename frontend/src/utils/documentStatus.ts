export type DashboardStatusLabel =
	| "Uploaded"
	| "Processing"
	| "Needs review"
	| "Needs revision"
	| "Approved"
	| "Rejected"
	| "Processed"
	| "Indexed"
	| "Unknown";

export const dashboardStatusLabel = (
	status: string | null
): DashboardStatusLabel => {
	const normalized = status?.trim().toLowerCase();
	switch (normalized) {
		case "uploaded":
			return "Uploaded";
		case "processing":
			return "Processing";
		case "pending_review":
		case "needs_review":
			return "Needs review";
		case "needs_revision":
			return "Needs revision";
		case "approved":
			return "Approved";
		case "rejected":
			return "Rejected";
		case "completed":
		case "processed":
			return "Processed";
		case "indexed":
			return "Indexed";
		default:
			return "Unknown";
	}
};
