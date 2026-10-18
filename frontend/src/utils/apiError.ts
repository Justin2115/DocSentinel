import { API_BASE_URL } from "../api/client";
import type { AxiosError } from "axios";

type FastApiValidationError = {
	msg?: string;
};

export function formatApiError(error: unknown, fallback = "Request failed."): string {
	const err = error as AxiosError<{ detail?: string | FastApiValidationError[] }>;
	if (!err.response) {
		return `Cannot reach the API at ${API_BASE_URL}. Start the backend (uvicorn on port 8000).`;
	}

	const detail = err.response.data?.detail;
	if (typeof detail === "string") {
		return detail;
	}
	if (Array.isArray(detail)) {
		return detail.map((item) => item.msg || "Validation error").join(" ");
	}

	if (err.response.status === 422) {
		return "Login request was invalid. Enter both email and password, then try again.";
	}
	if (err.response.status === 401) {
		return "Invalid email or password.";
	}
	if (err.response.status === 403) {
		return "Access denied: administrator account required.";
	}

	return fallback;
}
