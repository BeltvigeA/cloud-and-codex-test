// API Client for PrintPro3D
const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8080/api';

/**
 * Make an authenticated API request
 */
const apiRequest = async (endpoint, options = {}) => {
  const token = localStorage.getItem('auth_token');

  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: 'Request failed' }));
    throw new Error(error.error || error.message || 'Request failed');
  }

  return response.json();
};

// Organization API
export const organizationAPI = {
  // Get all organizations for current user
  getAll: async () => {
    const response = await apiRequest('/organizations');
    return response.data;
  },

  // Get organization by ID
  findById: async (id) => {
    const response = await apiRequest(`/organizations/${id}`);
    return response.data;
  },

  // Create new organization
  create: async (data) => {
    const response = await apiRequest('/organizations', {
      method: 'POST',
      body: JSON.stringify(data),
    });
    return response.data;
  },

  // Update organization
  update: async (id, data) => {
    const response = await apiRequest(`/organizations/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
    return response.data;
  },

  // Regenerate API key
  regenerateApiKey: async (organizationId) => {
    const response = await apiRequest(`/organizations/${organizationId}/regenerate-api-key`, {
      method: 'POST',
    });
    return response.data;
  },
};

// User API
export const userAPI = {
  // Get current user
  me: async () => {
    const response = await apiRequest('/users/me');
    return response.data;
  },

  // Update user profile
  update: async (data) => {
    const response = await apiRequest('/users/me', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
    return response.data;
  },
};

// User Settings API
export const userSettingsAPI = {
  // Get user settings
  get: async () => {
    const response = await apiRequest('/user-settings');
    return response.data;
  },

  // Create user settings
  create: async (data) => {
    const response = await apiRequest('/user-settings', {
      method: 'POST',
      body: JSON.stringify(data),
    });
    return response.data;
  },

  // Update user settings
  update: async (id, data) => {
    const response = await apiRequest(`/user-settings/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
    return response.data;
  },
};

// Printer Status API
export const printerStatusAPI = {
  // Update printer status (used by printer clients with API key)
  update: async (data, apiKey) => {
    const response = await fetch(`${API_BASE_URL}/printer-status`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': apiKey,
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: 'Request failed' }));
      throw new Error(error.error || error.message || 'Request failed');
    }

    return response.json();
  },

  // Get printer status
  get: async (serial, apiKey) => {
    const response = await fetch(`${API_BASE_URL}/printer-status/${serial}`, {
      headers: {
        'X-API-Key': apiKey,
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: 'Request failed' }));
      throw new Error(error.error || error.message || 'Request failed');
    }

    return response.json();
  },
};

export default {
  organization: organizationAPI,
  user: userAPI,
  userSettings: userSettingsAPI,
  printerStatus: printerStatusAPI,
};
