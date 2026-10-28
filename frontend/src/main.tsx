import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { AppThemeProvider } from "./context/ThemeContext";

import "./styles/globals.css";
import "./styles/variables.css";
import "./styles/typography.css";
import "./styles/design-system.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppThemeProvider>
      <App />
    </AppThemeProvider>
  </React.StrictMode>
);
