# main.py
import os
import requests
import time
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from github import Github, GithubException
import git
from typing import Optional

load_dotenv()

class TaskRequest(BaseModel):
    email: str
    brief: str
    task: str
    round: int
    evaluation_url : str
    nonce: str

app = FastAPI()

# --- GITHUB AUTOMATION FUNCTIONS ---

def create_github_repo(task_name: str):
    try:
        token = os.getenv("GITHUB_TOKEN")
        g = Github(token)
        user = g.get_user()
        
        try:
            repo = user.get_repo(task_name)
            print(f"Repo '{task_name}' pehle se hai. Usko use kar rahe hain.")
            return repo
        except GithubException:
            print(f"Naya repo bana rahe hain: {task_name}")
            repo = user.create_repo(task_name, private=False)
            return repo
            
    except Exception as e:
        print(f"GitHub repo banane mein error: {e}")
        return None

def commit_and_push_files(repo_url: str, local_dir: str, task_name: str):
    try:
        try:
            repo = git.Repo(local_dir)
        except git.exc.InvalidGitRepositoryError:
            repo = git.Repo.init(local_dir)

        repo.index.add('*')
        
        if repo.is_dirty(index=True, working_tree=False):
            repo.index.commit(f"Commit for task: {task_name}")
        
        if 'origin' in repo.remotes:
            origin = repo.remotes.origin
            origin.set_url(repo_url)
        else:
            origin = repo.create_remote('origin', repo_url)
            
        print(f"Pushing files to the '{repo.head.ref.name}' branch...")
        origin.push(refspec=f'{repo.head.ref.name}:main', force=True)
        
        print("Files successfully pushed to GitHub.")
        commit_sha = repo.head.commit.hexsha
        return commit_sha

    except Exception as e:
        print("!!! Git push mein error aaya !!!")
        print(f"Error Details: {e}")
        return None

def enable_github_pages(github_repo_object):
    """GitHub Pages ko enable karta hai using the repo's default branch."""
    try:
        token = os.getenv("GITHUB_TOKEN")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
        
        # Automatically get the default branch ('main' or 'master')
        default_branch = github_repo_object.default_branch
        print(f"Enabling Pages on default branch: '{default_branch}'")
        
        data = {"source": {"branch": default_branch, "path": "/"}}
        
        url = f"https://api.github.com/repos/{github_repo_object.full_name}/pages"
        response = requests.post(url, headers=headers, json=data)
        
        response.raise_for_status()
        
        pages_url = response.json().get("html_url")
        print(f"GitHub Pages enabled at: {pages_url}")
        return pages_url

    except Exception as e:
        print(f"GitHub Pages enable karne mein error: {e}")
        return None

# --- AI CODE GENERATION FUNCTION ---

def generate_code_from_brief(brief: str):
    print("Generating code for brief:", brief)
    api_key = os.getenv("AIPIPE_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {
                "role": "system",
                # --- NEW, STRICTER PROMPT ---
                "content": """
You are a silent code generation machine. Your ONLY task is to create a single, complete, and valid HTML file based on the user's request.

**RULES:**
1. **ONE FILE ONLY:** All CSS must be inside `<style>` tags and all JavaScript must be inside `<script>` tags within the single HTML file.
2. **NO CONVERSATION:** Do NOT provide any explanations, introductions, or summaries.
3. **NO MARKDOWN:** Do not wrap the code in markdown backticks (```).
4. **RAW CODE ONLY:** Your entire response must start directly with `<!DOCTYPE html>` and end with `</html>`.
""".strip()
                # -----------------------------
            },
            {
                "role": "user", 
                "content": f"Create an application for this brief: {brief}"
            }
        ],
    }
    
    try:
        response = requests.post(
            "https://aipipe.org/openrouter/v1/chat/completions",
            headers=headers, json=payload, timeout=60
        )
        response.raise_for_status()
        # ... (code cleaning logic is the same) ...
        generated_code = response.json()["choices"][0]["message"]["content"]
        clean_code = generated_code.strip()
        if clean_code.startswith("```html"): clean_code = clean_code[7:]
        if clean_code.startswith("```"): clean_code = clean_code[3:]
        if clean_code.endswith("```"): clean_code = clean_code[:-3]
        return clean_code.strip()
    except requests.exceptions.RequestException as e:
        return f"<h1>Error: Could not connect to API.</h1><pre>{e}</pre>"

# --- MAIN ENDPOINT ---
# In main.py, replace the main endpoint function

# In main.py, replace the entire @app.post("/") function

@app.post("/")
def recieve_request(request_data: TaskRequest):
    print(f"--- Task '{request_data.task}' (Round {request_data.round}) Shuru Hua ---")

    if request_data.round == 1:
        # Round 1: Naya App Banayein (Build)
        # (Yeh code bilkul same rahega)
        print("Executing Round 1: Build")
        local_project_path = os.path.join(os.getcwd(), request_data.task)
        os.makedirs(local_project_path, exist_ok=True)
        generated_html = generate_code_from_brief(request_data.brief)
        with open(os.path.join(local_project_path, "index.html"), "w", encoding='utf-8') as f: f.write(generated_html)
        with open(os.path.join(local_project_path, "README.md"), "w") as f: f.write(f"# {request_data.task}\n\n{request_data.brief}")
        with open(os.path.join(local_project_path, "LICENSE"), "w") as f: f.write("MIT License")
        repo = create_github_repo(request_data.task)
        if not repo: return {"error": "Failed to create GitHub repo."}
        clone_url = repo.clone_url
        token = os.getenv('GITHUB_TOKEN')
        repo_url_with_token = clone_url.replace("https://", f"https://{token}@")
        commit_sha = commit_and_push_files(repo_url_with_token, local_project_path, request_data.task)
        if not commit_sha: return {"error": "Failed to push files to GitHub."}
        pages_url = enable_github_pages(repo)
        if not pages_url:
            print("Waiting 10 seconds for branch to become available...")
            time.sleep(10)
            pages_url = enable_github_pages(repo)
        print(f"--- Task '{request_data.task}' (Round 1) Pura Hua ---")

        eval_payload = {
            "email": request_data.email,
            "task": request_data.task,
            "round": request_data.round,
            "nonce": request_data.nonce,
            "repo_url": repo.html_url,
            "commit_sha": commit_sha,
            "pages_url": pages_url,
        }
        report_to_evaluation_server(request_data.evaluation_url, eval_payload)

        return {
            "status": "Round 1 Deployment successful!",
            "repo_url": repo.html_url,
            "pages_url": pages_url,
            "commit_sha": commit_sha
        }
    


    elif request_data.round > 1:
        print(f"Executing Round {request_data.round}: Revise")
        
        # (Baaki ka poora revise logic bilkul same rahega)
        token = os.getenv('GITHUB_TOKEN')
        if not token: return {"error": "GITHUB_TOKEN not found."}
        local_project_path = os.path.join(os.getcwd(), request_data.task)
        try:
            g = Github(token)
            github_username = g.get_user().login
            repo_url_with_token = f"https://{token}@github.com/{github_username}/{request_data.task}.git"
            if os.path.exists(local_project_path):
                print("Pulling latest changes.")
                repo = git.Repo(local_project_path)
                repo.remotes.origin.set_url(repo_url_with_token)
                repo.remotes.origin.pull()
            else:
                print("Cloning repo...")
                git.Repo.clone_from(repo_url_with_token, local_project_path)
        except Exception as e:
            return {"error": f"Failed to clone or pull repo: {e}"}
        original_code_path = os.path.join(local_project_path, "index.html")
        try:
            with open(original_code_path, "r", encoding='utf-8') as f: original_code = f.read()
        except FileNotFoundError:
            return {"error": "index.html not found."}
        revised_html = generate_revised_code(request_data.brief, original_code)
        with open(original_code_path, "w", encoding='utf-8') as f: f.write(revised_html)
        with open(os.path.join(local_project_path, "README.md"), "a") as f: f.write(f"\n\n## Round {request_data.round}\n\n{request_data.brief}")
        commit_sha = commit_and_push_files(repo_url_with_token, local_project_path, f"{request_data.task} - Round {request_data.round}")
        if not commit_sha: return {"error": "Failed to push Round {request_data.round} changes."}
        
        print(f"--- Task '{request_data.task}' (Round {request_data.round}) Pura Hua ---")

        eval_payload = {
            "email": request_data.email,
            "task": request_data.task,
            "round": request_data.round,
            "nonce": request_data.nonce,
            # We don't need to send all details again, just the essentials
            "commit_sha": commit_sha,
        }
        report_to_evaluation_server(request_data.evaluation_url, eval_payload)


        return {
            "status": f"Round {request_data.round} Revision successful!",
            "commit_sha": commit_sha
        }
    
    else:
        return {"error": f"Invalid round number: {request_data.round}"}
    

def generate_revised_code(brief:str, original_code: str):
    system_prompt = """
You are a code modification expert. Your task is to modify the given HTML code based on the user's instructions.
Output ONLY the complete, new, raw HTML code. Do not include explanations, markdown, or any other text.
Ensure all CSS is in `<style>` tags and all JS is in `<script>` tags within the single HTML file.
Your response must start with `<!DOCTYPE html>` and end with `</html>`.
""".strip()
    
    user_prompt = f"""
Here is the original HTML code that needs to be modified:
---
{original_code}
---

Now, please apply the following change: "{brief}"
""".strip()
    
    api_key = os.getenv("AIPIPE_API_KEY")
    headers = {'Authorization': f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
    }

    try:
        response = requests.post(
            "https://aipipe.org/openrouter/v1/chat/completions",
            headers=headers,json=payload,timeout=90
        )
        response.raise_for_status()

        generated_code = response.json()['choices'][0]['message']['content']


        clean_code = generated_code.strip()
        if clean_code.startswith("```html"): clean_code = clean_code[7:]
        if clean_code.startswith("```"): clean_code = clean_code[3:]
        if clean_code.endswith("```"): clean_code = clean_code[:-3]

        return clean_code.strip()
    
    except requests.exceptions.RequestException as e:
        print(e)

    
def report_to_evaluation_server(eval_url: str,payload: dict):
    try:
        response = requests.post(eval_url,json=payload,timeout=30)


        if response.status_code == 200:
            return True
        
        else:
            print(f'Error Status {response.status_code}')
            return False
    
    except:
        return False
    

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)