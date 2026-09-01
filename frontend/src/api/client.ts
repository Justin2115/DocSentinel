import axios from "axios";

const apiBase = (
	import.meta.env.VITE_API_BASE_URL as string | undefined
)?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export const API_BASE_URL = apiBase;

export const apiClient = axios.create({
	baseURL: `${apiBase}/api`,
});

export default apiClient;
