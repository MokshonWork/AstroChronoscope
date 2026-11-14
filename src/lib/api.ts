const API_URL = import.meta.env.VITE_API_URL;

export interface HealthResponse {
    message: string;
}

export async function checkBackend(): Promise<HealthResponse> {
    const response = await fetch(`${API_URL}/`);

    if (!response.ok) {
        throw new Error(`API request failed: ${response.status}`);
    }

    return response.json();
}
