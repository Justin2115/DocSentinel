import React from "react";
import ReactDOM from "react-dom/client";

import { ThemeProvider } from "@mui/material";

import App from "./App";

import theme from "./styles/theme";

import "./styles/globals.css";
import "./styles/variables.css";
import "./styles/typography.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <App />
    </ThemeProvider>
  </React.StrictMode>
);