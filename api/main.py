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


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI()

# Globals to store last review context
last_patches = []          # list of patches
last_full_comment = ""     # combined AI review

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# GitHub token setup

GITHUB_TOKEN = "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"
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is required")
GITHUB_TOKEN = GITHUB_TOKEN.strip()

headers_github = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "GitHub-Webhook-Handler"
}

def str_to_dict(data_str):
    if isinstance(data_str, (dict, list)):
        return data_str  # already parsed
    try:
        return json.loads(data_str)   # JSON
    except Exception:
        return ast.literal_eval(data_str)  # Python literal


def get_pr_commit_sha(owner: str, repo: str, pr_number: int) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    resp = requests.get(url, headers=headers_github, timeout=30)
    resp.raise_for_status()
    pr_data = resp.json()
    return pr_data["head"]["sha"]

def post_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    comments: List[Dict],
) -> bool:
 
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    success_all = True
    commit_id = get_pr_commit_sha(owner, repo, pr_number)
    for idx, c in enumerate(comments, start=1):
        try:
            payload = {
                "body": c["body"],
                "commit_id": commit_id,
                "path": c["file"],            # e.g., "style.css"
                "line": c["end_line"],        # line in the diff (new code)
                "side": "RIGHT"               # comment on new code
            }

            response = requests.post(url, headers=headers_github, json=payload, timeout=60)

            if response.status_code == 201:
                logger.info(f" Posted inline comment #{idx} on {c['file']} line {c['end_line']}")
            else:
                logger.error(f" Failed comment #{idx}: {response.status_code} - {response.text}")
                success_all = False
        except Exception as e:
            logger.error(f"⚠️ Error posting comment #{idx}: {str(e)}")
            success_all = False

    return success_all        
def post_comment_to_pr(owner: str, repo: str, pr_number: int, comments_str: List[dict]):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    success_all = True
    for idx, comment in enumerate(comments_str, start=1):
        try:
            payload = {"body":comment["body"]}
            response = requests.post(url, headers=headers_github, json=payload, timeout=30)
            #add post in Files changed.
            if response.status_code == 201:
                logger.info(f"Posted comment #{idx} to PR #{pr_number}")
            else:
                logger.error(f"Failed comment #{idx}: {response.status_code} - {response.text}")
                success_all = False
        except Exception as e:
            logger.error(f"⚠️ Error posting comment #{idx}: {str(e)}")
            success_all = False
    return success_all

def should_review_file(filename: str, patch: str) -> bool:
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

@app.post("/")
async def github_webhook(request: Request):
    global last_patches, last_full_comment
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
        pr_number = pr_data["number"]
        pr_title = pr_data.get("title", "")

        if action in ["closed", "locked", "unlocked"]:
            return {"message": f"Action {action} ignored"}
        files_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(files_url, headers=headers)
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Failed to fetch PR files: {response.text}")

        files = response.json()
        code_files_count = 0
        last_patches = []  # reset
        all_reviews=[]
       

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
                
                all_reviews.append(query_openrouter_focused(filename, patch, status))
                ''' language = get_file_language(filename)
                file_review = f"""## `{filename}` ({language})  **Status**: {status.title()} • **Changes**: +{additions}/-{deletions} lines  {review_content}  ---"""
                all_reviews.append(file_review) '''
            except Exception as e:
                error_review = f"""## `{filename}`  **Status**: {status} (+{additions}/-{deletions} lines)  **Review Error**: {str(e)}  ---"""
                all_reviews.append(error_review)

        if all_reviews:
            comment_header = f"""# AI Code Review  **{pr_title}** (PR #{pr_number})  **Summary**: Analyzed {code_files_count} file(s) with targeted AI review.  """
            for reviews in all_reviews:
                reviews=reviews.replace('\n','')
                full_comment = json.loads(reviews)
                logger.info(type(full_comment))
                if len(full_comment) > 60000:
                    full_comment = full_comment[:60000] + "\n\n*⚠️ Truncated due to GitHub comment size limit*"
                last_full_comment = full_comment
                ''' success = post_comment_to_pr(owner, repo, pr_number, full_comment) '''
                success=post_review_comments(owner, repo, pr_number, full_comment)
        else:
            success = False

        return {
            "message": "Webhook processed successfully",
            "pr_number": pr_number,
            "files_total": len(files),
            "files_reviewed": code_files_count,
            "repository": f"{owner}/{repo}",
            "action": action,
            "review_posted": bool(all_reviews),
            "success": success
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")




@app.get("/")
async def root():
    return {"message": "Enhanced Code Review API is running with OpenRouter", "version": "2.1"}
