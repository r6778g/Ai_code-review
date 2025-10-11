from fastapi import FastAPI, Form, HTTPException, Request
import os
import requests
import logging
import traceback
import re
import json
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from typing import Dict, List, Tuple
import numpy as np
from .model import query_openrouter_focused, get_file_language
import ast
import time
import jwt
from .auth import generate_jwt, get_installations, get_installation_token

# ==============================
# Environment & Logging
# ==============================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================
# FastAPI Setup
# ==============================
app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# GitHub App Setup (Initial)
# ==============================
APP_ID = os.getenv("GITHUB_APP_ID")
if not APP_ID:
    raise ValueError("GITHUB_APP_ID not found in environment variables")

# Generate a JWT to authenticate as the GitHub App
jwt_token = generate_jwt()

# Fetch installations and pick the first one
installations = get_installations(jwt_token)
if not installations:
    raise ValueError("No installations found for the GitHub App")

installation_id = installations[0]["id"]
logger.info(f"Initial installation_id: {installation_id}")

# Get initial installation token
GITHUB_TOKEN = get_installation_token(jwt_token, installation_id)
if not GITHUB_TOKEN:
    raise ValueError("Failed to retrieve GitHub installation token")

# Global GitHub headers
headers_github = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": f"GitHubApp/{APP_ID}"
}

# Globals to store last review context
last_patches = []
last_full_comment = ""


# ==============================
# Utility Functions
# ==============================
def str_to_dict(data_str):
    """Safely convert JSON or Python literal string to dict."""
    if isinstance(data_str, (dict, list)):
        return data_str
    try:
        return json.loads(data_str)
    except Exception:
        return ast.literal_eval(data_str)


def get_pr_commit_sha(owner: str, repo: str, pr_number: int) -> str:
    """Fetch the latest commit SHA for a PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    response = requests.get(url, headers=headers_github, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["head"]["sha"]

def find_file_path_in_pr(owner: str, repo: str, pr_number: int, filename: str) -> str:
    """
    Find and return the exact file path in the PR that matches a given filename.
    Example: filename='Profile.css' → returns 'frontend/src/Components/Profile.css'
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
    response = requests.get(url, headers=headers_github, timeout=60)
    response.raise_for_status()

    files = response.json()
    logger.info(f"🔍 Searching for file '{filename}' in PR #{pr_number}...")

    for f in files:
        file_path = f.get("filename", "")
        if file_path.endswith(filename):  # check if file matches
            logger.info(f"✅ Found path: {file_path}")
            return file_path

    logger.warning(f"⚠️ File '{filename}' not found in PR #{pr_number}")
    return None



def post_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    comments: List[Dict]
) -> bool:
    """
    Posts all PR review comments in a single review (payload outside loop).
    """


    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    success_all = True
    commit_id = get_pr_commit_sha(owner, repo, pr_number)
    logger.info(f"🔑 Using commit SHA: {commit_id}")

    formatted_comments = []

    for idx, c in enumerate(comments, start=1):
        try:
            # Auto-resolve 
            
            file_path = find_file_path_in_pr(owner, repo, pr_number, c["file"])

            formatted_comments.append({
                "path": file_path,
                "position": c["end_line"],
                "body": c["body"],
            })

        except KeyError as e:
            logger.error(f"⚠️ Missing key in comment #{idx}: {str(e)} → {c}")
            success_all = False
        except Exception as e:
            logger.error(f"⚠️ Exception while preparing comment #{idx}: {str(e)}")
            success_all = False

    # ✅ One single payload for all comments
    payload = {
        "commit_id": commit_id,
        "body": "🤖 Automated AI Code Review Summary",
        "event": "COMMENT",
        "comments": formatted_comments,
    }



    # ✅ Send all comments in one review
    response = requests.post(url, headers=headers_github, json=payload, timeout=60)

    if response.status_code == 201:
        logger.info(f"✅ Successfully posted {len(formatted_comments)} comments in one review.")
    else:
        logger.error(f"❌ Review failed: {response.status_code} - {response.text}")
        success_all = False

    return success_all
   
def post_comment_to_pr(owner: str, repo: str, pr_number: int, comments_str: List[dict]) -> bool:
    """Post a summary comment on the PR Conversation tab."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    success_all = True

    for idx, comment in enumerate(comments_str, start=1):
        try:
            payload = {"body": comment["body"]}
            response = requests.post(url, headers=headers_github, json=payload, timeout=30)

            if response.status_code == 201:
                logger.info(f"✅ Posted summary comment #{idx} to PR #{pr_number}")
            else:
                logger.error(f"❌ Failed comment #{idx}: {response.status_code} - {response.text}")
                success_all = False
        except Exception as e:
            logger.error(f"⚠️ Error posting comment #{idx}: {str(e)}")
            success_all = False
    return success_all


def should_review_file(filename: str, patch: str) -> bool:
    """Check if the file should be reviewed (based on type, size, and content)."""
    if not patch or patch == "No patch available":
        return False

    code_extensions = [
        '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.cpp', '.c', '.go',
        '.rs', '.php', '.rb', '.swift', '.kt', '.sql', '.css', '.scss',
        '.html', '.vue', '.sh'
    ]
    if not any(filename.lower().endswith(ext) for ext in code_extensions):
        return False

    if len(patch.split('\n')) > 1000:
        logger.info(f"Skipping {filename} - patch too large")
        return False

    meaningful_lines = [
        line for line in patch.split('\n')
        if (line.startswith('+') or line.startswith('-'))
        and line.strip()
        and not line.startswith('+++')
        and not line.startswith('---')
    ]
    if len(meaningful_lines) < 2:
        logger.info(f"Skipping {filename} - only formatting changes")
        return False

    return True


# ==============================
# Webhook Handler
# ==============================
@app.post("/")
async def github_webhook(request: Request):
    global GITHUB_TOKEN, headers_github, last_patches, last_full_comment

    try:
        payload = await request.json()
        if "pull_request" not in payload:
            return {"message": "Not a PR event"}

        action = payload.get("action")
        pr_data = payload.get("pull_request")
        if not pr_data:
            return {"message": "No PR data"}

        repo = payload["repository"]["name"]
        owner = payload["repository"]["owner"]["login"]
        installation_id = payload["installation"]["id"]
        pr_number = pr_data["number"]
        pr_title = pr_data.get("title", "")

        logger.info(f"Processing webhook for PR #{pr_number} ({repo})")

        if action in ["closed", "locked", "unlocked"]:
            return {"message": f"Action {action} ignored"}

        # Refresh token for this installation
        jwt_token = generate_jwt()
        GITHUB_TOKEN = get_installation_token(jwt_token, installation_id)

        headers_github = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"GitHubApp/{APP_ID}",
        }

        # Fetch changed files in PR
        files_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        response = requests.get(files_url, headers=headers_github, timeout=60)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch PR files")

        files = response.json()
        code_files_count = 0
        last_patches = []
        all_reviews = []

        for file in files:
            filename = file.get("filename", "Unknown")
            status = file.get("status", "Unknown")
            additions = file.get("additions", 0)
            deletions = file.get("deletions", 0)
            patch = file.get("patch", "")

            if not should_review_file(filename, patch):
                continue

            code_files_count += 1
            last_patches.append(patch)

            try:
                review_json = query_openrouter_focused(filename, patch, status)
                review_json = review_json.replace('\n', '')
                parsed_review = json.loads(review_json)
                all_reviews.append(parsed_review)
            except Exception as e:
                error_review = [{"body": f"Review error in `{filename}`: {str(e)}"}]
                all_reviews.append(error_review)

        success = False
        if all_reviews:
            for review in all_reviews:
                success = post_review_comments(owner, repo, pr_number, review)
                last_full_comment = review

        return {
            "message": "Webhook processed successfully",
            "repository": f"{owner}/{repo}",
            "pr_number": pr_number,
            "action": action,
            "files_total": len(files),
            "files_reviewed": code_files_count,
            "review_posted": bool(all_reviews),
            "success": success,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")


# ==============================
# Root Route (Health Check)
# ==============================
@app.get("/")
async def root():
    return {
        "message": "Enhanced Code Review API is running with OpenRouter",
        "version": "2.1"
    }
