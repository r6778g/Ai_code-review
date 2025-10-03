from fastapi import FastAPI, HTTPException
import os
import numpy as np
import requests
import logging
import re
import json
from dotenv import load_dotenv
from typing import Dict, List
import uuid

# Initialize client with persistent storage

# ---------------- Setup ----------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY environment variable is required")

API_URL = "https://openrouter.ai/api/v1/chat/completions"
headers_openrouter = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
}



# ---------------- Patch Parsing -------------------
def get_file_language(filename: str) -> str:
    ext_map = {
        '.py': 'Python', '.js': 'JavaScript', '.jsx': 'React/JSX',
        '.ts': 'TypeScript', '.tsx': 'React/TypeScript', '.java': 'Java',
        '.cpp': 'C++', '.c': 'C', '.go': 'Go', '.rs': 'Rust',
        '.php': 'PHP', '.rb': 'Ruby', '.css': 'CSS', '.scss': 'SCSS',
        '.swift': 'Swift', '.kt': 'Kotlin', '.sql': 'SQL', '.html': 'HTML',
        '.vue': 'Vue.js', '.sh': 'Shell Script', '.yaml': 'YAML',
        '.yml': 'YAML', '.json': 'JSON'
    }
    for ext, lang in ext_map.items():
        if filename.lower().endswith(ext):
            return lang
    return 'Unknown'

def parse_patch_changes(patch: str) -> Dict:
    lines = patch.split('\n')
    changes = {'added_lines': [], 'removed_lines': [], 'context_lines': [], 'line_numbers': []}
    current_line_num = 0
    for line in lines:
        if line.startswith('@@'):
            match = re.search(r'@@ -(\d+),?\d* \+(\d+),?\d* @@', line)
            if match:
                current_line_num = int(match.group(2))
            continue
        if line.startswith('+') and not line.startswith('+++'):
            changes['added_lines'].append((current_line_num, line[1:]))
            changes['line_numbers'].append(current_line_num)
            current_line_num += 1
        elif line.startswith('-'):
            changes['removed_lines'].append(line[1:])
        else:
            if line and not line.startswith('\\'):
                changes['context_lines'].append((current_line_num, line[1:] if line.startswith(' ') else line))
                current_line_num += 1
    return changes

def create_focused_prompt(filename: str, file_status: str, changes: Dict, language: str) -> str:
    """Create a focused prompt based on the specific changes made"""
    
    added_count = len(changes['added_lines'])
    removed_count = len(changes['removed_lines'])
    
    # Create context about what changed
    change_summary = []
    if file_status == "added":
        change_summary.append("This is a new file")
    elif file_status == "removed":
        change_summary.append("This file is being deleted")
    elif file_status == "modified":
        change_summary.append(f"Modified with {added_count} additions and {removed_count} deletions")
    elif file_status == "renamed":
        change_summary.append("File was renamed and potentially modified")

    # Sample some key added lines for context
    key_additions = []
    if changes['added_lines']:
        # Take first few and last few additions to get a sense of the change
        sample_lines = changes['added_lines'][:3] + (changes['added_lines'][-2:] if len(changes['added_lines']) > 3 else [])
        for line_num, content in sample_lines:
            if content.strip():  # Only non-empty lines
                key_additions.append(f"Line {line_num}: {content.strip()}")

    prompt = """You are a senior software engineer and code reviewer.
Your mission: review code clearly and effectively, focusing on standards, readability, and maintainability.

 ### Output Format
Return a JSON-like list of review objects, structured like this:
[
  {
    "file": "filename",
    "start_line": line number,
    "end_line": line number,
    "body": "Snippet:\n```language\ncode here\n```\n\nIssue: <short description>\nProblem: <why it matters>\nSolution:\n```diff\n- old code\n+ new code\n```\nRationale: <why this improves the code>"
  }
]

### Review Focus
Syntax & Structure
Invalid code: Missing colons, semicolons, mismatched brackets/parentheses
Proper indentation: Consistent use of spaces/tabs (follow language standards)
Block structure: Proper opening/closing of functions, classes, loops
Line length: Stay within recommended limits (80-120 characters)

Naming Conventions
Avoid shadowing: Don't override built-in functions/variables
Follow style guides: PEP 8 (Python), Google Style Guide, etc.
Meaningful names: Variables, functions, classes should be self-documenting
Consistent naming: snake_case, camelCase, PascalCase as per language conventions
Avoid abbreviations: Use user_count instead of usr_cnt

Readability & Formatting
Consistent formatting: Spacing around operators, after commas
Logical grouping: Related code blocks together
Clear variable names: Descriptive and contextual
Avoid magic numbers: Use named constants
Whitespace usage: Proper separation of logical sections

Error Handling & Edge Cases
Prevent crashes: Handle exceptions gracefully
Input validation: Check for null, empty, out-of-bounds values
Boundary conditions: Test minimum/maximum values
Resource cleanup: Proper file/connection closing
Graceful degradation: Fallback behavior when things fail

Dependencies & Imports
Remove unused imports: Clean up dead code
Pin dependency versions: Avoid "latest" in production
Minimize dependencies: Only include what's necessary
Security updates: Keep dependencies current for vulnerabilities
Import organization: Group standard, third-party, and local imports

Documentation & Comments

Docstrings: Function/class purpose, parameters, return values
Inline comments: Explain complex logic, not obvious code
Type hints: Parameter and return types (Python, TypeScript)
API documentation: Clear interface contracts
README files: Setup, usage, and contribution guidelines

Maintainability & Design
DRY principle: Don't Repeat Yourself - extract common code
Single responsibility: Functions/classes should do one thing well
Simplify complex logic: Break down large functions
Consistent patterns: Use established project conventions
Modular design: Loose coupling, high cohesion

Security & Performance
SQL injection: Use parameterized queries
XSS prevention: Sanitize user inputs
Authentication: Proper user verification
Data exposure: Don't log sensitive information
Performance bottlenecks: Identify inefficient algorithms/queries
Memory leaks: Proper resource management

Testing & Quality

Test coverage: Unit tests for critical functionality
Test quality: Edge cases, error conditions
Test maintainability: Clear, isolated test cases
Integration testing: Component interaction verification
Code metrics: Cyclomatic complexity, maintainability index

### Guidelines
Be specific: include filename and line numbers
Be constructive: explain issues in a helpful tone
Be concise: no over-explaining
Show diff format fixes whenever possible
Ensure start_line and end_line are valid PR diff lines


Example Review Output (JSON Style)
[
  {
    "file": "user_service.py",
    "start_line": 45,
    "end_line": 48,
    "body": "Snippet:\n```python\nexample\ndef sum(a, b):\n    return a + b\n```\n\nIssue: Built-in Shadowing\nProblem: `sum` is a Python built-in function. Overriding it can cause confusion and bugs.\nSolution:\n```diff\n- def sum(a, b):\n+ def add(a, b):\n```\nRationale: Using `add` avoids conflicts with the built-in.\n"
  },
  {
    "file": "user_service.py",
    "start_line": 45,
    "end_line": 48,
    "body": "Issue: Style & Readability\nProblem: One-line function definition reduces readability.\nSolution:\n```python\ndef add(a: float, b: float) -> float:\n    \"\"\"Return the sum of two numbers.\"\"\"\n    return a + b\n```\nRationale: Improves readability, follows PEP8, adds type hints and docstring."
  }
]
"""

    return prompt


# ---------------- API Call ------------------
def query_openrouter_focused(filename: str, patch: str, file_status: str) -> str:
    try:
        language = get_file_language(filename)
        changes = parse_patch_changes(patch)
        
        if not changes['added_lines'] and not changes['removed_lines']:
            return "No significant changes to review in this file."
        focused_prompt = create_focused_prompt(filename, file_status, changes, language)

       
        payload = {
            "model": "x-ai/grok-4-fast:free",
            "messages": [
                {"role": "system", "content": f"You are an expert code reviewer specializing in {language}."},
                {"role": "user", "content": f"{focused_prompt}\n\nPatch:\n```diff\n{patch}\n"}
            ],
            "temperature": 0, "top_p": 1.0, "top_k": 0, "repetition_penalty": 1
        }
        
        response = requests.post(API_URL, headers=headers_openrouter, json=payload, timeout=60)
        logger.info(response)
        if response.status_code != 200:
            logger.error(f"OpenRouter API error {response.status_code} - {response.text}")
            return "**Review Error**: API error."
        response_data = response.json()
        if 'choices' in response_data and response_data['choices']:
            return response_data['choices'][0]['message']['content']
        else:
            return "**Review Error**: No valid response."
    except Exception as e:
        logger.error(f"Error in review for {filename}: {e}")
        return f"**Review Error**: {str(e)}"
