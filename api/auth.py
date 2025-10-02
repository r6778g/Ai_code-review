from dotenv import load_dotenv
import time
import os
import jwt
import requests



# 1. Your GitHub App ID
APP_ID = os.getenv("GITHUB_APP_ID")
algorithm1=os.getenv("algorithm_name")

# 2. Path to your private key (downloaded when creating the GitHub App)
PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY").replace("\\n", "\n")

def generate_jwt():
    """Generate a JWT for GitHub App authentication"""
    
    private_key = PRIVATE_KEY 

    now = int(time.time())
    payload = {
        "iat": now,               # issued at
        "exp": now + (10 * 60),   # expires after 10 minutes
        "iss": APP_ID             # GitHub App ID
    }

    encoded_jwt = jwt.encode(payload, private_key, algorithm=algorithm1)
    return encoded_jwt

def get_installations(jwt_token):
    """Fetch installations for this GitHub App"""
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    url = "https://api.github.com/app/installations"
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

def get_installation_token(jwt_token, installation_id):
    """Exchange JWT for an installation access token"""
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    response = requests.post(url, headers=headers)
    response.raise_for_status()
    return response.json()["token"]

