import { createTheme } from "@mui/material/styles";

const theme = createTheme({
  palette: {
    mode: "light",

    primary: {
      main: "#0B7A5C",
    },

    secondary: {
      main: "#6366F1",
    },

    background: {
      default: "#F8F7F4",
      paper: "#FFFFFF",
    },

    success: {
      main: "#16A34A",
    },

    warning: {
      main: "#F97316",
    },

    text: {
      primary: "#111827",
      secondary: "#667085",
    },
  },

  shape: {
    borderRadius: 14,
  },

  typography: {
    fontFamily:
      "'Inter', 'Segoe UI', 'Roboto', 'Helvetica Neue', sans-serif",

    h3: {
      fontWeight: 700,
    },

    h4: {
      fontWeight: 700,
    },

    h5: {
      fontWeight: 600,
    },

    h6: {
      fontWeight: 600,
    },

    button: {
      textTransform: "none",
      fontWeight: 600,
    },
  },

  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          borderRadius: 16,
        },
      },
    },

    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          padding: "10px 18px",
        },
      },
    },

    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 18,
          boxShadow: "0px 4px 18px rgba(0,0,0,0.06)",
        },
      },
    },
  },
});

export default theme;