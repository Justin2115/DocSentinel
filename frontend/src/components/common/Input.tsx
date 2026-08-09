import TextField from "@mui/material/TextField";

interface InputProps {
  label: string;
  type?: string;
}

const Input = ({ label, type = "text" }: InputProps) => {
  return (
    <TextField
      fullWidth
      variant="outlined"
      label={label}
      type={type}
      margin="normal"
    />
  );
};

export default Input;