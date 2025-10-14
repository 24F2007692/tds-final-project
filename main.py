# ==============================================================================
# TDS AI: LLM Code Deployment Project
# ==============================================================================
import os
import time
import base64
import traceback
import re
from typing import List, Optional, Dict, Tuple

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl, Field

from openai import OpenAI
from dotenv import load_dotenv
from github import Github, GithubException, UnknownObjectException
import requests

# Load environment variables
load_dotenv()

class Config:
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-4o")
    MY_SECRET: str = os.getenv("MY_SECRET", "my-super-secret-123")
    AIPIPE_TOKEN: str = os.getenv("AIPIPE_TOKEN")
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN")
    GITHUB_USERNAME: str = os.getenv("GITHUB_USERNAME")
    DEPLOYMENT_TIMEOUT: int = int(os.getenv("DEPLOYMENT_TIMEOUT", 180))
    MAX_ATTACHMENT_SIZE: int = 10 * 1024 * 1024

config = Config()

# Pydantic models
class Attachment(BaseModel):
    name: str
    url: str

class BuildRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    evaluation_url: HttpUrl
    attachments: Optional[List[Attachment]] = Field(default=None)
    checks: Optional[List[str]] = Field(default=None)

app = FastAPI()

@app.on_event("startup")
def startup_event():
    print("--- Performing startup validation of environment variables ---")
    if not config.AIPIPE_TOKEN: print("CRITICAL WARNING: AIPIPE_TOKEN is not set.")
    if not config.GITHUB_TOKEN: print("CRITICAL WARNING: GITHUB_TOKEN is not set.")
    if not config.GITHUB_USERNAME: print("CRITICAL WARNING: GITHUB_USERNAME is not set.")
    print("--- Startup validation complete. ---")

def sanitize_filename(filename: str) -> str:
    sanitized = filename.replace("..", "")
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', sanitized)

# === LLM / attachment handling ===
def generate_code_from_brief(request_data: BuildRequest, existing_code: str = None) -> Tuple[str, Dict[str, bytes]]:
    if not config.AIPIPE_TOKEN:
        raise HTTPException(status_code=503, detail="Server configuration error: AIPIPE_TOKEN is not set.")
    client = OpenAI(base_url="https://aipipe.org/openrouter/v1", api_key=config.AIPIPE_TOKEN)

    attachments_content = ""
    binary_files_to_commit: Dict[str, bytes] = {}
    if request_data.attachments:
        for attachment in request_data.attachments:
            try:
                header, encoded = attachment.url.split(",", 1)
                mime_type = header.split(';')[0].split(':')[1]
                decoded_bytes = base64.b64decode(encoded)

                if len(decoded_bytes) > config.MAX_ATTACHMENT_SIZE:
                    print(f"Attachment {attachment.name} too large. Skipping.")
                    continue

                if mime_type.startswith("text/") or mime_type in ("application/json", "application/javascript"):
                    try:
                        attachments_content += f"\n\n--- Attachment: `{attachment.name}` ---\n```\n{decoded_bytes.decode('utf-8')}\n```"
                    except Exception:
                        attachments_content += f"\n\n--- Attachment: `{attachment.name}` (text but decode failed) ---"
                else:
                    safe_filename = sanitize_filename(attachment.name)
                    binary_files_to_commit[safe_filename] = decoded_bytes
                    attachments_content += f"\n\n--- Attachment: `{safe_filename}` (Binary file saved to repo as `{safe_filename}.b64`) ---"
            except Exception as e:
                print(f"Warning: Could not process attachment '{attachment.name}'. Error: {e}")

    technical_requirements = ""
    if request_data.checks:
        technical_requirements += "\n\n**Technical Implementation Requirements (CRITICAL):**\n"
        for i, check in enumerate(request_data.checks):
            technical_requirements += f"{i+1}. The generated webpage MUST pass this JavaScript evaluation: `{check}`\n"

    action = "modify an existing HTML file" if existing_code else "create a new, self-contained `index.html` file"
    existing_code_section = f"**EXISTING CODE:**\n```html\n{existing_code}\n```" if existing_code else ""

    prompt_template = """
    You are an elite software engineer. Your task is to {action}.
    Analyze the user's brief, attachments, and technical requirements.
    Your output must be ONLY the complete, final, raw HTML code, starting with `<!DOCTYPE html>`.
    Do not include any explanations, comments, or markdown. For external libraries, use public CDNs.
    All technical requirements are mandatory.

    {existing_code_section}
    **USER'S BRIEF:** "{brief}"
    {attachments}
    {tech_reqs}
    """

    final_prompt = prompt_template.format(
        action=action, existing_code_section=existing_code_section,
        brief=request_data.brief, attachments=attachments_content, tech_reqs=technical_requirements
    )

    try:
        completion = client.chat.completions.create(
            model=config.LLM_MODEL, messages=[{"role": "user", "content": final_prompt}],
            temperature=0.1, timeout=120.0,
        )
        generated_code = completion.choices[0].message.content.strip()

        if generated_code.startswith("```html"):
            generated_code = generated_code.split("```html", 1)[1].rsplit("```", 1)[0]

        return generated_code.strip(), binary_files_to_commit
    except Exception as e:
        raise HTTPException(status_code=504, detail=f"LLM API call failed or timed out: {e}")

# === GitHub helpers ===
def get_existing_file(repo, file_path):
    try:
        return repo.get_contents(file_path, ref=repo.default_branch)
    except UnknownObjectException:
        return None

def enable_github_pages(repo):
    """
    Try to enable GitHub Pages programmatically.
    Prefer PyGithub method when available. Fallback to REST API.
    """
    owner = repo.owner.login
    repo_name = repo.name
    branch = repo.default_branch
    print(f"Attempting to enable GitHub Pages for {owner}/{repo_name} on branch {branch} (root).")

    # Try PyGithub high-level API first
    try:
        create_pages = getattr(repo, "create_pages_site", None)
        if callable(create_pages):
            try:
                repo.create_pages_site(source_type="branch", source_branch=branch, source_path="/")
                print("GitHub Pages enabled via PyGithub.")
                return True
            except GithubException as e:
                print(f"PyGithub create_pages_site failed: {e.status} {e.data if hasattr(e,'data') else e}")
        else:
            print("PyGithub create_pages_site not available in this PyGithub version.")
    except Exception as e:
        print(f"PyGithub pages attempt raised: {e}")

    # Fallback: use GitHub REST API
    try:
        url = f"https://api.github.com/repos/{owner}/{repo_name}/pages"
        headers = {
            "Authorization": f"token {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        payload = {"source": {"branch": branch, "path": "/"}}
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        if response.status_code in (201, 204):
            print("GitHub Pages enabled via REST API.")
            return True
        if response.status_code == 422 and "already configured" in response.text.lower():
            print("GitHub Pages already configured.")
            return True
        print(f"REST API Pages enable returned {response.status_code}: {response.text}")
    except requests.RequestException as e:
        print(f"REST API Pages enable attempt failed: {e}")

    print("Could not enable GitHub Pages programmatically. Manual enabling may be required.")
    return False

def verify_deployment(pages_url: str, timeout: int) -> bool:
    print(f"Verifying deployment at {pages_url}. Will wait up to {timeout} seconds.")
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(pages_url, timeout=10)
            if response.status_code == 200:
                print("Deployment verified successfully (HTTP 200).")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(5)
    print("Error: Deployment verification timed out.")
    return False

def create_or_update_file(repo, file_path: str, commit_message: str, content: str):
    """
    Create or update a text file. content must be a str.
    """
    file = get_existing_file(repo, file_path)
    if file:
        existing = file.decoded_content.decode("utf-8")
        if existing != content:
            repo.update_file(file.path, commit_message, content, file.sha, branch=repo.default_branch)
            print(f"Updated file: {file_path}")
        else:
            print(f"No change for file: {file_path}")
    else:
        repo.create_file(file_path, commit_message, content, branch=repo.default_branch)
        print(f"Created file: {file_path}")

def create_and_deploy(request_data: BuildRequest, html_content: str, binary_files: dict):
    g = Github(config.GITHUB_TOKEN)
    user = g.get_user()
    repo_name = request_data.task

    try:
        repo = user.create_repo(repo_name, auto_init=True)
        print(f"Created repository: {repo.full_name}")
    except GithubException as e:
        if getattr(e, "status", None) == 422:
            repo = g.get_repo(f"{user.login}/{repo_name}")
            print(f"Using existing repository: {repo.full_name}")
        else:
            raise e

    # README and LICENSE content
    readme_content = f"""# {repo_name.replace('-', ' ').title()}

## Project Summary

This repository hosts a web application automatically generated by an LLM-powered code deployment agent.

**Brief:** "{request_data.brief}"

## Live Application

The deployed application can be viewed at:
https://{config.GITHUB_USERNAME}.github.io/{repo_name}/
"""
    license_text = """MIT License
Copyright (c) 2025 TDS Student
Permission is hereby granted...
"""

    # Ensure index.html and support files are created/updated
    create_or_update_file(repo, "index.html", "Create/Update application code", html_content)
    create_or_update_file(repo, "README.md", "Create/Update README", readme_content)
    create_or_update_file(repo, "LICENSE", "Create/Update LICENSE", license_text)

    # Save binary attachments as base64 text files: filename.b64
    for filename, content_bytes in binary_files.items():
        safe_name = sanitize_filename(filename)
        b64_text = base64.b64encode(content_bytes).decode("utf-8")
        b64_path = f"{safe_name}.b64"
        create_or_update_file(repo, b64_path, f"Add/Update binary attachment {safe_name}.b64", b64_text)

    # Attempt to enable GitHub Pages
    pages_enabled = enable_github_pages(repo)
    if not pages_enabled:
        print("Warning: GitHub Pages may not be enabled. Check repository settings manually.")

    commit_sha = repo.get_branch(repo.default_branch).commit.sha
    pages_url = f"https://{config.GITHUB_USERNAME}.github.io/{repo_name}/"

    # Wait and verify
    verified = verify_deployment(pages_url, config.DEPLOYMENT_TIMEOUT)
    if not verified:
        print(f"Warning: Deployment not verified at {pages_url} within timeout.")

    return repo.html_url, commit_sha, pages_url

def revise_and_deploy(request_data: BuildRequest, new_html_content: str, binary_files: dict):
    g = Github(config.GITHUB_TOKEN)
    user = g.get_user()
    repo_name = request_data.task

    try:
        repo = g.get_repo(f"{user.login}/{repo_name}")
    except UnknownObjectException:
        raise ValueError(f"Repository {repo_name} not found for revision.")

    readme_file = get_existing_file(repo, "README.md")
    existing_readme = readme_file.decoded_content.decode("utf-8") if readme_file else ""
    new_readme_content = f"{existing_readme}\n\n### Round {request_data.round} Update\n\n> {request_data.brief}"

    create_or_update_file(repo, "index.html", f"Update webpage for Round {request_data.round}", new_html_content)
    create_or_update_file(repo, "README.md", f"Update README for Round {request_data.round}", new_readme_content)

    for filename, content in binary_files.items():
        safe_name = sanitize_filename(filename)
        b64_text = base64.b64encode(content).decode("utf-8")
        b64_path = f"{safe_name}.b64"
        create_or_update_file(repo, b64_path, f"Add/Update binary attachment {safe_name}.b64", b64_text)

    # Ensure pages enabled
    enable_github_pages(repo)

    commit_sha = repo.get_branch(repo.default_branch).commit.sha
    pages_url = f"https://{config.GITHUB_USERNAME}.github.io/{repo.name}/"

    verify_deployment(pages_url, config.DEPLOYMENT_TIMEOUT)
    return repo.html_url, commit_sha, pages_url

# === Notification ===
def notify_evaluation_server(payload: dict):
    url = str(payload.pop("evaluation_url"))
    for i, delay in enumerate([1, 2, 4, 8]):
        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            print(f"Successfully notified evaluation server on attempt {i+1}.")
            return True
        except requests.exceptions.RequestException as e:
            print(f"Notification attempt {i+1} failed: {e}. Retrying in {delay}s...")
            if i < 3: time.sleep(delay)
    print("All notification attempts failed.")
    return False

# === Background task ===
def run_build_and_deploy_task(request_data: BuildRequest):
    print(f"Starting background task for '{request_data.task}', round {request_data.round}.")
    try:
        if request_data.round == 1:
            html_content, binary_files = generate_code_from_brief(request_data)
            repo_url, commit_sha, pages_url = create_and_deploy(request_data, html_content, binary_files)
        else:
            g = Github(config.GITHUB_TOKEN)
            user = g.get_user()
            repo = g.get_repo(f"{user.login}/{request_data.task}")

            file_content = get_existing_file(repo, "index.html")
            if not file_content: raise ValueError("Could not retrieve existing code for revision.")

            existing_code = file_content.decoded_content.decode("utf-8")
            modified_html_code, binary_files = generate_code_from_brief(request_data, existing_code)
            repo_url, commit_sha, pages_url = revise_and_deploy(request_data, modified_html_code, binary_files)

        notification_payload = {
            "email": request_data.email, "task": request_data.task,
            "round": request_data.round, "nonce": request_data.nonce,
            "repo_url": repo_url, "commit_sha": commit_sha,
            "pages_url": pages_url, "evaluation_url": str(request_data.evaluation_url)
        }

        notified = notify_evaluation_server(notification_payload)
        if not notified:
            print(f"CRITICAL (background): Build for '{request_data.task}' succeeded, but FAILED to notify server.")

        print(f"Background task for '{request_data.task}' completed successfully!")

    except Exception as e:
        print(f"FATAL ERROR in background task for '{request_data.task}': {e}")
        traceback.print_exc()

# === API endpoint ===
@app.post("/api/build", status_code=202)
def handle_build_request(request_data: BuildRequest, background_tasks: BackgroundTasks):
    if request_data.secret != config.MY_SECRET:
        raise HTTPException(status_code=403, detail="Authentication failed: Invalid secret.")

    if not all([config.AIPIPE_TOKEN, config.GITHUB_TOKEN, config.GITHUB_USERNAME]):
        raise HTTPException(status_code=503, detail="Server is not fully configured. Missing environment variables.")

    request_data.task = sanitize_filename(request_data.task)

    # Use background task to run the heavy process
    background_tasks.add_task(run_build_and_deploy_task, request_data)

    return {"status": "accepted", "message": "The build and deploy process has been started in the background."}

