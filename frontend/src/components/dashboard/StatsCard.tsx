import type { ReactNode } from "react";

interface Props {
  title: string;
  value: string;
  change: string;
  icon: ReactNode;
}

export default function StatsCard({
  title,
  value,
  change,
  icon,
}: Props) {
  return (
    <div className="statsCard">

      <div className="statsHeader">

        <span>{title}</span>

        <div className="statsIcon">
          {icon}
        </div>

      </div>

      <h2>{value}</h2>

      <p>{change}</p>

    </div>
  );
}