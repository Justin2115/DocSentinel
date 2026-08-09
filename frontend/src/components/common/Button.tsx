import Button from "@mui/material/Button";
import type { ReactNode } from "react";

interface CustomButtonProps {
  children: ReactNode;
  onClick?: () => void;
  type?: "button" | "submit" | "reset";
  variant?: "contained" | "outlined" | "text";
  fullWidth?: boolean;
  disabled?: boolean;
}

const CustomButton = ({
  children,
  onClick,
  type = "button",
  variant = "contained",
  fullWidth = false,
  disabled = false,
}: CustomButtonProps) => {
  return (
    <Button
      variant={variant}
      type={type}
      fullWidth={fullWidth}
      disabled={disabled}
      onClick={onClick}
      sx={{
        borderRadius: "12px",
        padding: "12px 20px",
        fontWeight: 600,
      }}
    >
      {children}
    </Button>
  );
};

export default CustomButton;