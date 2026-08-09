import MuiCard from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import type { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
}

const Card = ({ children }: CardProps) => {
  return (
    <MuiCard elevation={0}>
      <CardContent>{children}</CardContent>
    </MuiCard>
  );
};

export default Card;