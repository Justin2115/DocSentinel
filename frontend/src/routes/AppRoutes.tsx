import { Navigate, Route, Routes } from "react-router-dom";

import AskQuestion from "../pages/AskQuestion";
import Dashboard from "../pages/Dashboard";
import Library from "../pages/Library";
import Login from "../pages/Login";
import NotFound from "../pages/NotFound";
import ReviewQueue from "../pages/ReviewQueue";
import Settings from "../pages/Settings";
import Upload from "../pages/Upload";

import PageLayout from "../components/layout/PageLayout";

const AppRoutes = () => {
  return (
    <Routes>
      <Route
        path="/"
        element={<Navigate to="/login" replace />}
      />

      <Route
        path="/login"
        element={<Login />}
      />

      <Route
        path="/dashboard"
        element={
          <PageLayout>
            <Dashboard />
          </PageLayout>
        }
      />

      <Route
        path="/upload"
        element={
          <PageLayout>
            <Upload />
          </PageLayout>
        }
      />

      <Route
        path="/library"
        element={
          <PageLayout>
            <Library />
          </PageLayout>
        }
      />

      <Route
        path="/ask"
        element={
          <PageLayout>
            <AskQuestion />
          </PageLayout>
        }
      />

      <Route
        path="/review"
        element={
          <PageLayout>
            <ReviewQueue />
          </PageLayout>
        }
      />

      <Route
        path="/settings"
        element={
          <PageLayout>
            <Settings />
          </PageLayout>
        }
      />

      <Route
        path="*"
        element={<NotFound />}
      />
    </Routes>
  );
};

export default AppRoutes;