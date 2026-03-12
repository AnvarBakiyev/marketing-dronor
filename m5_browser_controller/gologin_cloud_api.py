import os
import requests
import logging

logger = logging.getLogger(__name__)

# Cloud API by default, fallback to local
GOLOGIN_API_URL = os.environ.get('GOLOGIN_API_URL', 'https://api.gologin.com')
GOLOGIN_API_TOKEN = os.environ.get('GOLOGIN_API', '')

class GoLoginAPI:
    """GoLogin API wrapper - supports both cloud and local."""
    
    def __init__(self, base_url: str = None, token: str = None):
        self.base_url = base_url or GOLOGIN_API_URL
        self.token = token or GOLOGIN_API_TOKEN
        self.is_cloud = 'api.gologin.com' in self.base_url
        
    def _headers(self):
        if self.is_cloud and self.token:
            return {
                'Authorization': f'Bearer {self.token}',
                'Content-Type': 'application/json'
            }
        return {'Content-Type': 'application/json'}
    
    def is_running(self) -> bool:
        try:
            if self.is_cloud:
                r = requests.get(f'{self.base_url}/browser/v2', headers=self._headers(), timeout=5)
            else:
                r = requests.get(f'{self.base_url}/browser/v2', timeout=2)
            return r.status_code in [200, 401, 403]  # Cloud returns 401/403 if no profiles
        except:
            return False
    
    def get_profiles(self) -> list:
        """Get all profiles from GoLogin."""
        try:
            if self.is_cloud:
                r = requests.get(f'{self.base_url}/browser/v2', headers=self._headers(), timeout=10)
            else:
                r = requests.get(f'{self.base_url}/browser/v2', timeout=5)
            if r.status_code == 200:
                return r.json().get('profiles', [])
        except Exception as e:
            logger.error(f"GoLogin get_profiles error: {e}")
        return []
    
    def start_profile(self, profile_id: str) -> dict:
        """Start browser profile."""
        try:
            if self.is_cloud:
                r = requests.get(
                    f'{self.base_url}/browser/{profile_id}/start',
                    headers=self._headers(),
                    timeout=60
                )
            else:
                r = requests.get(f'{self.base_url}/browser/{profile_id}/start', timeout=30)
            return r.json()
        except Exception as e:
            logger.error(f"GoLogin start_profile error: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def stop_profile(self, profile_id: str) -> dict:
        """Stop browser profile."""
        try:
            if self.is_cloud:
                r = requests.get(
                    f'{self.base_url}/browser/{profile_id}/stop',
                    headers=self._headers(),
                    timeout=30
                )
            else:
                r = requests.get(f'{self.base_url}/browser/{profile_id}/stop', timeout=10)
            return r.json()
        except Exception as e:
            logger.error(f"GoLogin stop_profile error: {e}")
            return {'status': 'error', 'message': str(e)}

# For backward compatibility
def browser_controller(*args, **kwargs):
    logger.warning("browser_controller called - not implemented for cloud")
    return None
