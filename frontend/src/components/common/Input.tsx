import TextField from "@mui/material/TextField";
import type { OutlinedTextFieldProps } from "@mui/material/TextField";

export type InputProps = Omit<OutlinedTextFieldProps, "variant"> & {
  label: string;
  type?: string;
  variant?: "outlined" | "filled" | "standard";
  InputProps?: any;
};

const Input = ({
  label,
  type = "text",
  variant = "outlined",
  InputProps,
  slotProps,
  ...props
}: InputProps) => {
  const mergedSlotProps = {
    ...slotProps,
    ...(InputProps ? { input: InputProps } : {}),
  };

  return (
    <TextField
      fullWidth
      variant={variant as any}
      label={label}
      type={type}
      margin="normal"
      slotProps={mergedSlotProps}
      {...props}
    />
  );
};

export default Input;