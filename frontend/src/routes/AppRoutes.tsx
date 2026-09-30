import { Navigate, Route, Routes } from "react-router-dom";

import Admin from "../pages/Admin";
import AskQuestion from "../pages/AskQuestion";
import AuthCallback from "../pages/AuthCallback";
import Dashboard from "../pages/Dashboard";
import Library from "../pages/Library";
import Login from "../pages/Login";
import NotFound from "../pages/NotFound";
import Profile from "../pages/Profile";
import ReviewQueue from "../pages/ReviewQueue";
import Settings from "../pages/Settings";
import Upload from "../pages/Upload";

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
	);
};

export default AppRoutes;