interface StatusBadgeProps {
  status: "Processed" | "Needs review" | "Indexed";
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const colors = {
    Processed: "#DDF4EC",
    "Needs review": "#FFE7DD",
    Indexed: "#ECE7FF",
  };

  const text = {
    Processed: "#0B7A5C",
    "Needs review": "#F97316",
    Indexed: "#6366F1",
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