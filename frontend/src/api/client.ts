import axios from "axios";

export const isRequestCanceled = (error: unknown) => axios.isCancel(error);

const apiBase = (
	import.meta.env.VITE_API_BASE_URL as string | undefined
)?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export const API_BASE_URL = apiBase;

export const apiClient = axios.create({
	baseURL: `${apiBase}/api`,
});

// Automatically inject JWT Bearer token into outgoing requests
apiClient.interceptors.request.use((config) => {
	const token = localStorage.getItem("auth_token");
	if (token) {
		config.headers.Authorization = `Bearer ${token}`;
	}
	return config;
});

// Handle 401 Unauthorized responses globally
apiClient.interceptors.response.use(
	(response) => response,
	(error) => {
		if (error.response && error.response.status === 401) {
			// If not on login page, remove expired token and redirect
			if (window.location.pathname !== "/login" && window.location.pathname !== "/auth/callback") {
				localStorage.removeItem("auth_token");
				window.location.href = "/login?error=Session%20expired.%20Please%20log%20in%20again.";
			}
		}
		return Promise.reject(error);
	}
);

export default apiClient;
