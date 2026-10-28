import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import NotFound from "../pages/NotFound";
const Admin = lazy(() => import("../pages/Admin"));
const AskQuestion = lazy(() => import("../pages/AskQuestion"));
const AuthCallback = lazy(() => import("../pages/AuthCallback"));
const Dashboard = lazy(() => import("../pages/Dashboard"));
const Library = lazy(() => import("../pages/Library"));
const Login = lazy(() => import("../pages/Login"));
const Profile = lazy(() => import("../pages/Profile"));
const ReviewQueue = lazy(() => import("../pages/ReviewQueue"));
const Settings = lazy(() => import("../pages/Settings"));
const Upload = lazy(() => import("../pages/Upload"));

import PageLayout from "../components/layout/PageLayout";
import { AdminRoute, CheckerRoute, MakerRoute, ProtectedRoute } from "./RouteGuards";
import { useAuth } from "../context/AuthContext";


const RootRedirect = () => {
	const { isAuthenticated, isLoading } = useAuth();
	if (isLoading) {
		return null;
	}
	return <Navigate to={isAuthenticated ? "/dashboard" : "/login"} replace />;
};

const AppRoutes = () => {
	return (
		<Suspense fallback={<div className="routeLoading" role="status" aria-live="polite">Loading page…</div>}>
		<Routes>
			<Route path="/" element={<RootRedirect />} />
			<Route path="/login" element={<Login />} />
			<Route path="/auth/callback" element={<AuthCallback />} />

			{/* Protected User Routes */}
			<Route
				path="/dashboard"
				element={
					<ProtectedRoute>
						<PageLayout>
							<Dashboard />
						</PageLayout>
					</ProtectedRoute>
				}
			/>

			<Route
				path="/upload"
				element={
					<MakerRoute>
						<PageLayout>
							<Upload />
						</PageLayout>
					</MakerRoute>
				}
			/>

			<Route
				path="/library"
				element={
					<ProtectedRoute>
						<PageLayout>
							<Library />
						</PageLayout>
					</ProtectedRoute>
				}
			/>

			<Route
				path="/ask"
				element={
					<ProtectedRoute>
						<PageLayout>
							<AskQuestion />
						</PageLayout>
					</ProtectedRoute>
				}
			/>

			<Route
				path="/review"
				element={
					<CheckerRoute>
						<PageLayout>
							<ReviewQueue />
						</PageLayout>
					</CheckerRoute>
				}
			/>


			<Route
				path="/settings"
				element={
					<AdminRoute>
						<PageLayout>
							<Settings />
						</PageLayout>
					</AdminRoute>
				}
			/>

			<Route
				path="/profile"
				element={
					<ProtectedRoute>
						<PageLayout>
							<Profile />
						</PageLayout>
					</ProtectedRoute>
				}
			/>

			{/* Admin Only Route */}
			<Route
				path="/admin"
				element={
					<AdminRoute>
						<PageLayout>
							<Admin />
						</PageLayout>
					</AdminRoute>
				}
			/>

			<Route path="*" element={<NotFound />} />
		</Routes>
		</Suspense>
	);
};

export default AppRoutes;
