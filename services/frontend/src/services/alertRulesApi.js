const API_BASE_URL = 'http://localhost:8000';

export async function getAllAlertRules(detectionType = null) {
  try {
    const url = detectionType
      ? `${API_BASE_URL}/api/alert-rules?detection_type=${detectionType}`
      : `${API_BASE_URL}/api/alert-rules`;

    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Error fetching alert rules:', error);
    return [];
  }
}

export async function getAlertRule(ruleId) {
  try {
    const response = await fetch(`${API_BASE_URL}/api/alert-rules/${ruleId}`);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    return await response.json();
  } catch (error) {
    console.error('Error fetching alert rule:', error);
    return null;
  }
}