interface StatusBadgeProps {
  status: "Processed" | "Needs review" | "Indexed" | "Failed";
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const colors = {
    Processed: "#DDF4EC",
    "Needs review": "#FFE7DD",
    Indexed: "#ECE7FF",
    Failed: "#FEE2E2",
  };

  const text = {
    Processed: "#0B7A5C",
    "Needs review": "#F97316",
    Indexed: "#6366F1",
    Failed: "#B91C1C",
  };

  return (
    <span
      style={{
        background: colors[status],
        color: text[status],
        padding: "6px 14px",
        borderRadius: "999px",
        fontWeight: 600,
        fontSize: "14px",
      }}
    >
      {status}
    </span>
  );
}
